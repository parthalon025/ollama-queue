"""DLQ auto-reschedule: sweeps dead-letter entries into optimal time slots.

Triggered event-driven (after job completion) or periodically (daemon poll).
Skips chronic failures (reschedule count >= threshold) and permanent failures.
Uses RuntimeEstimator for duration prediction and slot_scoring for placement.
"""

import json
import logging
import threading
import time

from ollama_queue.scheduler import _estimate_model_vram
from ollama_queue.slot_scoring import find_fitting_slot
from ollama_queue.system_snapshot import classify_failure

logger = logging.getLogger(__name__)


class DLQScheduler:
    """Sweeps DLQ entries and auto-reschedules into optimal time slots."""

    def __init__(self, db, estimator, load_map_fn):
        """
        Args:
            db: Database instance.
            estimator: RuntimeEstimator instance.
            load_map_fn: Callable returning list[dict] (the load map from scheduler).
        """
        self.db = db
        self.estimator = estimator
        self.load_map_fn = load_map_fn
        self._sweep_lock = threading.Lock()
        self._last_sweep = 0.0

    def on_job_completed(self, job_id: int) -> None:
        """Event-driven trigger — called after any job completes."""
        unscheduled = self.db.list_dlq(unscheduled_only=True)
        if unscheduled:
            self._sweep(unscheduled)

    def periodic_sweep(self) -> None:
        """Fallback trigger — called from daemon poll loop."""
        unscheduled = self.db.list_dlq(unscheduled_only=True)
        if unscheduled:
            self._sweep(unscheduled)

    def _sweep(self, entries: list[dict]) -> list[dict]:
        """Core sweep logic. Returns list of rescheduled entries."""
        if not self._sweep_lock.acquire(blocking=False):
            return []  # Another sweep in progress
        try:
            return self._do_sweep(entries)
        finally:
            self._sweep_lock.release()

    def _do_sweep(self, entries: list[dict]) -> list[dict]:
        """Process DLQ entries by priority, find slots, create new jobs."""
        if not self.db.get_setting("dlq.auto_reschedule"):
            return []

        rescheduled = []
        # Sort by priority descending (higher priority = try first)
        sorted_entries = sorted(entries, key=lambda e: e.get("priority", 0), reverse=True)

        # Check chronic failure threshold
        chronic_threshold = self.db.get_setting("dlq.chronic_failure_threshold") or 5

        load_map = self.load_map_fn()

        for entry in sorted_entries:
            # Skip chronic failures
            if (entry.get("auto_reschedule_count") or 0) >= chronic_threshold:
                logger.info(
                    "DLQ #%s: skipping chronic failure (count=%s, threshold=%s)",
                    entry.get("id"),
                    entry.get("auto_reschedule_count"),
                    chronic_threshold,
                )
                continue

            # Classify failure
            failure_cat = classify_failure(entry.get("failure_reason", ""))

            # Skip permanent failures
            if failure_cat == "permanent":
                logger.info(
                    "DLQ #%s: skipping permanent failure (%s)",
                    entry.get("id"),
                    entry.get("failure_reason", "")[:80],
                )
                continue

            # Estimate runtime
            est = self.estimator.estimate(
                entry.get("model", ""),
                entry.get("command", ""),
                entry.get("resource_profile", "ollama"),
            )

            # Find fitting slot
            model = entry.get("model", "")
            job_vram = _estimate_model_vram(model)
            estimated_slots = max(1, int(est.total_upper / 1800) + 1)  # 30-min slots
            slot = find_fitting_slot(
                load_map,
                job_vram_needed_gb=job_vram,
                total_vram_gb=24.0,  # TODO: get from health monitor
                estimated_slots=estimated_slots,
                failure_category=failure_cat,
                job_model=model,
            )

            if slot is None:
                logger.debug("DLQ #%s: no fitting slot found", entry.get("id"))
                continue

            # Create new job
            new_job_id = self.db.submit_job(
                command=entry["command"],
                model=entry.get("model", ""),
                priority=entry.get("priority", 0),
                timeout=entry.get("timeout", 600),
                source=entry.get("source", "dlq-reschedule"),
                tag=entry.get("tag"),
                resource_profile=entry.get("resource_profile", "ollama"),
            )

            # Build reasoning
            reasoning = json.dumps(
                {
                    "failure_category": failure_cat,
                    "estimate": {
                        "mean": est.total_mean,
                        "upper": est.total_upper,
                        "confidence": est.confidence,
                    },
                    "slot": {
                        "index": slot["slot_index"],
                        "score": slot["score"],
                    },
                    "reschedule_count": (entry.get("auto_reschedule_count") or 0) + 1,
                }
            )

            # Update DLQ entry
            self.db.update_dlq_reschedule(
                entry["id"],
                rescheduled_job_id=new_job_id,
                rescheduled_for=slot["scheduled_time"],
                reschedule_reasoning=reasoning,
            )

            logger.info(
                "DLQ #%s: rescheduled as job #%s at slot %s (score=%.1f, cat=%s)",
                entry["id"],
                new_job_id,
                slot["slot_index"],
                slot["score"],
                failure_cat,
            )
            rescheduled.append({"dlq_id": entry["id"], "new_job_id": new_job_id})

        self._last_sweep = time.time()
        return rescheduled
