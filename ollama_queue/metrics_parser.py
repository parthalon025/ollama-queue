"""Parse Ollama performance metrics from job stdout.

Ollama's generate/chat endpoints emit a final JSON line with ``done: true``
containing timing fields (load_duration, eval_count, eval_duration, etc.).
This module extracts those metrics for storage in the job_metrics table.

Non-Ollama jobs produce no matching JSON — returns None gracefully.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Ollama final response always has "done":true and timing fields
_DONE_PATTERN = re.compile(r'\{[^{}]*"done"\s*:\s*true[^{}]*\}')


def parse_ollama_metrics(stdout: str) -> dict | None:
    """Extract Ollama performance metrics from job stdout.

    Scans for the final ``{"done": true, ...}`` JSON object that Ollama
    emits at the end of generate/chat responses. Returns a dict with
    standardized field names, or None if no metrics found.

    Args:
        stdout: Raw stdout from the job subprocess.

    Returns:
        Dict with keys matching job_metrics columns, or None.
    """
    if not stdout:
        return None

    # Search from the end — the done:true line is always last
    matches = _DONE_PATTERN.findall(stdout)
    if not matches:
        return None

    # Take the last match (final response)
    try:
        data = json.loads(matches[-1])
    except (json.JSONDecodeError, IndexError):
        logger.debug("Failed to parse Ollama metrics JSON")
        return None

    if not data.get("done"):
        return None

    # Extract and normalize fields
    metrics = {}

    # Timing fields (Ollama reports in nanoseconds)
    for field in (
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "total_duration",
    ):
        val = data.get(field)
        if val is not None:
            # Map to _ns suffix for duration fields
            key = f"{field}_ns" if field.endswith("_duration") else field
            metrics[key] = val

    # Model size from response (if present)
    if "model" in data:
        metrics["response_model"] = data["model"]

    return metrics if metrics else None
