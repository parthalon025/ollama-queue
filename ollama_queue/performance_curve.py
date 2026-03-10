"""Cross-model performance curve fitted from empirical hardware data.

Uses log-linear regression on (log(model_size), log(tok_per_min))
to estimate performance for never-run models based on observed
performance of other models on this machine.
"""

import logging
import math

logger = logging.getLogger(__name__)


def _linear_regression(x: list[float], y: list[float]) -> tuple[float, float]:
    """Simple OLS linear regression. Returns (slope, intercept)."""
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(a * b for a, b in zip(x, y, strict=False))
    sum_x2 = sum(a**2 for a in x)

    denom = n * sum_x2 - sum_x**2
    if abs(denom) < 1e-10:
        return 0.0, sum_y / n if n else 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


class PerformanceCurve:
    """Cross-model performance curve fitted from empirical hardware data."""

    def __init__(self):
        self._tok_slope: float | None = None
        self._tok_intercept: float | None = None
        self._tok_residual_std: float | None = None
        self._warmup_slope: float | None = None
        self._warmup_intercept: float | None = None
        self._points: list[dict] = []
        self.fitted: bool = False

    def fit(self, model_stats: list[dict]) -> None:
        """Fit curves from model aggregate stats.

        Each entry: {model_size_gb, avg_tok_per_min, avg_warmup_s (optional)}
        """
        self._points = model_stats

        # tok/min curve: log-linear regression
        valid_tok = [s for s in model_stats if s.get("avg_tok_per_min") and s.get("model_size_gb")]
        if len(valid_tok) >= 2:
            log_sizes = [math.log(s["model_size_gb"]) for s in valid_tok]
            log_rates = [math.log(s["avg_tok_per_min"]) for s in valid_tok]
            self._tok_slope, self._tok_intercept = _linear_regression(log_sizes, log_rates)
            # Residual std for confidence intervals
            predicted = [self._tok_slope * x + self._tok_intercept for x in log_sizes]
            residuals = [a - p for a, p in zip(log_rates, predicted, strict=False)]
            self._tok_residual_std = (
                math.sqrt(sum(r**2 for r in residuals) / (len(residuals) - 2)) if len(residuals) > 2 else 0.3
            )
            self.fitted = True
        elif len(valid_tok) == 1:
            # Single point — use typical slope
            s = valid_tok[0]
            self._tok_slope = -0.7  # typical power-law exponent
            self._tok_intercept = math.log(s["avg_tok_per_min"]) - self._tok_slope * math.log(s["model_size_gb"])
            self._tok_residual_std = 0.5
            self.fitted = True

        # warmup curve: linear regression on (size, warmup)
        valid_warmup = [s for s in model_stats if s.get("avg_warmup_s") and s.get("model_size_gb")]
        if len(valid_warmup) >= 2:
            sizes = [s["model_size_gb"] for s in valid_warmup]
            warmups = [s["avg_warmup_s"] for s in valid_warmup]
            self._warmup_slope, self._warmup_intercept = _linear_regression(sizes, warmups)

    def predict_tok_per_min(self, model_size_gb: float) -> float | None:
        """Predict tok/min for a model size."""
        if self._tok_slope is None:
            return None
        log_rate = self._tok_slope * math.log(model_size_gb) + self._tok_intercept
        return math.exp(log_rate)

    def predict_tok_per_min_ci(self, model_size_gb: float, z: float = 1.28) -> tuple[float, float, float] | None:
        """Predict tok/min with confidence interval (default 90%)."""
        if self._tok_slope is None:
            return None
        log_rate = self._tok_slope * math.log(model_size_gb) + self._tok_intercept
        std = self._tok_residual_std or 0.3
        mean = math.exp(log_rate)
        lower = math.exp(log_rate - z * std)
        upper = math.exp(log_rate + z * std)
        return mean, lower, upper

    def predict_warmup(self, model_size_gb: float) -> float | None:
        """Predict warmup time (seconds) for a model size."""
        if self._warmup_slope is None:
            return None
        return max(0.1, self._warmup_slope * model_size_gb + self._warmup_intercept)

    def get_curve_data(self) -> dict:
        """Return fitted curve parameters for API/UI."""
        return {
            "tok_slope": self._tok_slope,
            "tok_intercept": self._tok_intercept,
            "tok_residual_std": self._tok_residual_std,
            "warmup_slope": self._warmup_slope,
            "warmup_intercept": self._warmup_intercept,
            "n_points": len(self._points),
            "points": self._points,
            "fitted": self.fitted,
        }
