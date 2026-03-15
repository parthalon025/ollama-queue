# Eval Model Validation — Fix & Prevent

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all eval runs failing due to missing models, and add validation to prevent it from happening again.

**Architecture:** Three layers of defense: (1) immediate data fix via API/SQL, (2) write-time validation on settings and variant endpoints that checks `OllamaModels.list_local()` for `provider=ollama`, (3) pre-flight check in `run_eval_session` before burning compute. Hardcoded model defaults removed — settings must be explicit.

**Tech Stack:** Python, FastAPI, SQLite, httpx, pytest

---

## Batch 1: Data Fix — Update Settings & Variants to Installed Models

### Task 1: Update eval settings to reference installed models

**Files:**
- None (API calls only)

**Step 1: Update `eval.judge_model` and `eval.analysis_model`**

```bash
curl -s -X PUT http://127.0.0.1:7683/api/eval/settings \
  -H 'Content-Type: application/json' \
  -d '{"eval.judge_model": "qwen3.5:9b", "eval.analysis_model": "qwen3.5:9b"}' | python3 -m json.tool
```

Expected: 200 OK, updated settings returned.

**Step 2: Verify settings persisted**

```bash
curl -s http://127.0.0.1:7683/api/eval/settings | python3 -c "
import json, sys; s = json.load(sys.stdin)
print('judge_model:', s['eval.judge_model'])
print('analysis_model:', s['eval.analysis_model'])
"
```

Expected: Both show `qwen3.5:9b`.

### Task 2: Update system variant models via direct SQL

System variants (A, B, C, F, H + copies) use `deepseek-r1:8b` which is not installed.
The PUT API rejects system variant edits, so direct SQL is required.
Replace with `qwen3.5:9b` — installed reasoning model of similar capability.

**Files:**
- None (direct DB update on production DB)

**Step 1: Update all `deepseek-r1:8b` variants**

```bash
sqlite3 /home/justin/.local/share/ollama-queue/queue.db \
  "UPDATE eval_variants SET model = 'qwen3.5:9b' WHERE model = 'deepseek-r1:8b';"
```

**Step 2: Verify update**

```bash
sqlite3 /home/justin/.local/share/ollama-queue/queue.db \
  "SELECT id, model, label FROM eval_variants WHERE model = 'deepseek-r1:8b';"
```

Expected: No rows returned (all updated).

```bash
sqlite3 /home/justin/.local/share/ollama-queue/queue.db \
  "SELECT id, model, label FROM eval_variants WHERE model = 'qwen3.5:9b';"
```

Expected: All previously `deepseek-r1:8b` variants now show `qwen3.5:9b`.

### Task 3: Smoke-test an eval run

**Step 1: Trigger a quick eval run via API**

```bash
curl -s -X POST http://127.0.0.1:7683/api/eval/runs \
  -H 'Content-Type: application/json' \
  -d '{"variants": ["M"], "per_cluster": 2}' | python3 -m json.tool
```

**Step 2: Watch progress for 60s**

```bash
RUN_ID=$(curl -s http://127.0.0.1:7683/api/eval/runs | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")
for i in $(seq 1 6); do
  sleep 10
  curl -s "http://127.0.0.1:7683/api/eval/runs/$RUN_ID" | python3 -c "
import json, sys; r = json.load(sys.stdin)
print(f'status={r[\"status\"]} error={r.get(\"error\",\"none\")}')
"
done
```

Expected: Status progresses from `generating` → `judging` → `complete`, no `proxy_unavailable` errors.

**Step 3: Commit data fix documentation**

No code committed — this is an operational fix. Proceed to Batch 2.

---

## Batch 2: Model Validation Helper + Settings Validation

### Task 4: Write failing test for model validation in settings

**Files:**
- Test: `tests/test_api_eval_settings.py`
- Create helper: `ollama_queue/eval/validation.py` (extend existing)

**Step 1: Write the failing test**

Add to `tests/test_api_eval_settings.py`:

