"""Bayesian runtime estimator using log-normal model with hierarchical priors.

Predicts how long a job will take based on model size, historical performance,
and token throughput — all learned from this machine's actual behavior.

Uses 4-tier hierarchy:
1. Resource profile prior (weakest — generic bucket)
2. Cross-model performance curve (interpolated from other models)
3. Model-level tok/min history (direct observations)
4. (Model, command) duration history (strongest — exact match)
"""

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Resource profile priors (log-normal parameters: log-mean, log-std, pseudo-observations)
PROFILE_PRIORS = {
    "light": {"log_mean": math.log(30), "log_std": 0.8, "n0": 2},
    "medium": {"log_mean": math.log(120), "log_std": 0.7, "n0": 2},
    "heavy": {"log_mean": math.log(600), "log_std": 0.6, "n0": 2},
    "gpu_heavy": {"log_mean": math.log(900), "log_std": 0.5, "n0": 2},
    "ollama": {"log_mean": math.log(300), "log_std": 0.8, "n0": 1},
}

WARMUP_PRIOR = {"log_mean": math.log(3.0), "log_std": 1.0, "n0": 1}


@dataclass
class Estimate:
    """Runtime estimate with uncertainty."""

    warmup_mean: float = 0.0
    warmup_upper: float = 0.0
    generation_mean: float = 0.0
    generation_upper: float = 0.0
    total_mean: float = 0.0
    total_upper: float = 0.0
    confidence: str = "low"  # low, medium, high
    n_observations: int = 0


class RuntimeEstimator:
    """Bayesian runtime estimator using log-normal model with hierarchical priors."""

    def __init__(self, db):
        self.db = db

    def estimate(
        self,
        model: str,
        command: str | None,
        resource_profile: str,
        loaded_models: list[str] | None = None,
    ) -> Estimate:
        """Estimate runtime for a job."""
        # Tier 1: resource profile prior
        prior = PROFILE_PRIORS.get(resource_profile, PROFILE_PRIORS["ollama"]).copy()

        # Tier 2: cross-model curve — handled externally by PerformanceCurve

        # Tier 3: model-level historical durations
        durations = self.db.get_job_durations(model)
        n_obs = len(durations)

        # Tier 4: (model, command) specific durations
        if command:
            specific = self.db.get_job_durations(model, command)
            if len(specific) >= 3:
                durations = specific
                n_obs = len(specific)

        # Bayesian update
        if durations:
            log_durations = [math.log(max(d, 0.1)) for d in durations]
            n = len(log_durations)
            sample_mean = sum(log_durations) / n

            # Posterior mean (weighted average of prior and sample)
            n0 = prior["n0"]
            post_mean = (n0 * prior["log_mean"] + n * sample_mean) / (n0 + n)

            # Posterior variance
            if n > 1:
                sample_var = sum((x - sample_mean) ** 2 for x in log_durations) / (n - 1)
            else:
                sample_var = prior["log_std"] ** 2
            post_std = math.sqrt((n0 * prior["log_std"] ** 2 + n * sample_var) / (n0 + n))
        else:
            post_mean = prior["log_mean"]
            post_std = prior["log_std"]

        gen_mean = math.exp(post_mean)
        gen_upper = math.exp(post_mean + 1.28 * post_std)  # 90th percentile

        # Warmup estimate
        warmup_mean = 0.0
        warmup_upper = 0.0
        if loaded_models is None or model not in (loaded_models or []):
            warmup_mean, warmup_upper = self._estimate_warmup(model)

        confidence = self._confidence_level(n_obs)

        return Estimate(
            warmup_mean=warmup_mean,
            warmup_upper=warmup_upper,
            generation_mean=gen_mean,
            generation_upper=gen_upper,
            total_mean=warmup_mean + gen_mean,
            total_upper=warmup_upper + gen_upper,
            confidence=confidence,
            n_observations=n_obs,
        )

    def _estimate_warmup(self, model: str) -> tuple[float, float]:
        """Estimate model warmup time from historical load_duration data."""
        warmups = self.db.get_load_durations(model)
        prior = WARMUP_PRIOR.copy()

        if warmups:
            log_warmups = [math.log(max(w, 0.01)) for w in warmups]
            n = len(log_warmups)
            sample_mean = sum(log_warmups) / n
            n0 = prior["n0"]
            post_mean = (n0 * prior["log_mean"] + n * sample_mean) / (n0 + n)

            sample_var = sum((x - sample_mean) ** 2 for x in log_warmups) / (n - 1) if n > 1 else prior["log_std"] ** 2
            post_std = math.sqrt((n0 * prior["log_std"] ** 2 + n * sample_var) / (n0 + n))
        else:
            post_mean = prior["log_mean"]
            post_std = prior["log_std"]

        mean = math.exp(post_mean)
        upper = math.exp(post_mean + 1.28 * post_std)
        return mean, upper

    def _confidence_level(self, n_observations: int) -> str:
        if n_observations >= 5:
            return "high"
        elif n_observations >= 2:
            return "medium"
        return "low"

    def refresh(self, job_id: int | None = None) -> None:
        """Called after job completion — hook for cache invalidation."""
