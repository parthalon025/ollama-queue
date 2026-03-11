# Eval Enhancement — Implementation Plan (Phase 1: Foundation)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add system_prompt, params JSON bag, training_config, and provider columns to eval_variants; build multi-provider abstraction (Ollama + Claude + OpenAI); wire through proxy, CRUD, diff, reports, and promotion.

**Architecture:** Hybrid schema (flat columns for high-use params, JSON bag for long tail). Provider abstraction routes generation through Ollama proxy or Claude/OpenAI SDKs. Validation allowlist prevents typos in Ollama params. All existing behavior preserved (additive changes only).

**Tech Stack:** Python 3.12, SQLite (WAL), FastAPI, httpx, anthropic SDK (optional), openai SDK (optional)

**Design Doc:** `docs/plans/2026-03-11-eval-enhancement-design.md`

**Subsequent Phases (separate plans):**
- Phase 2: Quality (assertions, judge debias + cache, cost tracking)
- Phase 3: Intelligence (suggestions engine, reasoning loop, oracle)
- Phase 4: Generalization (YAML task abstraction, eval set rotation)
- Phase 5: Fine-tuning (Unsloth guided workflow)
- Phase 6: Frontend (SPA redesign — card grid, compare matrix, sweep, optimization timeline)

---

## Batch 1: Schema + Validation

### Task 1: Add new columns to eval_variants

**Files:**
- Modify: `ollama_queue/db/schema.py:91-173` (add to `_run_migrations`)
- Test: `tests/test_api_eval_variants.py`

**Step 1: Write the failing test**

```python
def test_system_variants_have_new_columns_after_init(client):
    """System variants should have params, system_prompt, training_config, provider columns."""
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variant_a = next(v for v in resp.json() if v["id"] == "A")
    assert "params" in variant_a
    assert "system_prompt" in variant_a
    assert "training_config" in variant_a
    assert "provider" in variant_a
    assert variant_a["params"] == "{}"
    assert variant_a["provider"] == "ollama"
    assert variant_a["system_prompt"] is None
    assert variant_a["training_config"] is None
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py::test_system_variants_have_new_columns_after_init -v`
Expected: FAIL — KeyError or missing columns

**Step 3: Write minimal implementation**

In `ollama_queue/db/schema.py`, add to `_run_migrations()` after the `_system_descriptions` backfill block:

```python
        # Eval enhancement: variant params, system_prompt, training_config, provider
        self._add_column_if_missing(conn, "eval_variants", "system_prompt", "TEXT")
        self._add_column_if_missing(conn, "eval_variants", "params", "TEXT DEFAULT '{}'")
        self._add_column_if_missing(conn, "eval_variants", "training_config", "TEXT")
        self._add_column_if_missing(conn, "eval_variants", "provider", "TEXT DEFAULT 'ollama'")
        # Backfill pre-existing rows (INSERT OR IGNORE skips them — Lesson #1268)
        conn.execute("UPDATE eval_variants SET params = '{}' WHERE params IS NULL")
        conn.execute("UPDATE eval_variants SET provider = 'ollama' WHERE provider IS NULL")
```

**Step 4: Run test to verify it passes**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py::test_system_variants_have_new_columns_after_init -v`
Expected: PASS

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/db/schema.py tests/test_api_eval_variants.py
git commit -m "feat(eval): add system_prompt, params, training_config, provider columns to eval_variants"
```

---

### Task 2: Add eval_cache table and eval_runs columns

**Files:**
- Modify: `ollama_queue/db/schema.py` (add table in `initialize()`, add columns in `_run_migrations`)

**Step 1: Write the failing test**

```python
def test_eval_cache_table_exists(client_and_db):
    """eval_cache table should exist after initialization."""
    _, db = client_and_db
    with db._lock:
        conn = db._connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='eval_cache'"
        ).fetchone()
    assert row is not None


def test_eval_runs_has_cost_and_oracle_columns(client_and_db):
    """eval_runs should have cost_json, oracle_json, suggestions_json columns."""
    _, db = client_and_db
    with db._lock:
        conn = db._connect()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(eval_runs)").fetchall()}
    assert "cost_json" in cols
    assert "oracle_json" in cols
    assert "suggestions_json" in cols
```

**Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py::test_eval_cache_table_exists tests/test_api_eval_variants.py::test_eval_runs_has_cost_and_oracle_columns -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `schema.py` `initialize()`, add after the existing `CREATE TABLE IF NOT EXISTS` block (before `_run_migrations`):

```sql
CREATE TABLE IF NOT EXISTS eval_cache (
    principle_hash TEXT NOT NULL,
    target_hash TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    judge_mode TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    reasoning TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (principle_hash, target_hash, judge_model, judge_mode)
);
```

In `_run_migrations()`:

```python
        # Eval enhancement: run-level tracking columns
        self._add_column_if_missing(conn, "eval_runs", "cost_json", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "oracle_json", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "suggestions_json", "TEXT")
```

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py::test_eval_cache_table_exists tests/test_api_eval_variants.py::test_eval_runs_has_cost_and_oracle_columns -v`
Expected: PASS

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/db/schema.py tests/test_api_eval_variants.py
git commit -m "feat(eval): add eval_cache table and cost/oracle/suggestions columns on eval_runs"
```

---

### Task 3: Params validation helper

**Files:**
- Create: `ollama_queue/eval/validation.py`
- Test: `tests/test_eval_validation.py`

**Step 1: Write the failing tests**

```python
"""Tests for eval variant params validation."""

import pytest

from ollama_queue.eval.validation import validate_variant_params, VALID_OLLAMA_PARAMS


class TestValidateVariantParams:
    def test_none_returns_empty_json(self):
        assert validate_variant_params(None) == "{}"

    def test_empty_dict_returns_empty_json(self):
        assert validate_variant_params({}) == "{}"

    def test_valid_params_accepted(self):
        result = validate_variant_params({"top_k": 40, "top_p": 0.9})
        assert '"top_k": 40' in result
        assert '"top_p": 0.9' in result

    def test_string_input_parsed(self):
        result = validate_variant_params('{"top_k": 40}')
        assert '"top_k": 40' in result

    def test_invalid_param_rejected(self):
        with pytest.raises(ValueError, match="topk"):
            validate_variant_params({"topk": 40})

    def test_fuzzy_suggestion_for_typo(self):
        with pytest.raises(ValueError, match="top_k"):
            validate_variant_params({"topk": 40})

    def test_temperature_in_params_rejected(self):
        with pytest.raises(ValueError, match="flat fields"):
            validate_variant_params({"temperature": 0.5})

    def test_num_ctx_in_params_rejected(self):
        with pytest.raises(ValueError, match="flat fields"):
            validate_variant_params({"num_ctx": 4096})

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            validate_variant_params([1, 2, 3])

    def test_invalid_json_string_rejected(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            validate_variant_params("not valid json")

    def test_sorted_keys_in_output(self):
        result = validate_variant_params({"top_p": 0.9, "top_k": 40})
        assert result.index("top_k") < result.index("top_p")
```

**Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_eval_validation.py -v`
Expected: FAIL — ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
"""Validation for eval variant params and provider settings."""

from __future__ import annotations

import difflib
import json

VALID_OLLAMA_PARAMS = frozenset({
    "top_k", "top_p", "mirostat", "mirostat_eta", "mirostat_tau",
    "repeat_penalty", "repeat_last_n", "presence_penalty", "frequency_penalty",
    "seed", "stop", "tfs_z", "typical_p", "num_predict", "num_keep",
    "num_batch", "num_thread", "num_gpu",
})

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
```

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_eval_validation.py -v`
Expected: PASS (11/11)

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/eval/validation.py tests/test_eval_validation.py
git commit -m "feat(eval): add params validation with fuzzy suggestions and provider validation"
```

---

## Batch 2: Provider Abstraction

### Task 4: Provider interface and Ollama provider

**Files:**
- Create: `ollama_queue/eval/providers.py`
- Test: `tests/test_eval_providers.py`

**Step 1: Write the failing tests**