```python
def test_put_eval_settings_rejects_missing_ollama_model(client, mock_db):
    """Settings with provider=ollama must reference an installed model."""
    with patch("ollama_queue.api.eval_settings._installed_ollama_models", return_value={"qwen3.5:9b", "qwen3:14b"}):
        resp = client.put("/api/eval/settings", json={
            "eval.judge_model": "nonexistent-model:7b",
        })
    assert resp.status_code == 422
    assert "not installed" in resp.json()["detail"].lower()


def test_put_eval_settings_accepts_installed_ollama_model(client, mock_db):
    """Settings referencing an installed model should succeed."""
    with patch("ollama_queue.api.eval_settings._installed_ollama_models", return_value={"qwen3.5:9b", "qwen3:14b"}):
        resp = client.put("/api/eval/settings", json={
            "eval.judge_model": "qwen3.5:9b",
        })
    assert resp.status_code == 200


def test_put_eval_settings_skips_model_check_for_empty_string(client, mock_db):
    """Empty model string is valid (means 'use default')."""
    with patch("ollama_queue.api.eval_settings._installed_ollama_models", return_value={"qwen3.5:9b"}):
        resp = client.put("/api/eval/settings", json={
            "eval.generator_model": "",
        })
    assert resp.status_code == 200
```

**Step 2: Run tests to verify they fail**

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_api_eval_settings.py -k "missing_ollama_model or accepts_installed or skips_model_check" -v
```

Expected: FAIL — no validation logic exists yet.

### Task 5: Implement `_installed_ollama_models` helper and settings validation

**Files:**
- Modify: `ollama_queue/api/eval_settings.py`

**Step 1: Add helper function**

At the top of `eval_settings.py`, after imports, add:

```python
from ollama_queue.models.client import OllamaModels


def _installed_ollama_models() -> set[str]:
    """Return set of locally installed Ollama model names (e.g. {'qwen3.5:9b', 'qwen3:14b'})."""
    return {m["name"].removesuffix(":latest") for m in OllamaModels.list_local()}
```

**Step 2: Add validation to `put_eval_settings`**

In the `put_eval_settings` function, after the existing validation loop, before writing to DB, add:

```python
    # Validate Ollama model references exist locally
    _MODEL_SETTINGS = {"judge_model", "analysis_model", "generator_model"}
    judge_provider = body.get("eval.judge_provider") or _eng._get_eval_setting(db, "eval.judge_provider", "ollama")
    generator_provider = body.get("eval.generator_provider") or _eng._get_eval_setting(db, "eval.generator_provider", "ollama")

    provider_for_setting = {
        "judge_model": judge_provider,
        "analysis_model": judge_provider,  # analysis uses judge provider
        "generator_model": generator_provider,
    }

    installed = None  # lazy — only fetch if needed
    for key, value in body.items():
        bare = key.removeprefix("eval.")
        if bare in _MODEL_SETTINGS and value and provider_for_setting.get(bare) == "ollama":
            if installed is None:
                installed = _installed_ollama_models()
            # Normalize: strip :latest suffix for comparison
            check_name = value.removesuffix(":latest")
            if check_name not in installed:
                validation_errors.append(
                    f"{key}={value!r} is not installed in Ollama. "
                    f"Installed models: {', '.join(sorted(installed))}"
                )
```

**Step 3: Run tests to verify they pass**

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_api_eval_settings.py -k "missing_ollama_model or accepts_installed or skips_model_check" -v
```

Expected: All 3 PASS.

**Step 4: Commit**

```bash
git add ollama_queue/api/eval_settings.py tests/test_api_eval_settings.py
git commit -m "feat(eval): validate Ollama model existence in settings PUT"
```

---

## Batch 3: Variant Model Validation

### Task 6: Write failing tests for variant model validation

**Files:**
- Test: `tests/test_api_eval_variants.py`
- Modify: `ollama_queue/api/eval_variants.py`

**Step 1: Write failing tests**

Add to `tests/test_api_eval_variants.py`:

```python
def test_create_variant_rejects_missing_ollama_model(client, mock_db):
    """Creating a variant with provider=ollama must reference an installed model."""
    with patch("ollama_queue.api.eval_variants._installed_ollama_models", return_value={"qwen3.5:9b"}):
        resp = client.post("/api/eval/variants", json={
            "label": "Test",
            "model": "nonexistent:7b",
            "prompt_template_id": "fewshot",
            "provider": "ollama",
        })
    assert resp.status_code == 422
    assert "not installed" in resp.json()["detail"].lower()


def test_create_variant_accepts_claude_model_without_ollama_check(client, mock_db):
    """provider=claude skips Ollama model existence check."""
    with patch("ollama_queue.api.eval_variants._installed_ollama_models", return_value=set()):
        resp = client.post("/api/eval/variants", json={
            "label": "Claude Test",
            "model": "claude-sonnet-4-6",
            "prompt_template_id": "fewshot",
            "provider": "claude",
        })
    # Should not fail due to model check (may fail for other reasons in mock)
    assert resp.status_code != 422 or "not installed" not in resp.json().get("detail", "").lower()


def test_update_variant_rejects_missing_ollama_model(client, mock_db):
    """Updating variant model to non-existent Ollama model is rejected."""
    # First create a user variant, then try to update its model
    with patch("ollama_queue.api.eval_variants._installed_ollama_models", return_value={"qwen3.5:9b"}):
        create_resp = client.post("/api/eval/variants", json={
            "label": "Updatable",
            "model": "qwen3.5:9b",
            "prompt_template_id": "fewshot",
            "provider": "ollama",
        })
    vid = create_resp.json().get("id", "test-id")
    with patch("ollama_queue.api.eval_variants._installed_ollama_models", return_value={"qwen3.5:9b"}):
        resp = client.put(f"/api/eval/variants/{vid}", json={
            "model": "nonexistent:13b",
        })
    assert resp.status_code == 422
    assert "not installed" in resp.json()["detail"].lower()
```

