"""Validation for eval variant params and provider settings."""

from __future__ import annotations

import difflib
import json

VALID_OLLAMA_PARAMS = frozenset(
    {
        "top_k",
        "top_p",
        "mirostat",
        "mirostat_eta",
        "mirostat_tau",
        "repeat_penalty",
        "repeat_last_n",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "stop",
        "tfs_z",
        "typical_p",
        "num_predict",
        "num_keep",
        "num_batch",
        "num_thread",
        "num_gpu",
    }
)

FLAT_COLUMN_PARAMS = frozenset({"temperature", "num_ctx"})

VALID_PROVIDERS = frozenset({"ollama", "claude", "openai"})


def validate_variant_params(params_raw: str | dict | list | None) -> str:
    """Parse and validate Ollama params. Returns sorted JSON string.

    Raises ValueError on invalid input (callers convert to HTTPException).
    """
    if params_raw is None:
        return "{}"

    if isinstance(params_raw, str):
        try:
            params = json.loads(params_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
    else:
        params = params_raw

    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")

    if not params:
        return "{}"

    # Reject flat-column params (prevents ambiguity)
    overlap = FLAT_COLUMN_PARAMS & set(params.keys())
    if overlap:
        raise ValueError(f"Use flat fields for {overlap}, not params")

    # Reject unknown params with fuzzy suggestions
    invalid = set(params.keys()) - VALID_OLLAMA_PARAMS
    if invalid:
        parts = []
        for key in sorted(invalid):
            matches = difflib.get_close_matches(key, VALID_OLLAMA_PARAMS, n=1, cutoff=0.6)
            if matches:
                parts.append(f"'{key}' — did you mean '{matches[0]}'?")
            else:
                parts.append(f"'{key}' is not a valid Ollama param")
        raise ValueError(f"Invalid params: {'; '.join(parts)}")

    return json.dumps(params, sort_keys=True)


def validate_provider(provider: str | None) -> str:
    """Validate provider string. Returns normalized provider name."""
    if provider is None:
        return "ollama"
    provider = provider.strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Invalid provider '{provider}'. Must be one of: {', '.join(sorted(VALID_PROVIDERS))}")
    return provider
