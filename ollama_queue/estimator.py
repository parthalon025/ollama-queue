"""Duration estimator for queue jobs.

Uses rolling average from DB history, with model-based and generic fallbacks.
"""

from ollama_queue.db import Database


class DurationEstimator:
    """Estimate job durations for queue ETA calculations."""

    MODEL_DEFAULTS = {
        "deepseek-r1:8b": 1800,          # 30 min
        "deepseek-coder-v2:lite": 1200,  # 20 min
        "qwen2.5-coder:14b": 900,        # 15 min
        "qwen2.5:7b": 600,               # 10 min
        "nomic-embed-text": 900,          # 15 min
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
        """Given list of pending jobs, return ETAs for each.

        Each job dict must have 'source' and 'model' keys.
        Returns list of dicts with 'estimated_start_offset' and 'estimated_duration'.
        """
        results = []
        cumulative_offset = 0

        for job in queue_jobs:
            duration = self.estimate(job["source"], model=job.get("model"))
            results.append({
                "estimated_start_offset": cumulative_offset,
                "estimated_duration": duration,
            })
            cumulative_offset += duration

        return results
