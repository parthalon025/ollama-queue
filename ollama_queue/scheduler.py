"""Scheduler: recurring job promotion and schedule rebalancing.

Plain English: The calendar manager. Knows which recurring jobs (daily syncs,
hourly scripts) are due to fire, and promotes them into the active queue. Also
spreads out jobs that share an interval so they don't pile up simultaneously
(e.g. three hourly jobs fire at :00, :20, :40 instead of all at :00). Cron
jobs with pinned times are protected so rebalancing doesn't move them.

Decision it drives: When should the next run of a recurring job fire, and if a
new job is being added, which time slot has the lightest existing load?
"""

from __future__ import annotations

import logging
import time

from ollama_queue.db import Database

_log = logging.getLogger(__name__)


class Scheduler:
    """Manages recurring job promotion and schedule rebalancing."""

    _JOBS_CACHE_TTL = 10.0  # seconds before recurring-jobs list cache expires

    def __init__(self, db: Database):
        self.db = db
        self._jobs_cache: tuple[float, list[dict]] | None = None

    def _invalidate_jobs_cache(self) -> None:
        """Drop the recurring-jobs list cache. Call whenever recurring jobs are mutated."""
        self._jobs_cache = None

    def _get_recurring_jobs(self) -> list[dict]:
        """Return the recurring-jobs list, using a short TTL cache to avoid redundant DB reads.

        The cache is invalidated by any method that adds, removes, or modifies a recurring job.
        TTL acts as a safety net for external mutations (e.g. direct DB writes).
        """
        now = time.time()
        if self._jobs_cache is not None:
            cached_at, jobs = self._jobs_cache
            if now - cached_at < self._JOBS_CACHE_TTL:
                return jobs
        jobs = self.db.list_recurring_jobs()
        self._jobs_cache = (now, jobs)
        return jobs

    def promote_due_jobs(
        self,
        now: float | None = None,
        suspend_low_priority: bool = False,
    ) -> list[int]:
        """Promote due recurring jobs to pending. Coalesces duplicates.

        Args:
            suspend_low_priority: If True, skip promotion of priority 8-10 jobs.
                Set by daemon when entropy anomaly indicates critical_backlog.

        Returns list of new job IDs created.
        """
        if now is None:
            now = time.time()
        due = self.db.get_due_recurring_jobs(now)
        # Pre-fetch AoI parameters once — O(1) instead of O(N) DB reads during sort.
        aoi_weight = float(self.db.get_setting("aoi_weight") or 0.3)
        last_success_cache = {rj["id"]: self.db.get_last_successful_run_time(rj["id"]) for rj in due}
        # AoI sort: lower score = higher urgency. Ensures stale jobs promoted first
        # when multiple become due simultaneously.
        due.sort(key=lambda rj: self._aoi_sort_key(rj, now, aoi_weight, last_success_cache.get(rj["id"])))
        new_ids = []
        next_run_updates: dict[int, float] = {}  # rj_id → new next_run; batched at end
        for rj in due:
            # Entropy suspension: skip low-priority promotion during backlog
            if suspend_low_priority and int(rj.get("priority") or 5) >= 8:
                _log.debug(
                    "Skipping promotion of %r (priority=%d) — entropy suspension active",
                    rj["name"],
                    rj.get("priority"),
                )
                continue
            if self.db.has_pending_or_running_recurring(rj["id"]):
                self.db.log_schedule_event(
                    "skipped_duplicate",
                    recurring_job_id=rj["id"],
                    details={"name": rj["name"], "reason": "already pending or running"},
                )
                # Advance next_run to avoid re-evaluating on every poll
                cron_expr = rj.get("cron_expression")
                if cron_expr:
                    import datetime

                    from croniter import croniter

                    start_dt = datetime.datetime.fromtimestamp(now)
                    new_next_run = croniter(cron_expr, start_dt).get_next(datetime.datetime).timestamp()
                else:
                    if not rj.get("interval_seconds") and not rj.get("cron_expression"):
                        _log.warning(
                            "Recurring job #%d has neither interval_seconds nor cron_expression; defaulting to 300s",
                            rj.get("id"),
                        )
                    interval = rj.get("interval_seconds") or 300  # fallback 5min
                    new_next_run = now + interval
                next_run_updates[rj["id"]] = new_next_run
                continue
            job_id = self.db.submit_job(
                command=rj["command"],
                model=rj["model"],
                priority=rj["priority"],
                timeout=rj["timeout"],
                source=rj["source"] or rj["name"],
                tag=rj.get("tag"),
                max_retries=rj.get("max_retries", 0),
                resource_profile=rj.get("resource_profile", "ollama"),
                recurring_job_id=rj["id"],
            )
            self.db.log_schedule_event(
                "promoted",
                recurring_job_id=rj["id"],
                job_id=job_id,
                details={"name": rj["name"]},
            )
            new_ids.append(job_id)
            _log.info("Promoted recurring job %r → job #%d", rj["name"], job_id)
        # Flush all next_run updates in a single batch DB write
        if next_run_updates:
            self.db.batch_set_recurring_next_runs(next_run_updates)
        return new_ids

    def _aoi_sort_key(self, rj: dict, now: float, aoi_weight: float, last_success: float | None) -> float:
        """Compute AoI-weighted scheduling urgency score. Lower = higher priority.

        Score = priority_norm * (1 - aoi_weight) + (1 - staleness_norm) * aoi_weight

        priority_norm: 0=critical(p1), 1=background(p10), normalized to [0,1]
        staleness_norm: 0=fresh, 1=maximally stale (>=5 intervals), normalized to [0,1]
        aoi_weight=0.3 means exactly 30% of score from information staleness.
        """
        priority = max(1, min(10, int(rj.get("priority") or 5)))
        priority_norm = (priority - 1) / 9.0  # 0 = p1 (critical), 1 = p10 (background)
        if last_success is not None:
            interval = float(rj.get("interval_seconds") or 3600)
            # Note: cron jobs (interval_seconds=None) use 3600s fallback.
            # This means cron jobs tend toward staleness_norm=1.0 quickly;
            # AoI tiebreaker degrades to pure priority for cron vs cron comparisons.
            staleness_ratio = (now - last_success) / max(interval, 1.0)
            staleness_norm = min(1.0, staleness_ratio / 5.0)
        else:
            staleness_norm = 1.0  # never completed → maximum urgency

        return priority_norm * (1.0 - aoi_weight) + (1.0 - staleness_norm) * aoi_weight

    def update_next_run(self, recurring_job_id: int, completed_at: float, job_id: int | None = None) -> None:
        """Update next_run after job completion. Anchors to completed_at."""
        self._invalidate_jobs_cache()
        self.db.update_recurring_next_run(recurring_job_id, completed_at, job_id)
        rj = self.db.get_recurring_job(recurring_job_id)
        if rj is None:
            _log.warning("update_next_run: recurring job %d no longer exists (deleted?)", recurring_job_id)
            return
        self.db.log_schedule_event(
            "next_run_updated",
            recurring_job_id=recurring_job_id,
            job_id=job_id,
            details={"name": rj["name"], "next_run": rj["next_run"]},
        )

    def _nudge_past_blocked(self, ts: float, blocked_slots: set[int], job_name: str) -> float:
        """Advance ts by 30-min increments until it clears all blocked slots."""
        for _ in range(self._SLOT_COUNT):
            if self._time_to_slot(ts) not in blocked_slots:
                return ts
            ts += self._SLOT_SECONDS
        _log.warning(
            "Rebalance: all slots blocked for %r — placing at best-effort position",
            job_name,
        )
        return ts

    def rebalance(self, now: float | None = None) -> list[dict]:
        """Rebalance all enabled recurring jobs to spread load evenly.

        Groups jobs by interval, then staggers each group across its interval
        window so jobs with the same cadence don't all fire simultaneously.
        Higher priority jobs get earlier slots within each group.
        """
        if now is None:
            now = time.time()
        self._invalidate_jobs_cache()  # rebalance writes next_run for all interval jobs
        # NOTE: list_recurring_jobs() and the subsequent _set_recurring_next_run() calls each
        # acquire db._lock independently — there is a narrow TOCTOU window where a concurrent
        # add/delete could produce a stale rj_id reference. Rebalance is rare (called on startup
        # and via API) so this race is accepted; add a db-level batch API if it becomes a problem.
        rjs = [r for r in self.db.list_recurring_jobs() if r["enabled"]]
        if not rjs:
            return []

        # Cron jobs have pinned wall-clock times; interval spreading doesn't apply
        rjs = [r for r in rjs if not r.get("cron_expression") and r.get("interval_seconds")]
        if not rjs:
            return []

        # Group by interval_seconds, sort within each group by priority
        groups: dict[int, list[dict]] = {}
        for rj in rjs:
            groups.setdefault(rj["interval_seconds"], []).append(rj)
        for group in groups.values():
            group.sort(key=lambda r: (r["priority"], r["name"]))

        # Build blocked slot set from pinned cron jobs
        blocked_slots = {i for i, s in enumerate(self.load_map(now)) if s >= self._PIN_SCORE}

        changes = []
        for interval, group in sorted(groups.items()):
            n = len(group)
            for i, rj in enumerate(group):
                old_next_run = rj["next_run"]
                candidate = now + (interval * i / n)
                new_next_run = self._nudge_past_blocked(candidate, blocked_slots, rj["name"])

                self.db._set_recurring_next_run(rj["id"], new_next_run)
                change = {
                    "name": rj["name"],
                    "old_next_run": old_next_run,
                    "new_next_run": new_next_run,
                }
                changes.append(change)
                self.db.log_schedule_event(
                    "rebalanced",
                    recurring_job_id=rj["id"],
                    details=change,
                )
                _log.info(
                    "Rebalanced %r: next_run shifted by %.0fs",
                    rj["name"],
                    new_next_run - (old_next_run or now),
                )

        return changes

    _SLOT_COUNT = 48  # 30-min slots across 24h
    _SLOT_SECONDS = 1800  # 30 minutes per slot
    _DAY_SECONDS = 86400
    _PIN_SCORE = 999

    def _time_to_slot(self, unix_ts: float) -> int:
        """Convert a Unix timestamp to a 30-min slot index (0-47) based on local time."""
        import datetime

        dt = datetime.datetime.fromtimestamp(unix_ts)
        seconds_in_day = dt.hour * 3600 + dt.minute * 60 + dt.second
        return (seconds_in_day % self._DAY_SECONDS) // self._SLOT_SECONDS

    def _score_cron_job(self, scores: list[float], rj: dict, job_score: float, now: float) -> None:
        """Apply cron job scores to the load map in-place."""
        import datetime

        from croniter import croniter

        cron_expr = rj["cron_expression"]
        pinned = bool(rj.get("pinned"))
        start_dt = datetime.datetime.fromtimestamp(now)
        c = croniter(cron_expr, start_dt)
        fire_times: list[float] = []
        for _ in range(48):  # max 48 firings in 24h (every 30 min)
            nxt = c.get_next(datetime.datetime)
            if nxt.timestamp() > now + self._DAY_SECONDS:
                break
            fire_times.append(nxt.timestamp())
        if not fire_times:
            next_run = rj.get("next_run")
            if next_run:
                fire_times = [next_run]

        for ft in fire_times:
            slot = self._time_to_slot(ft)
            if pinned:
                for adj in [slot - 1, slot, slot + 1]:
                    scores[adj % self._SLOT_COUNT] = self._PIN_SCORE
            else:
                scores[slot] = min(self._PIN_SCORE - 1, scores[slot] + job_score)

    def _score_interval_job(self, scores: list[float], rj: dict, job_score: float, now: float) -> None:
        """Apply interval job scores to the load map in-place.

        Firings are anchored to local midnight so interval and cron slot indices
        are coherent — both use local wall-clock time.
        """
        import datetime

        interval = rj["interval_seconds"]
        firings_per_day = max(1, self._DAY_SECONDS // interval)
        local_midnight = datetime.datetime.combine(
            datetime.datetime.fromtimestamp(now).date(), datetime.time.min
        ).timestamp()
        for i in range(firings_per_day):
            fire_ts = local_midnight + (i * interval) % self._DAY_SECONDS
            slot = self._time_to_slot(fire_ts)
            if scores[slot] < self._PIN_SCORE:
                scores[slot] = min(self._PIN_SCORE - 1, scores[slot] + job_score)

    def load_map(self, now: float | None = None) -> list[float]:
        """Build a 48-slot priority-weighted load map for the next 24 hours.

        Slots are 30-minute windows starting at 00:00.
        Pinned cron jobs write 999 to their slot and adjacent slots (±15 min buffer).
        Non-pinned cron jobs write (11 - priority) to their slot.
        Interval jobs distribute fire times across 24h and score each hit slot.
        """
        if now is None:
            now = time.time()

        scores: list[float] = [0.0] * self._SLOT_COUNT
        for rj in self._get_recurring_jobs():
            if not rj["enabled"]:
                continue
            priority = rj.get("priority") or 5
            job_score = 11 - priority  # priority 1 → 10, priority 10 → 1
            if rj.get("cron_expression"):
                self._score_cron_job(scores, rj, job_score, now)
            elif rj.get("interval_seconds"):
                self._score_interval_job(scores, rj, job_score, now)
        return scores

    def load_map_extended(self, now: float | None = None) -> list[dict]:
        """Build a 48-slot load map with VRAM estimates and scheduling metadata.

        Returns list of dicts with keys consumed by find_fitting_slot:
        - load: priority-weighted score
        - vram_committed_gb: estimated VRAM commitment
        - is_pinned: True if slot is pinned (score >= PIN_SCORE)
        - recurring_ids: list of recurring job IDs firing in this slot
        - timestamp: wall-clock time for this slot (anchored to local midnight)
        """
        import datetime as _dt

        if now is None:
            now = time.time()

        scores = self.load_map(now=now)
        vram: list[float] = [0.0] * self._SLOT_COUNT
        slot_rj_ids: list[list[int]] = [[] for _ in range(self._SLOT_COUNT)]

        local_midnight = _dt.datetime.combine(_dt.datetime.fromtimestamp(now).date(), _dt.time.min).timestamp()

        for rj in self._get_recurring_jobs():
            if not rj["enabled"]:
                continue
            model = rj.get("model", "")
            model_vram = _estimate_model_vram(model)

            # Build a temporary score array to find which slots this job fires in
            tmp: list[float] = [0.0] * self._SLOT_COUNT
            if rj.get("cron_expression"):
                self._score_cron_job(tmp, rj, 1.0, now)
            elif rj.get("interval_seconds"):
                self._score_interval_job(tmp, rj, 1.0, now)

            for i in range(self._SLOT_COUNT):
                if tmp[i] > 0:
                    slot_rj_ids[i].append(rj["id"])
                    if model_vram > 0:
                        vram[i] += model_vram

        return [
            {
                "load": scores[i],
                "vram_committed_gb": round(vram[i], 1),
                "is_pinned": scores[i] >= self._PIN_SCORE,
                "recurring_ids": slot_rj_ids[i],
                "timestamp": local_midnight + i * self._SLOT_SECONDS,
            }
            for i in range(self._SLOT_COUNT)
        ]

    def suggest_time(
        self,
        priority: int = 5,
        top_n: int = 3,
        now: float | None = None,
    ) -> list[tuple[str, float]]:
        """Return top_n suggested cron expressions for a new job at the given priority.

        Returns list of (cron_expression, load_score) tuples, lowest score first.
        Excludes slots with score >= _PIN_SCORE (pinned blocks).
        """
        if now is None:
            now = time.time()
        scores = self.load_map(now)
        # Build (score, slot_index) pairs, excluding hard blocks
        candidates = [(scores[i], i) for i in range(self._SLOT_COUNT) if scores[i] < self._PIN_SCORE]
        # Sort by score ascending, then slot index (prefer earlier in day on ties)
        candidates.sort(key=lambda x: (x[0], x[1]))
        results = []
        for score, slot in candidates[:top_n]:
            # Convert slot index to HH:MM cron expression
            total_minutes = slot * 30
            hour = total_minutes // 60
            minute = total_minutes % 60
            cron_expr = f"{minute} {hour} * * *"
            results.append((cron_expr, score))
        return results


import re as _re

_SIZE_PATTERN = _re.compile(r"(\d+(?:\.\d+)?)b", _re.IGNORECASE)

# Rough mapping from parameter count (billions) to VRAM (GB) at Q4 quantization
_PARAM_TO_VRAM = {
    0.5: 0.5,
    1: 1.0,
    1.5: 1.2,
    3: 2.0,
    7: 4.5,
    8: 5.0,
    9: 5.5,
    13: 8.0,
    14: 8.5,
    32: 20.0,
    70: 40.0,
}


def _estimate_model_vram(model: str) -> float:
    """Estimate VRAM usage in GB from a model name like 'qwen2.5:7b'.

    Uses parameter count hints in the model name (e.g. '7b', '14b') and maps to
    approximate VRAM at Q4 quantization. Returns 4.0 GB default if no size hint found.
    """
    match = _SIZE_PATTERN.search(model)
    if not match:
        return 4.0
    params = float(match.group(1))
    # Find closest known size
    best_key = min(_PARAM_TO_VRAM, key=lambda k: abs(k - params))
    if abs(best_key - params) / max(params, 0.1) > 0.5:
        # Too far from any known size — interpolate linearly
        return params * 0.6  # ~0.6 GB per billion params at Q4
    return _PARAM_TO_VRAM[best_key]
