"""Proactive deferral scheduler — sweeps deferred jobs and resumes or schedules them.

Periodically checks all unscheduled deferred jobs. If a slot is available now
(slot_index == 0), resumes immediately. If a future slot scores well, records
the scheduled time. If a scheduled time has already passed, resumes the job.
"""

import json
import logging
import threading
import time

from ollama_queue.scheduling.scheduler import _estimate_model_vram
from ollama_queue.scheduling.slot_scoring import find_fitting_slot

logger = logging.getLogger(__name__)


class DeferralScheduler:
    """Sweeps deferred jobs and resumes them when conditions are favorable."""

    def __init__(self, db, estimator, load_map_fn, vram_total_fn=None):
        self.db = db
        self.estimator = estimator
        self.load_map_fn = load_map_fn
        self.vram_total_fn = vram_total_fn or (lambda: 24.0)
        self._sweep_lock = threading.Lock()

    def sweep(self) -> list[dict]:
        """Check deferred jobs and resume those that can now run.

        Returns a list of dicts with deferral_id and job_id for each resumed job.
        Non-blocking: returns [] immediately if another sweep is in progress.
        """
        if not self._sweep_lock.acquire(blocking=False):
            return []
        try:
            return self._do_sweep()
        finally:
            self._sweep_lock.release()

    def _do_sweep(self) -> list[dict]:
        """Process deferred jobs — resume scheduled-past entries and find slots for unscheduled."""
        if not self.db.get_setting("defer.enabled"):
            return []

        resumed = []
        now = time.time()

        # Phase 1: Resume entries whose scheduled time has passed
        all_deferred = self.db.list_deferred()
        for entry in all_deferred:
            if entry.get("scheduled_for") and entry["scheduled_for"] <= now:
                job = self.db.get_job(entry["job_id"])
                if not job or job["status"] != "deferred":
                    continue
                self.db.resume_deferred_job(entry["id"])
                resumed.append({"deferral_id": entry["id"], "job_id": entry["job_id"]})
                logger.info(
                    "Resumed deferred job %s (deferral %s) — scheduled time passed",
                    entry["job_id"],
                    entry["id"],
                )

        # Phase 2: Find slots for unscheduled entries
        unscheduled = self.db.list_deferred(unscheduled_only=True)
        if not unscheduled:
            return resumed

        load_map = self.load_map_fn()

        for entry in unscheduled:
            try:
                result = self._process_unscheduled_entry(entry, load_map)
                if result is not None:
                    resumed.append(result)
            except Exception as exc:
                logger.warning(
                    "_do_sweep: skipping deferral entry %s due to error: %s",
                    entry.get("id", "?"),
                    exc,
                )

        return resumed

    def _process_unscheduled_entry(self, entry: dict, load_map: list) -> dict | None:
        """Attempt to find and apply a slot for a single unscheduled deferral entry.

        Returns a resumed-job dict if the job was resumed immediately, or None otherwise.
        Raises on any error — caller wraps in try/except to continue the sweep loop.
        """
        job = self.db.get_job(entry["job_id"])
        if not job or job["status"] != "deferred":
            return None

        est = self.estimator.estimate(
            job.get("model", ""),
            job.get("command", ""),
            job.get("resource_profile", "ollama"),
        )

        model = job.get("model", "")
        job_vram = _estimate_model_vram(model)
        estimated_slots = max(1, int(est.total_upper / 1800) + 1)
        slot = find_fitting_slot(
            load_map,
            job_vram_needed_gb=job_vram,
            total_vram_gb=self.vram_total_fn(),
            estimated_slots=estimated_slots,
            job_model=model,
        )

        if slot is None:
            return None

        if slot["slot_index"] == 0:
            self.db.resume_deferred_job(entry["id"])
            logger.info(
                "Resumed deferred job %s (deferral %s) — slot available now",
                entry["job_id"],
                entry["id"],
            )
            return {"deferral_id": entry["id"], "job_id": entry["job_id"]}

        # Schedule for later
        scoring = json.dumps(
            {
                "slot_index": slot["slot_index"],
                "score": slot["score"],
                "estimate": {"mean": est.total_mean, "upper": est.total_upper},
            }
        )
        self.db.update_deferral_schedule(
            entry["id"],
            scheduled_for=slot["scheduled_time"],
            scoring_snapshot=scoring,
        )
        logger.debug(
            "Scheduled deferred job %s (deferral %s) for slot %d at %.0f",
            entry["job_id"],
            entry["id"],
            slot["slot_index"],
            slot["scheduled_time"],
        )
        return None
