"""Duration estimator for queue jobs.

Plain English: The time-predictor. When you submit a job, the dashboard shows
"estimated wait: X minutes." This module figures out that estimate by checking
historical run times for the same source/model, falling back to known defaults
for popular models, then a generic 10-minute guess if nothing better exists.

Decision it drives: How long should we tell the user this job will take, and
what ETA do we show for each item in the queue?
"""

from typing import ClassVar

from ollama_queue.db import Database
from ollama_queue.models import OllamaModels


class DurationEstimator:
    """Estimate job durations for queue ETA calculations."""

    MODEL_DEFAULTS: ClassVar[dict[str, int]] = {
        "deepseek-r1:8b": 1800,  # 30 min
        "deepseek-coder-v2:lite": 1200,  # 20 min
        "qwen2.5-coder:14b": 900,  # 15 min
        "qwen2.5:7b": 600,  # 10 min
        "nomic-embed-text": 900,  # 15 min
    }
    GENERIC_DEFAULT = 600  # 10 min

    def __init__(self, db: Database):
        self.db = db

    def estimate(self, source: str, model: str | None = None) -> float:
        """Return estimated duration in seconds.

        Lookup order:
        1. DB rolling average (last 5 successful runs for this source)
        2. Model-based default (substring match against known models)
        3. Generic default (600s)
        """
        db_est = self.db.estimate_duration(source)
        if db_est is not None:
            return db_est

        if model:
            for key, default in self.MODEL_DEFAULTS.items():
                if key in model:
                    return default

        return self.GENERIC_DEFAULT

    def estimate_with_variance(
        self,
        source: str,
        model: str | None = None,
        cached: dict | None = None,
    ) -> tuple[float, float]:
        """Return (mean_seconds, cv_squared) for a source.

        cv_squared = Var(S) / Mean(S)^2

        Interpretation guide:
          cv_squared < 0.5:  highly predictable (same model, consistent workload)
          cv_squared 0.5-1.5: normal variance
          cv_squared > 1.5:  unreliable estimate (mixed workloads, treat skeptically)

        Falls back to cached bulk mean, then model default, then GENERIC_DEFAULT.
        Returns cv_squared=1.5 (conservative) when no variance data available.
        """
        stats = self.db.estimate_duration_stats(source)
        if stats is not None:
            mean, variance = stats
            if mean > 0:
                cv_sq = variance / (mean**2)
                return mean, max(0.0, cv_sq)

        # No variance data — fall back to mean only
        mean = None
        if cached:
            mean = cached.get(source)
        if mean is None:
            mean = self.db.estimate_duration(source)
        if mean is None and model:
            for key, default in self.MODEL_DEFAULTS.items():
                if key in model:
                    mean = float(default)
                    break
        if mean is None:
            mean = float(self.GENERIC_DEFAULT)

        return mean, 1.5  # unknown variance → conservative default

    def queue_etas(self, queue_jobs: list[dict], om: OllamaModels | None = None) -> list[dict]:
        """Given list of pending jobs, return ETAs for each, concurrency-aware.

        Embed-profile jobs don't consume a serial slot — they show concurrent=True
        and don't advance the cumulative offset for subsequent jobs.

        Each job dict must have 'source' and 'model' keys.
        Returns list of dicts with 'estimated_start_offset', 'estimated_duration',
        and 'concurrent'.

        om: optional OllamaModels instance to reuse (avoids re-instantiation on each call).
            If None, a new instance is created.
        """
        results = []
        cumulative_offset: float = 0.0
        if om is None:
            om = OllamaModels()

        for job in queue_jobs:
            duration = self.estimate(job["source"], model=job.get("model"))
            profile = job.get("resource_profile") or om.classify(job.get("model") or "")["resource_profile"]
            is_concurrent = profile == "embed"
            results.append(
                {
                    "estimated_start_offset": 0.0 if is_concurrent else cumulative_offset,
                    "estimated_duration": duration,
                    "concurrent": is_concurrent,
                }
            )
            if not is_concurrent:
                cumulative_offset += duration

        return results