```python
"""Tests for eval provider abstraction."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ollama_queue.eval.providers import (
    EvalProvider,
    OllamaProvider,
    get_provider,
)


class TestOllamaProvider:
    def test_generate_calls_proxy(self):
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("result text", {"tokens": 100}, 123)
            text, usage, job_id = provider.generate(
                prompt="test prompt",
                system=None,
                model="qwen2.5:7b",
                temperature=0.6,
                num_ctx=8192,
                params=None,
                timeout=300,
                source="test",
            )
        assert text == "result text"
        assert usage["tokens"] == 100
        # Verify proxy was called with correct options
        call_kwargs = mock.call_args
        assert call_kwargs is not None

    def test_generate_merges_extra_params(self):
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("text", {}, None)
            provider.generate(
                prompt="test",
                system="Be precise",
                model="qwen2.5:7b",
                temperature=0.6,
                num_ctx=8192,
                params={"top_k": 40, "top_p": 0.9},
                timeout=300,
                source="test",
            )
        body = mock.call_args[0][0]  # first positional arg is the body dict
        assert body["options"]["top_k"] == 40
        assert body["options"]["top_p"] == 0.9
        assert body["options"]["temperature"] == 0.6  # flat column wins
        assert body["system"] == "Be precise"

    def test_generate_omits_system_when_none(self):
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("text", {}, None)
            provider.generate(
                prompt="test", system=None, model="m", temperature=0.6,
                num_ctx=8192, params=None, timeout=300, source="test",
            )
        body = mock.call_args[0][0]
        assert "system" not in body


class TestGetProvider:
    def test_ollama_returns_ollama_provider(self):
        p = get_provider("ollama", http_base="http://localhost:7683")
        assert isinstance(p, OllamaProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("invalid")

    def test_claude_without_sdk_raises(self):
        with patch.dict("sys.modules", {"anthropic": None}):
            with pytest.raises(ImportError, match="anthropic"):
                get_provider("claude")

    def test_openai_without_sdk_raises(self):
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="openai"):
                get_provider("openai")
```

**Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_eval_providers.py -v`
Expected: FAIL — ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
"""Multi-provider abstraction for eval pipeline.

Providers: Ollama (via queue proxy), Claude (Anthropic SDK), OpenAI (OpenAI SDK).
All providers return (text, usage_metadata, job_id_or_none).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

_log = logging.getLogger(__name__)

_RETRYABLE_CODES = {429, 502, 503, 504}
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 2.0


def _call_proxy_raw(
    body: dict[str, Any],
    http_base: str,
    timeout: int,
) -> tuple[str | None, dict, int | None]:
    """Low-level POST to ollama-queue proxy. Returns (text, usage, job_id)."""
    import re

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=timeout + 30) as client:
                resp = client.post(
                    f"{http_base}/api/generate",
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code in _RETRYABLE_CODES and attempt < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                _log.warning("proxy %d retry in %.0fs", resp.status_code, delay)
                time.sleep(delay)
                last_exc = Exception(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("response", "")
            # Strip <think>...</think> tags
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            usage = {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_duration_ns": data.get("total_duration", 0),
            }
            job_id = data.get("queue_job_id")
            return raw if raw else None, usage, job_id
        except httpx.HTTPStatusError:
            _log.exception("proxy call failed (HTTP error)")
            return None, {}, None
        except Exception:
            _log.exception("proxy call failed")
            if attempt >= _MAX_RETRIES:
                return None, {}, None
            last_exc = Exception("retry")
            time.sleep(_RETRY_BASE_DELAY * (2**attempt))
    return None, {}, None


class EvalProvider(ABC):
    """Unified interface for LLM calls across providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str | None,
        model: str,
        temperature: float,
        num_ctx: int,
        params: dict | None,
        timeout: int,
        source: str,
        priority: int = 2,
    ) -> tuple[str | None, dict, int | None]:
        """Generate text. Returns (text, usage_metadata, provider_job_id)."""
        ...


class OllamaProvider(EvalProvider):
    """Routes through ollama-queue proxy."""

    def __init__(self, http_base: str = "http://127.0.0.1:7683"):
        self.http_base = http_base

    def generate(self, prompt, system, model, temperature, num_ctx,
                 params, timeout, source, priority=2):
        options: dict[str, Any] = {"temperature": temperature, "num_ctx": num_ctx}
        if params:
            for k, v in params.items():
                if k not in ("temperature", "num_ctx"):
                    options[k] = v

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options,
            "_priority": priority,
            "_source": source,
            "_timeout": timeout,
        }
        if system:
            body["system"] = system

        return _call_proxy_raw(body, self.http_base, timeout)


class ClaudeProvider(EvalProvider):
    """Anthropic SDK provider."""

    def __init__(self, api_key: str | None = None):
        try:
            import anthropic
        except (ImportError, TypeError):
            raise ImportError("Install `anthropic` to use Claude provider: pip install anthropic")
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def generate(self, prompt, system, model, temperature, num_ctx,
                 params, timeout, source, priority=2):
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": min(num_ctx, 4096),
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        try:
            response = self._client.messages.create(**kwargs)
            text = response.content[0].text if response.content else None
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            }
            return text, usage, None
        except Exception:
            _log.exception("Claude API call failed")
            return None, {}, None


class OpenAIProvider(EvalProvider):
    """OpenAI SDK provider (also works with OpenAI-compatible servers)."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        try:
            import openai
        except (ImportError, TypeError):
            raise ImportError("Install `openai` to use OpenAI provider: pip install openai")
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    def generate(self, prompt, system, model, temperature, num_ctx,
                 params, timeout, source, priority=2):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(num_ctx, 4096),
        }

        try:
            response = self._client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content if response.choices else None
            usage = {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            }
            return text, usage, None
        except Exception:
            _log.exception("OpenAI API call failed")
            return None, {}, None


def get_provider(
    provider_name: str,
    http_base: str = "http://127.0.0.1:7683",
    api_key: str | None = None,
    base_url: str | None = None,
) -> EvalProvider:
    """Factory function to get a provider instance."""
    if provider_name == "ollama":
        return OllamaProvider(http_base=http_base)
    elif provider_name == "claude":
        return ClaudeProvider(api_key=api_key)
    elif provider_name == "openai":
        return OpenAIProvider(api_key=api_key, base_url=base_url)
    else:
        raise ValueError(f"Unknown provider '{provider_name}'. Use: ollama, claude, openai")
```

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_eval_providers.py -v`
Expected: PASS (7/7)

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/eval/providers.py tests/test_eval_providers.py
git commit -m "feat(eval): add multi-provider abstraction (Ollama, Claude, OpenAI)"
```