**Step 2: Run tests to verify they fail**

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_api_eval_variants.py -k "rejects_missing_ollama or accepts_claude" -v
```

Expected: FAIL.

### Task 7: Implement variant model validation

**Files:**
- Modify: `ollama_queue/api/eval_variants.py`

**Step 1: Add helper import and function**

At the top of `eval_variants.py`, add:

```python
from ollama_queue.models.client import OllamaModels


def _installed_ollama_models() -> set[str]:
    """Return set of locally installed Ollama model names."""
    return {m["name"].removesuffix(":latest") for m in OllamaModels.list_local()}


def _validate_model_installed(model: str, provider: str) -> None:
    """Raise HTTPException if provider=ollama and model is not installed."""
    if provider != "ollama" or not model:
        return
    installed = _installed_ollama_models()
    check_name = model.removesuffix(":latest")
    if check_name not in installed:
        raise HTTPException(
            status_code=422,
            detail=f"Model {model!r} is not installed in Ollama. "
                   f"Installed: {', '.join(sorted(installed))}",
        )
```

**Step 2: Add validation call to `create_eval_variant`**

In the `create_eval_variant` function, after existing validation (label, model, prompt_template_id checks) and before the INSERT, add:

```python
        _validate_model_installed(model, provider)
```

**Step 3: Add validation call to `update_eval_variant`**

In the `update_eval_variant` function, after building the `updates` dict and before the UPDATE SQL, add:

```python
        if "model" in updates:
            update_provider = updates.get("provider") or variant.get("provider", "ollama")
            _validate_model_installed(updates["model"], update_provider)
```

**Step 4: Run tests to verify they pass**

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_api_eval_variants.py -k "rejects_missing_ollama or accepts_claude or rejects_missing" -v
```

Expected: All PASS.

**Step 5: Commit**

```bash
git add ollama_queue/api/eval_variants.py tests/test_api_eval_variants.py
git commit -m "feat(eval): validate Ollama model existence in variant create/update"
```

---

## Batch 4: Pre-flight Check + Remove Hardcoded Defaults

### Task 8: Write failing test for pre-flight model check in run_eval_session

**Files:**
- Test: `tests/test_eval_engine.py`
- Modify: `ollama_queue/eval/engine.py`

**Step 1: Write the failing test**

Add to `tests/test_eval_engine.py`:

```python
def test_run_eval_session_fails_fast_on_missing_model(mock_db):
    """Eval session should fail immediately if variant models aren't installed."""
    # Setup: create a run referencing a variant with a missing model
    # The session should check models before starting generate phase
    with patch("ollama_queue.eval.engine.OllamaModels") as MockModels:
        MockModels.list_local.return_value = [{"name": "qwen3.5:9b"}]
        # run references variant with model="nonexistent:7b"
        run_eval_session(run_id, mock_db)

    run = get_eval_run(mock_db, run_id)
    assert run["status"] == "failed"
    assert "not installed" in run["error"].lower()
```

Note: Exact test setup depends on existing test fixtures — the implementing agent should adapt to match the test patterns already in `test_eval_engine.py`.

