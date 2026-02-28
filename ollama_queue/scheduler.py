"""Scheduler: recurring job promotion and schedule rebalancing."""

from __future__ import annotations

import logging
import time

from ollama_queue.db import Database

_log = logging.getLogger(__name__)


class Scheduler:
    """Manages recurring job promotion and schedule rebalancing."""

    def __init__(self, db: Database):
        self.db = db

    def promote_due_jobs(self, now: float | None = None) -> list[int]:
        """Promote due recurring jobs to pending. Coalesces duplicates.

        Returns list of new job IDs created.
        """
        if now is None:
            now = time.time()
        due = self.db.get_due_recurring_jobs(now)
        new_ids = []
        for rj in due:
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
                self.db._set_recurring_next_run(rj["id"], new_next_run)
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
        return new_ids

    def update_next_run(self, recurring_job_id: int, completed_at: float, job_id: int | None = None) -> None:
        """Update next_run after job completion. Anchors to completed_at."""
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
        for rj in self.db.list_recurring_jobs():
            if not rj["enabled"]:
                continue
            priority = rj.get("priority") or 5
            job_score = 11 - priority  # priority 1 → 10, priority 10 → 1
            if rj.get("cron_expression"):
                self._score_cron_job(scores, rj, job_score, now)
            elif rj.get("interval_seconds"):
                self._score_interval_job(scores, rj, job_score, now)
        return scores

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