---

### Task 5: Provider settings (backend)

**Files:**
- Modify: `ollama_queue/db/schema.py` (seed new settings)
- Modify: `ollama_queue/api/eval_settings.py` (mask API keys in GET, validate on PUT)
- Test: `tests/test_api_eval_settings.py`

**Step 1: Write the failing tests**

```python
def test_provider_settings_exist_after_init(client):
    """Provider settings should be seeded with defaults."""
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("eval.generator_provider") == "ollama"
    assert data.get("eval.judge_provider") == "ollama"
    assert data.get("eval.optimizer_provider") == "claude"
    assert data.get("eval.oracle_provider") == "claude"


def test_api_keys_masked_in_get(client_and_db):
    """API keys should be masked in GET responses."""
    client, db = client_and_db
    db.set_setting("eval.claude_api_key", "sk-ant-api03-realkey123456")
    resp = client.get("/api/eval/settings")
    data = resp.json()
    assert data.get("eval.claude_api_key") != "sk-ant-api03-realkey123456"
    assert "***" in data.get("eval.claude_api_key", "")


def test_set_invalid_provider_rejected(client):
    """PUT with invalid provider should return 400."""
    resp = client.put("/api/eval/settings", json={"eval.generator_provider": "invalid"})
    assert resp.status_code == 400
```

**Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_settings.py::test_provider_settings_exist_after_init tests/test_api_eval_settings.py::test_api_keys_masked_in_get tests/test_api_eval_settings.py::test_set_invalid_provider_rejected -v`
Expected: FAIL

**Step 3: Implement**

In `schema.py` settings seed section, add:

```python
            ("eval.generator_provider", "ollama"),
            ("eval.generator_model", ""),
            ("eval.judge_provider", "ollama"),
            ("eval.optimizer_provider", "claude"),
            ("eval.optimizer_model", "claude-sonnet-4-6"),
            ("eval.oracle_provider", "claude"),
            ("eval.oracle_model", "claude-sonnet-4-6"),
            ("eval.oracle_enabled", "false"),
            ("eval.claude_api_key", ""),
            ("eval.openai_api_key", ""),
            ("eval.openai_base_url", ""),
            ("eval.max_cost_per_run_usd", "1.00"),