**Step 2: Run test to verify it fails**

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_eval_engine.py -k "missing_model" -v
```

Expected: FAIL.

### Task 9: Implement pre-flight model check in run_eval_session

**Files:**
- Modify: `ollama_queue/eval/engine.py`

**Step 1: Add pre-flight check at start of `run_eval_session`**

At the beginning of `run_eval_session`, after fetching the run and before calling `run_eval_generate`, add:

```python
    # Pre-flight: verify all referenced models are installed
    from ollama_queue.models.client import OllamaModels
    installed = {m["name"].removesuffix(":latest") for m in OllamaModels.list_local()}

    variant_ids = json.loads(run.get("variants", "[]")) if isinstance(run.get("variants"), str) else run.get("variants", [])
    missing = []
    for vid in variant_ids:
        variant = get_eval_variant(db, vid)
        if variant and variant.get("provider", "ollama") == "ollama":
            model = variant["model"].removesuffix(":latest")
            if model not in installed:
                missing.append(f"variant {vid}: {variant['model']}")

    judge_model = run.get("judge_model") or _get_eval_setting(db, "eval.judge_model", "")
    judge_provider = _get_eval_setting(db, "eval.judge_provider", "ollama")
    if judge_provider == "ollama" and judge_model:
        if judge_model.removesuffix(":latest") not in installed:
            missing.append(f"judge: {judge_model}")

    if missing:
        error_msg = f"Models not installed in Ollama: {'; '.join(missing)}"
        _log.error("pre-flight failed for run %d: %s", run_id, error_msg)
        update_eval_run(db, run_id, status="failed", error=error_msg,
                        completed_at=datetime.now(UTC).isoformat())
        return
```

**Step 2: Run test to verify it passes**

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_eval_engine.py -k "missing_model" -v
```

Expected: PASS.

**Step 3: Commit**

```bash
git add ollama_queue/eval/engine.py tests/test_eval_engine.py
git commit -m "feat(eval): pre-flight model check in run_eval_session"
```

### Task 10: Remove hardcoded `deepseek-r1:8b` default from judge.py and promote.py

**Files:**
- Modify: `ollama_queue/eval/judge.py:638`
- Modify: `ollama_queue/eval/promote.py:379`
- Test: `tests/test_eval_judge.py`, `tests/test_eval_promote.py`

**Step 1: Write failing test — judge fails explicitly when no model configured**

Add to relevant test file:

```python
def test_judge_fails_when_no_judge_model_configured(mock_db):
    """Judge must not silently fall back to a hardcoded model."""
    # Setup: run with no judge_model, setting eval.judge_model also empty
    # Expect: run fails with clear error about missing judge model
    ...
```

**Step 2: Change judge.py:638**

From:
```python
judge_model: str = run.get("judge_model") or _eng._get_eval_setting(db, "eval.judge_model", "deepseek-r1:8b")
```

To:
```python
judge_model: str = run.get("judge_model") or _eng._get_eval_setting(db, "eval.judge_model", "")
if not judge_model:
    _eng.update_eval_run(db, run_id, status="failed",
                         error="No judge model configured — set eval.judge_model in settings",
                         completed_at=datetime.now(UTC).isoformat())
    return
```

**Step 3: Change promote.py:379**

From:
```python
        or _eng._get_eval_setting(db, "eval.judge_model", "deepseek-r1:8b")
```

To:
```python
        or _eng._get_eval_setting(db, "eval.judge_model", "")
```

Add a guard after the resolution:
```python
    if not analysis_model:
        _log.warning("No analysis model configured — skipping analysis for run %d", run_id)
        return ""
```

**Step 4: Run affected tests**

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_eval_judge.py tests/test_eval_promote.py -v --timeout=120
```

Expected: All PASS (existing tests should already set judge_model explicitly).

**Step 5: Commit**

```bash
git add ollama_queue/eval/judge.py ollama_queue/eval/promote.py tests/test_eval_judge.py tests/test_eval_promote.py
git commit -m "fix(eval): remove hardcoded deepseek-r1:8b default, require explicit config"
```

---

## Batch 5: Full Test Suite + Lesson Capture

### Task 11: Run full test suite

```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/ --timeout=120 -x -q
```

Expected: All ~1,788 tests pass. Fix any regressions from new validation.

### Task 12: Capture lesson

```bash
lessons-db capture \
  --title "Eval settings and variants must validate Ollama model existence at write time" \
  --category "configuration" \
  --description "Eval runs failed for weeks because settings referenced models not installed in Ollama (gpt-oss:20b, deepseek-r1:8b). The proxy returned 504 timeouts instead of a clear 'model not found' error. Validation was deferred to runtime with no pre-flight check." \
  --detection "Settings or variant endpoints accept model strings without checking OllamaModels.list_local()" \
  --correction "Add _installed_ollama_models() check to PUT /api/eval/settings, POST/PUT /api/eval/variants, and run_eval_session pre-flight" \
  --files "ollama_queue/api/eval_settings.py,ollama_queue/api/eval_variants.py,ollama_queue/eval/engine.py"
```
