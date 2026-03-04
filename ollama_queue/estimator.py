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

    def queue_etas(self, queue_jobs: list[dict]) -> list[dict]:
        """Given list of pending jobs, return ETAs for each, concurrency-aware.

        Embed-profile jobs don't consume a serial slot — they show concurrent=True
        and don't advance the cumulative offset for subsequent jobs.

        Each job dict must have 'source' and 'model' keys.
        Returns list of dicts with 'estimated_start_offset', 'estimated_duration',
        and 'concurrent'.
        """
        results = []
        cumulative_offset: float = 0.0
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