```

In `api/eval_settings.py`, update the GET handler to mask keys and the PUT handler to validate provider values.

Mask pattern (same as existing `data_source_token`):

```python
_MASKED_SETTINGS = {"eval.data_source_token", "eval.claude_api_key", "eval.openai_api_key"}

# In GET handler:
for key in _MASKED_SETTINGS:
    val = settings.get(key, "")
    if val and len(val) > 6:
        settings[key] = val[:6] + "***"
    elif val:
        settings[key] = "***"

_PROVIDER_SETTINGS = {
    "eval.generator_provider", "eval.judge_provider",
    "eval.optimizer_provider", "eval.oracle_provider",
}
_VALID_PROVIDERS = {"ollama", "claude", "openai"}

# In PUT handler, validate provider values:
for key in _PROVIDER_SETTINGS:
    if key in body and body[key] not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider for {key}: must be one of {_VALID_PROVIDERS}")
```

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_settings.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/db/schema.py ollama_queue/api/eval_settings.py tests/test_api_eval_settings.py
git commit -m "feat(eval): add provider settings with API key masking and validation"
```

---

## Batch 3: API CRUD Updates

### Task 6: Update variant create endpoint

**Files:**
- Modify: `ollama_queue/api/eval_variants.py:228-268` (`create_eval_variant`)
- Test: `tests/test_api_eval_variants.py`

**Step 1: Write the failing tests**

```python
def test_create_variant_with_params(client):
    """POST with params should persist the JSON bag."""
    body = {
        "label": "Params test",
        "prompt_template_id": "zero-shot-causal",
        "model": "qwen2.5:7b",
        "params": {"top_k": 40, "top_p": 0.9},
    }
    resp = client.post("/api/eval/variants", json=body)
    assert resp.status_code == 201
    data = resp.json()
    params = json.loads(data["params"])
    assert params["top_k"] == 40
    assert params["top_p"] == 0.9


def test_create_variant_with_system_prompt(client):
    """POST with system_prompt should persist it."""
    body = {
        "label": "System prompt test",
        "prompt_template_id": "zero-shot-causal",
        "model": "qwen2.5:7b",
        "system_prompt": "Be precise and concise.",
    }
    resp = client.post("/api/eval/variants", json=body)
    assert resp.status_code == 201
    assert resp.json()["system_prompt"] == "Be precise and concise."


def test_create_variant_with_provider(client):
    """POST with provider should persist it."""
    body = {
        "label": "Claude variant",
        "prompt_template_id": "zero-shot-causal",
        "model": "claude-sonnet-4-6",
        "provider": "claude",
    }
    resp = client.post("/api/eval/variants", json=body)
    assert resp.status_code == 201
    assert resp.json()["provider"] == "claude"


def test_create_variant_invalid_params_returns_400(client):
    """POST with invalid Ollama param should return 400."""
    body = {
        "label": "Bad params",
        "prompt_template_id": "zero-shot-causal",
        "model": "qwen2.5:7b",
        "params": {"topk": 40},
    }
    resp = client.post("/api/eval/variants", json=body)
    assert resp.status_code == 400
    assert "top_k" in resp.json()["detail"]  # fuzzy suggestion


def test_create_variant_temperature_in_params_returns_400(client):
    """POST with temperature in params should return 400."""
    body = {
        "label": "Ambiguous params",
        "prompt_template_id": "zero-shot-causal",
        "model": "qwen2.5:7b",
        "params": {"temperature": 0.5},
    }
    resp = client.post("/api/eval/variants", json=body)
    assert resp.status_code == 400
```

**Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py::test_create_variant_with_params tests/test_api_eval_variants.py::test_create_variant_with_system_prompt tests/test_api_eval_variants.py::test_create_variant_with_provider tests/test_api_eval_variants.py::test_create_variant_invalid_params_returns_400 tests/test_api_eval_variants.py::test_create_variant_temperature_in_params_returns_400 -v`
Expected: FAIL

**Step 3: Update `create_eval_variant` in `api/eval_variants.py`**

Add import at top:
```python
from ollama_queue.eval.validation import validate_variant_params, validate_provider
```

Update the function body to accept and validate new fields, include them in INSERT.

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/api/eval_variants.py tests/test_api_eval_variants.py
git commit -m "feat(eval): wire params/system_prompt/provider into variant create endpoint"
```

