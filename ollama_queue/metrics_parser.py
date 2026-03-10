"""Parse Ollama performance metrics from job stdout.

Ollama's generate/chat endpoints emit a final JSON line with ``done: true``
containing timing fields (load_duration, eval_count, eval_duration, etc.).
This module extracts those metrics for storage in the job_metrics table.

Non-Ollama jobs produce no matching JSON — returns None gracefully.
"""

import json
import logging

logger = logging.getLogger(__name__)


def parse_ollama_metrics(stdout: str) -> dict | None:
    """Extract Ollama performance metrics from job stdout.

    Scans for the final ``{"done": true, ...}`` JSON object that Ollama
    emits at the end of generate/chat responses. Returns a dict with
    standardized field names, or None if no metrics found.
    """
    if not stdout:
        return None

    last_done = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("done") is True:
            last_done = data

    if last_done is None:
        return None

    # Extract and normalize fields
    metrics = {}
    for field in (
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "total_duration",
    ):
        val = last_done.get(field)
        if val is not None:
            key = f"{field}_ns" if field.endswith("_duration") else field
            metrics[key] = val

    if "model" in last_done:
        metrics["response_model"] = last_done["model"]

    return metrics if metrics else None