---

### Task 7: Update variant update, clone, import, generate endpoints

**Files:**
- Modify: `ollama_queue/api/eval_variants.py` (update, clone, import, generate functions)
- Test: `tests/test_api_eval_variants.py`

**Step 1: Write the failing tests**

```python
def test_update_variant_params(client):
    """PUT with params should update the JSON bag."""
    create_resp = client.post("/api/eval/variants", json={
        "label": "Update test", "prompt_template_id": "zero-shot-causal", "model": "qwen2.5:7b",
    })
    var_id = create_resp.json()["id"]
    update_resp = client.put(f"/api/eval/variants/{var_id}", json={"params": {"top_k": 80}})
    assert update_resp.status_code == 200
    assert json.loads(update_resp.json()["params"])["top_k"] == 80


def test_clone_preserves_new_columns(client):
    """Clone should copy system_prompt, params, provider, training_config."""
    create_resp = client.post("/api/eval/variants", json={
        "label": "Clone source",
        "prompt_template_id": "zero-shot-causal",
        "model": "qwen2.5:7b",
        "system_prompt": "Be precise",
        "params": {"top_k": 40},
        "provider": "ollama",
    })
    var_id = create_resp.json()["id"]
    clone_resp = client.post(f"/api/eval/variants/{var_id}/clone")
    assert clone_resp.status_code == 201
    clone = clone_resp.json()
    assert clone["system_prompt"] == "Be precise"
    assert json.loads(clone["params"])["top_k"] == 40
    assert clone["provider"] == "ollama"


def test_import_includes_new_columns(client):
    """Import should persist system_prompt, params, provider."""
    payload = {
        "variants": [{
            "id": "imported-1",
            "label": "Imported",
            "prompt_template_id": "zero-shot-causal",
            "model": "qwen2.5:7b",
            "temperature": 0.6,
            "num_ctx": 8192,
            "system_prompt": "Imported system prompt",
            "params": '{"top_k": 20}',
            "provider": "openai",
        }],
        "templates": [],
    }
    resp = client.post("/api/eval/variants/import", json=payload)
    assert resp.json()["variants_imported"] == 1
    # Verify imported data
    variants = client.get("/api/eval/variants").json()
    imported = next(v for v in variants if v["id"] == "imported-1")
    assert imported["system_prompt"] == "Imported system prompt"
    assert imported["provider"] == "openai"


def test_generate_with_provider(client):
    """Bulk generate should accept provider parameter."""
    resp = client.post("/api/eval/variants/generate", json={
        "models": ["gpt-4o-mini"],
        "template_id": "zero-shot-causal",
        "provider": "openai",
    })
    assert resp.status_code == 200
    created = resp.json()["variants"]
    assert len(created) == 1
    assert created[0]["provider"] == "openai"
```

**Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py::test_update_variant_params tests/test_api_eval_variants.py::test_clone_preserves_new_columns tests/test_api_eval_variants.py::test_import_includes_new_columns tests/test_api_eval_variants.py::test_generate_with_provider -v`
Expected: FAIL

**Step 3: Update all four endpoints in `eval_variants.py`**

- `update_eval_variant`: Add `system_prompt`, `params`, `training_config`, `provider` to `updatable_fields`. Validate params on write.
- `clone_eval_variant`: Copy all 4 new columns from original.
- `import_eval_variants`: Read and persist new columns.
- `generate_eval_variants`: Accept `params`, `system_prompt`, `provider` from body, apply to all created variants.

**Step 4: Run full eval variant test suite**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_api_eval_variants.py -v`
Expected: ALL PASS (existing + new)

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/api/eval_variants.py tests/test_api_eval_variants.py
git commit -m "feat(eval): wire new columns into update/clone/import/generate endpoints"
```

---

## Batch 4: Proxy + Pipeline Integration

### Task 8: Wire extra_params and system_prompt into _call_proxy

**Files:**
- Modify: `ollama_queue/eval/engine.py:203-245` (`_call_proxy`)
- Test: `tests/test_eval_engine.py`

**Step 1: Write the failing test**

```python
def test_call_proxy_merges_extra_params(self):
    """_call_proxy should merge extra_params into options, flat columns winning."""
    with patch("ollama_queue.eval.engine.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "test"}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_httpx.Client.return_value = mock_client

        _call_proxy(
            "http://localhost:7683", "model", "prompt",
            temperature=0.6, num_ctx=8192, timeout=300, source="test",
            extra_params={"top_k": 40, "temperature": 999},  # temperature should be ignored
            system_prompt="Be precise",
        )
        body = mock_client.post.call_args[1]["json"]
        assert body["options"]["top_k"] == 40
        assert body["options"]["temperature"] == 0.6  # flat column wins
        assert body["system"] == "Be precise"
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_eval_engine.py::TestCallProxy::test_call_proxy_merges_extra_params -v`
Expected: FAIL — TypeError (unexpected keyword arguments)

**Step 3: Update `_call_proxy` signature and body**

Add `extra_params=None, system_prompt=None` parameters. Merge into options dict. Add `system` to body when present.

**Step 4: Run test to verify it passes**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_eval_engine.py -v -x`
Expected: PASS (new + all existing)

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/eval/engine.py tests/test_eval_engine.py
git commit -m "feat(eval): wire extra_params and system_prompt into _call_proxy"
```

---

### Task 9: Wire variant params through generate.py

**Files:**
- Modify: `ollama_queue/eval/generate.py:320-345` (`_generate_one_item`, `_self_critique`)
- Test: `tests/test_eval_engine.py` (integration-level test)

**Step 1: Write the failing test**

```python
def test_generate_one_item_passes_variant_params(self):
    """_generate_one_item should pass params and system_prompt from variant to proxy."""
    variant = {
        "id": "test-v",
        "model": "qwen2.5:7b",
        "temperature": 0.6,
        "num_ctx": 8192,
        "params": '{"top_k": 40}',
        "system_prompt": "Be concise",
        "prompt_template_id": "zero-shot-causal",
    }
    # ... mock _call_proxy, verify extra_params and system_prompt are passed
```

**Step 2-4: Implement and verify**

In `generate.py:_generate_one_item()`, update both proxy calls to include:
```python
extra_params=json.loads(variant.get("params") or "{}"),
system_prompt=variant.get("system_prompt"),
```

Same for `_self_critique()`.

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/eval/generate.py tests/test_eval_engine.py
git commit -m "feat(eval): pass variant params and system_prompt through generation pipeline"
```

---

### Task 10: Update analysis diff, report rendering, and promote payload

**Files:**
- Modify: `ollama_queue/eval/analysis.py:277-324` (`describe_config_diff`)
- Modify: `ollama_queue/eval/metrics.py:278-289` (`render_report` settings line)
- Modify: `ollama_queue/eval/promote.py:54-59` (promotion payload)
- Test: `tests/test_eval_engine.py`

**Step 1: Write the failing tests**

```python
def test_config_diff_detects_params_change():
    """describe_config_diff should report params changes."""
    from ollama_queue.eval.analysis import describe_config_diff
    a = {"model": "m", "temperature": 0.6, "num_ctx": 8192, "prompt_template_id": "t", "params": '{"top_k": 20}'}
    b = {"model": "m", "temperature": 0.6, "num_ctx": 8192, "prompt_template_id": "t", "params": '{"top_k": 40}'}
    diffs = describe_config_diff(a, b)
    assert any("top_k" in d for d in diffs)


def test_config_diff_detects_system_prompt_change():
    """describe_config_diff should report system_prompt changes."""
    from ollama_queue.eval.analysis import describe_config_diff
    a = {"model": "m", "temperature": 0.6, "num_ctx": 8192, "prompt_template_id": "t", "system_prompt": None}
    b = {"model": "m", "temperature": 0.6, "num_ctx": 8192, "prompt_template_id": "t", "system_prompt": "Be precise"}
    diffs = describe_config_diff(a, b)
    assert any("System prompt" in d for d in diffs)


def test_config_diff_detects_provider_change():
    """describe_config_diff should report provider changes."""
    from ollama_queue.eval.analysis import describe_config_diff
    a = {"model": "m", "temperature": 0.6, "num_ctx": 8192, "prompt_template_id": "t", "provider": "ollama"}
    b = {"model": "m", "temperature": 0.6, "num_ctx": 8192, "prompt_template_id": "t", "provider": "claude"}
    diffs = describe_config_diff(a, b)
    assert any("Provider" in d for d in diffs)
```

**Step 2-4: Implement and verify**

Add system_prompt, params (JSON key-by-key diff), provider, and training_config diff blocks to `describe_config_diff()`. Update `render_report()` settings line. Update `do_promote_eval_run()` payload.

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/eval/analysis.py ollama_queue/eval/metrics.py ollama_queue/eval/promote.py tests/test_eval_engine.py
git commit -m "feat(eval): update config diff, reports, and promote payload for new variant columns"
```

---

## Batch 5: Provider Test Endpoint + Full Suite

### Task 11: Provider test endpoint

**Files:**
- Modify: `ollama_queue/api/eval_settings.py`
- Test: `tests/test_api_eval_settings.py`

**Step 1: Write the failing test**

```python
def test_provider_test_ollama_success(client):
    """POST /api/eval/providers/test with ollama should succeed (mock proxy)."""
    with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
        mock.return_value = ("hello", {"prompt_tokens": 5}, None)
        resp = client.post("/api/eval/providers/test", json={
            "provider": "ollama",
            "model": "qwen2.5:7b",
        })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_provider_test_invalid_provider(client):
    """POST /api/eval/providers/test with unknown provider should return 400."""
    resp = client.post("/api/eval/providers/test", json={
        "provider": "invalid",
        "model": "test",
    })
    assert resp.status_code == 400
```

**Step 2-4: Implement and verify**

Add `POST /api/eval/providers/test` endpoint that instantiates the provider and sends a minimal prompt ("Say hello"). Returns `{"ok": true, "response_length": N}` on success.

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/api/eval_settings.py tests/test_api_eval_settings.py
git commit -m "feat(eval): add provider test endpoint for API key validation"
```

---

### Task 12: Update eval __init__.py exports + run full test suite

**Files:**
- Modify: `ollama_queue/eval/__init__.py`

**Step 1: Update exports**

Add `validation` and `providers` to the module exports.

**Step 2: Run full test suite**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest --timeout=120 -x -q`
Expected: ALL PASS (1588 existing + ~30 new ≈ 1618 total)

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/eval/__init__.py
git commit -m "feat(eval): export validation and providers from eval package"
```

---

## Post-Batch Quality Gate

Run before declaring Phase 1 complete:

```bash
cd ~/Documents/projects/ollama-queue

# Full test suite
python3 -m pytest --timeout=120 -x -q

# Lint
make lint

# Type check
make format

# Verify SPA still builds (no API contract break)
cd ollama_queue/dashboard/spa && npm run build && cd ../../..

# Verify existing eval endpoints still work
python3 -c "
from ollama_queue.db import Database
from ollama_queue.app import create_app
from fastapi.testclient import TestClient
db = Database(':memory:')
db.initialize()
app = create_app(db)
c = TestClient(app)
variants = c.get('/api/eval/variants').json()
assert len(variants) == 9, f'Expected 9 system variants, got {len(variants)}'
assert all('params' in v for v in variants), 'Missing params column'
assert all('provider' in v for v in variants), 'Missing provider column'
print(f'OK: {len(variants)} variants with new columns')
"
```

---

## Summary

| Batch | Tasks | New Tests | What It Delivers |
|-------|-------|-----------|------------------|
| 1 | Schema + Validation | ~15 | New columns, cache table, params validation |
| 2 | Provider Abstraction | ~7 | OllamaProvider, ClaudeProvider, OpenAIProvider, factory |
| 3 | API CRUD | ~9 | All variant endpoints handle new columns |
| 4 | Proxy + Pipeline | ~6 | Params flow through generation, diff, reports, promote |
| 5 | Provider Test + Suite | ~3 | Provider test endpoint, full regression pass |
| **Total** | **12 tasks** | **~40 tests** | **Phase 1 complete** |
