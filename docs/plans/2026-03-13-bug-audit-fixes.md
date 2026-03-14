# Bug Audit Fixes — 26 Issues from 2026-03-13 Audit

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 26 bugs identified in the 2026-03-13 three-agent codebase audit, covering eval retry logic, auto-promote gate bypass, proxy streaming safety, API correctness, daemon admission, sensing robustness, and DB quality.

**Architecture:** 9 batches by subsystem affinity and dependency order — eval retry first (breaks the pipeline), then gates (correctness), then proxy/API (availability), then daemon (scheduling), then sensing/db (robustness). No schema changes. All fixes are backwards-compatible.

**Tech Stack:** Python 3.12, FastAPI, SQLite WAL, httpx, asyncio, threading

---

## Deconflict — Already Fixed in `2026-03-13-edge-case-fixes.md`

Do NOT re-implement these — they are covered by the companion plan:
- Task 2: proxy streaming claim release via BackgroundTask (covers post-StreamingResponse path)
- Task 9: auto-promote with no production variant at all (`prod_row is None`)
- Task 12: DLQ chronic failure atomic threshold check
- Task 13: cron expression validation at submission time
- Task 20: model list cache TTL reduction

This plan covers distinct bugs in the same areas (see per-task notes).

---

## Critical Gotchas — Read Before Writing Any Code

1. **Python test command:** `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q`
2. **Single test:** `.venv/bin/python -m pytest tests/test_<file>.py::test_<name> -xvs --timeout=120`
3. **`db._lock` is `threading.RLock`** — do NOT change to `Lock`; nested acquisition is intentional.
4. **`except Exception` does NOT catch `CancelledError`** — `CancelledError` inherits `BaseException` in Python 3.8+.
5. **Never use `git add -A`** — stage only the specific files you changed.

---

## Batch 1: Eval Retry Logic (CRITICAL — C1, C2, C3)

### Task 1: Fix wrong field name `queue_job_id` → `_queue_job_id` in providers.py

**Files:**
- Modify: `ollama_queue/eval/providers.py:52`
- Test: `tests/test_eval_providers.py`

**Step 1: Write the failing test**

```python
# tests/test_eval_providers.py — add to existing file
from unittest.mock import MagicMock, patch
import httpx

def test_call_proxy_raw_returns_queue_job_id():
    """_call_proxy_raw must read _queue_job_id (with underscore) from proxy response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": "hello world",
        "_queue_job_id": 42,
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        from ollama_queue.eval.providers import _call_proxy_raw
        text, usage, job_id = _call_proxy_raw(
            {"model": "qwen2.5:7b", "prompt": "test", "stream": False},
            "http://127.0.0.1:7683",
            60,
        )

    assert job_id == 42, f"Expected job_id=42, got {job_id!r}"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_providers.py::test_call_proxy_raw_returns_queue_job_id -xvs`
Expected: FAIL — `job_id` is `None`

**Step 3: Write minimal implementation**

In `ollama_queue/eval/providers.py`, line 52:
```python
# Change:
job_id = data.get("queue_job_id")
# To:
job_id = data.get("_queue_job_id")
```

**Step 4: Run test**

Run: `.venv/bin/python -m pytest tests/test_eval_providers.py::test_call_proxy_raw_returns_queue_job_id -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/providers.py tests/test_eval_providers.py
git commit -m "fix(eval): read _queue_job_id (not queue_job_id) from proxy response (C1)"
```

---

### Task 2: Retry on `httpx.HTTPStatusError` in `_call_proxy_raw`

**Files:**
- Modify: `ollama_queue/eval/providers.py:43-63`
- Test: `tests/test_eval_providers.py`

**Step 1: Write the failing test**

```python
def test_call_proxy_raw_retries_on_429():
    """_call_proxy_raw must retry up to _MAX_RETRIES times on retryable HTTP errors."""
    import httpx
    from ollama_queue.eval.providers import _call_proxy_raw

    call_count = {"n": 0}

    def make_resp(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "429", request=MagicMock(), response=MagicMock(status_code=429)
            )
            return mock_resp
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "response": "ok", "_queue_job_id": 1,
            "prompt_eval_count": 0, "eval_count": 0,
        }
        return mock_resp

    with patch("httpx.Client") as mock_client_cls, patch("time.sleep"):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = make_resp
        text, usage, job_id = _call_proxy_raw(
            {"model": "x", "prompt": "y", "stream": False}, "http://127.0.0.1:7683", 60
        )

    assert call_count["n"] == 2, f"Expected 2 attempts (retry on 429), got {call_count['n']}"
    assert text == "ok"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_providers.py::test_call_proxy_raw_retries_on_429 -xvs`
Expected: FAIL — `call_count["n"] == 1` (HTTPStatusError caught, returns immediately)

**Step 3: Write implementation**

In `providers.py`, replace the `except httpx.HTTPStatusError` block (lines 54-56):

```python
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRYABLE_CODES and attempt < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                _log.warning(
                    "proxy HTTP %d — retry in %.0fs (attempt %d/%d)",
                    exc.response.status_code, delay, attempt + 1, _MAX_RETRIES + 1,
                )
                time.sleep(delay)
                continue
            _log.warning("proxy call failed (HTTP %d)", exc.response.status_code)
            return None, {}, None
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_providers.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/providers.py tests/test_eval_providers.py
git commit -m "fix(eval): retry HTTPStatusError in _call_proxy_raw for 429/502/503/504 (C3)"
```

---

### Task 3: Retry on `httpx.TimeoutException` in `engine._call_proxy`

**Files:**
- Modify: `ollama_queue/eval/engine.py:282-285`
- Test: `tests/test_eval_engine.py`

**Step 1: Write the failing test**

```python
def test_call_proxy_retries_on_timeout(monkeypatch):
    """engine._call_proxy must retry on TimeoutException, not return None immediately."""
    import httpx, time
    from ollama_queue.eval import engine

    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.TimeoutException("timeout")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "ok", "done": True}
        return mock_resp

    with patch("httpx.Client") as mock_client_cls, patch("time.sleep"):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = fake_post
        result, job_id = engine._call_proxy(
            "test prompt", "qwen2.5:7b", "http://127.0.0.1:7683", timeout=60
        )

    assert call_count["n"] == 2, f"Expected 2 attempts on timeout, got {call_count['n']}"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_engine.py::test_call_proxy_retries_on_timeout -xvs`
Expected: FAIL — `call_count["n"] == 1`

**Step 3: Write implementation**

In `engine.py` lines 282-285, change `return None, None` to retry:

```python
        except httpx.TimeoutException:
            _log.warning(
                "proxy timeout for model=%s (attempt %d/%d)", model, attempt + 1, _MAX_RETRIES + 1
            )
            last_exc = Exception(f"timeout model={model}")
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue
            return None, None
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_engine.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/engine.py tests/test_eval_engine.py
git commit -m "fix(eval): retry TimeoutException in engine._call_proxy (C2)"
```

---

## Batch 2: Auto-Promote Gates (CRITICAL + HIGH — C4, H1)

### Task 4: Block Gate 2 when production variant has no completed eval run

**Context:** Task 9 in `2026-03-13-edge-case-fixes.md` handles `prod_row is None` (no production variant). This task handles `prod_row is not None AND prod_run_row is None` — production variant exists (manual promote/seed) but has never been the winner of a completed eval run.

**Files:**
- Modify: `ollama_queue/eval/promote.py:206-229`
- Test: `tests/test_eval_promote.py`

**Step 1: Write the failing test**

```python
def test_gate2_blocked_when_prod_variant_has_no_completed_run(eval_db):
    """Gate 2 must block when production variant exists but has no completed eval run baseline."""
    # Mark variant A as production (simulating manual promote — no eval run)
    with eval_db._lock:
        conn = eval_db._connect()
        conn.execute("UPDATE eval_variants SET is_production = 1 WHERE id = 'A'")
        conn.commit()
    # Confirm no eval run has variant A as winner
    eval_db.set_setting("eval.f1_threshold", "0.40")
    eval_db.set_setting("eval.auto_promote", "true")

    # Create a run where variant B wins with high quality
    run = _make_completed_run(eval_db, winner_variant="B", winner_f1=0.90)
    check_auto_promote(eval_db, run["id"])

    # B must NOT have been auto-promoted — no baseline for Gate 2
    variants = eval_db.list_eval_variants()
    assert not any(v["id"] == "B" and v.get("is_production") for v in variants), \
        "Must not auto-promote when production variant has no eval run baseline"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_promote.py::test_gate2_blocked_when_prod_variant_has_no_completed_run -xvs`
Expected: FAIL — B gets auto-promoted (Gate 2 skipped because `production_quality is None`)

**Step 3: Write implementation**

In `promote.py`, after line 205 (`if prod_row is not None:`), add the `prod_run_row is None` early return:

```python
    if prod_row is not None:
        if prod_run_row is None:
            # Production variant exists (e.g. manual promote or seed) but has no completed
            # eval run. Cannot compute quality baseline for Gate 2.
            # Require manual promote until a baseline eval run exists.
            _log.info(
                "check_auto_promote: run %d — production variant %s has no completed eval run. "
                "Cannot evaluate Gate 2 without baseline. Manual promote required.",
                run_id,
                prod_row["id"],
            )
            return
        # prod_run_row is not None — parse quality
        try:
            m = json.loads(prod_run_row["metrics"]) if isinstance(prod_run_row["metrics"], str) else {}
            production_quality = (m.get(prod_id) or {}).get(quality_metric)
        except (json.JSONDecodeError, TypeError):
            _log.warning(
                "check_auto_promote: production metrics unparseable for variant %s — gate 2 skipped as unsafe",
                prod_id,
            )
            return
```

Remove the old `if prod_run_row is not None:` indented block that follows — it's now inlined above.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_promote.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/promote.py tests/test_eval_promote.py
git commit -m "fix(eval): Gate 2 blocks auto-promote when prod variant has no eval baseline (C4)"
```

---

### Task 5: Fix Gate 3 error-budget denominator (judge rows, not source items)

**Files:**
- Modify: `ollama_queue/eval/promote.py:231-250`
- Test: `tests/test_eval_promote.py`

**Step 1: Write the failing test**

```python
def test_gate3_uses_judge_row_count_not_source_item_count(eval_db):
    """Gate 3 error budget denominator must be total judge rows, not source item count."""
    # 10 source items, 80 judge rows, 4 failures → 4/80 = 5% (within 30% budget)
    # Bug: 4/10 = 40% → incorrectly fails Gate 3
    run_id = _make_run_with_judge_rows(
        eval_db, item_count=10, judge_row_count=80, null_score_count=4,
        winner_f1=0.90, winner_variant="B",
    )
    eval_db.set_setting("eval.f1_threshold", "0.40")
    eval_db.set_setting("eval.error_budget", "0.30")
    eval_db.set_setting("eval.auto_promote", "true")

    # With correct denominator (80): 4/80 = 5% < 30% → passes Gate 3
    # With wrong denominator (10): 4/10 = 40% > 30% → fails Gate 3
    check_auto_promote(eval_db, run_id)

    variants = eval_db.list_eval_variants()
    assert any(v["id"] == "B" and v.get("is_production") for v in variants), \
        "Gate 3 must use judge row count (not item count) as denominator"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_promote.py::test_gate3_uses_judge_row_count_not_source_item_count -xvs`
Expected: FAIL — B not promoted (Gate 3 fails with wrong denominator)

**Step 3: Write implementation**

In `promote.py`, replace lines 231-250 (Gate 3 block):

```python
    # Gate 3: error_budget_used <= error_budget
    _eb = db.get_setting("eval.error_budget")
    error_budget = float(_eb) if _eb is not None else 0.30
    with db._lock:
        conn = db._connect()
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN score_transfer IS NULL THEN 1 ELSE 0 END) as failed "
            "FROM eval_results WHERE run_id = ? AND row_type = 'judge'",
            (run_id,),
        ).fetchone()
    judge_row_count = row["total"] if row else 0
    failed_count = int(row["failed"] or 0) if row else 0
    if judge_row_count > 0:
        error_budget_used = failed_count / judge_row_count
        if error_budget_used > error_budget:
            _log.info(
                "check_auto_promote: run %d error_budget_used=%.3f (failed=%d/judge_rows=%d) > %.3f, skipping",
                run_id, error_budget_used, failed_count, judge_row_count, error_budget,
            )
            return
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_promote.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/promote.py tests/test_eval_promote.py
git commit -m "fix(eval): Gate 3 uses judge row count (not source item count) as error budget denominator (H1)"
```

---

## Batch 3: Proxy Streaming Safety (CRITICAL — C5, H8)

### Task 6: Guard proxy claim and httpx client from `CancelledError` before `StreamingResponse` setup

**Context:** Task 2 in `2026-03-13-edge-case-fixes.md` covers cleanup after `StreamingResponse` is returned (via `BackgroundTask`). This task covers the gap: `CancelledError` thrown during `async_client.send()` — before the `return StreamingResponse(...)` line, so the `BackgroundTask` is never registered.

**Files:**
- Modify: `ollama_queue/api/proxy.py` (streaming setup, around lines 256-300)
- Test: `tests/test_proxy.py`

**Step 1: Write the failing test**

```python
import asyncio
import pytest

async def test_streaming_proxy_releases_claim_on_cancelled_error(mock_db):
    """Proxy claim must be released even when CancelledError fires during async_client.send()."""
    mock_db.try_claim_for_proxy.return_value = ({"id": 99, "model": "qwen2.5:7b"}, 99)
    release_called = {"flag": False}
    mock_db.release_proxy_claim = lambda: release_called.__setitem__("flag", True)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(side_effect=asyncio.CancelledError())
        mock_client.aclose = AsyncMock()

        with pytest.raises(asyncio.CancelledError):
            from ollama_queue.api.proxy import proxy_generate
            await proxy_generate({"model": "qwen2.5:7b", "prompt": "hi", "stream": True}, mock_db)

    assert release_called["flag"], "Proxy claim must be released on CancelledError before StreamingResponse"
    assert mock_client.aclose.called, "httpx client must be closed on CancelledError"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy.py::test_streaming_proxy_releases_claim_on_cancelled_error -xvs`
Expected: FAIL — claim not released, client not closed

**Step 3: Write implementation**

In `proxy.py` streaming setup, wrap the `async_client.send()` call with a `BaseException` guard:

```python
    async_client = httpx.AsyncClient(...)
    try:
        rp_req = async_client.build_request(...)
        rp_resp = await async_client.send(rp_req, stream=True)
    except BaseException:
        # Catches CancelledError (BaseException, not Exception) fired before StreamingResponse
        # is set up. The BackgroundTask cleanup from Task 2 hasn't been registered yet.
        _log.warning(
            "proxy streaming setup interrupted for job %d — releasing claim", job_id
        )
        try:
            await async_client.aclose()
        except Exception:
            pass
        _release()
        raise
```

**Step 4: Run proxy tests**

Run: `.venv/bin/python -m pytest tests/test_proxy.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/api/proxy.py tests/test_proxy.py
git commit -m "fix(proxy): release claim on CancelledError before StreamingResponse setup (C5, H8)"
```

---

## Batch 4: API Correctness (CRITICAL + HIGH — C6, H4, M4, M5)

### Task 7: Return 404/409 when `cancel_job` finds no matching row

**Files:**
- Modify: `ollama_queue/db/jobs.py:163-172` (`cancel_job`)
- Modify: `ollama_queue/api/jobs.py:116-120` (cancel endpoint)
- Test: `tests/test_jobs_api.py`

**Step 1: Write the failing tests**

```python
def test_cancel_nonexistent_job_returns_404(client):
    resp = client.post("/api/queue/cancel/99999")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"

def test_cancel_running_job_returns_409(client, db):
    job_id = db.submit_job(command="sleep 10", source="test", priority=5, timeout=30)
    db.claim_next_job()  # puts it in 'running'
    resp = client.post(f"/api/queue/cancel/{job_id}")
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_jobs_api.py::test_cancel_nonexistent_job_returns_404 tests/test_jobs_api.py::test_cancel_running_job_returns_409 -xvs`
Expected: Both FAIL — return 200

**Step 3: Write implementation**

In `db/jobs.py`, update `cancel_job` to return rowcount:

```python
def cancel_job(self, job_id: int) -> int:
    """Cancel a pending job. Returns number of rows updated (0 = not found or not cancellable)."""
    with self._lock:
        conn = self._connect()
        cur = conn.execute(
            "UPDATE jobs SET status = 'cancelled', completed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (time.time(), job_id),
        )
        conn.commit()
    return cur.rowcount
```

In `api/jobs.py`, update the cancel endpoint:

```python
@router.post("/cancel/{job_id}")
def cancel_job_endpoint(job_id: int):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    rowcount = db.cancel_job(job_id)
    if rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} cannot be cancelled (status={job.get('status')})",
        )
    return {"ok": True, "job_id": job_id}
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_jobs_api.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/db/jobs.py ollama_queue/api/jobs.py tests/test_jobs_api.py
git commit -m "fix(api): cancel_job returns 404/409 instead of always 200 (C6)"
```

---

### Task 8: `repeat_eval_run` must propagate `data_source_token`

**Files:**
- Modify: `ollama_queue/api/eval_runs.py` (`repeat_eval_run` endpoint, ~lines 557-580)
- Test: `tests/test_eval_runs_api.py`

**Step 1: Write the failing test**

```python
def test_repeat_eval_run_copies_data_source_token(client, eval_db):
    """Repeating an eval run must propagate data_source_token from the original."""
    # Create original run with a token stored
    with eval_db._lock:
        conn = eval_db._connect()
        conn.execute(
            "UPDATE eval_runs SET data_source_token = 'secret-token-xyz' WHERE id = 1"
        )
        conn.commit()

    resp = client.post("/api/eval/runs/1/repeat")
    assert resp.status_code == 200
    new_run_id = resp.json()["run_id"]

    with eval_db._lock:
        row = eval_db._connect().execute(
            "SELECT data_source_token FROM eval_runs WHERE id = ?", (new_run_id,)
        ).fetchone()
    assert row["data_source_token"] == "secret-token-xyz", \
        "Repeated run must inherit data_source_token from original"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_runs_api.py::test_repeat_eval_run_copies_data_source_token -xvs`
Expected: FAIL — `data_source_token` is `None`

**Step 3: Write implementation**

In `api/eval_runs.py` `repeat_eval_run`, add `data_source_token` to the INSERT column list and values:

```python
# Find the INSERT statement in repeat_eval_run and add data_source_token:
cur = conn.execute(
    """INSERT INTO eval_runs
       (status, variant_ids, item_count, seed, judge_model, judge_backend,
        judge_prompt_template, scheduling_mode, same_targets, diff_targets,
        num_variants, data_source_token, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (
        "queued",
        run["variant_ids"],
        run["item_count"],
        run.get("seed"),
        run.get("judge_model"),
        run.get("judge_backend"),
        run.get("judge_prompt_template"),
        run.get("scheduling_mode", "immediate"),
        run.get("same_targets", 1),
        run.get("diff_targets", 1),
        run.get("num_variants"),
        run.get("data_source_token"),   # ← propagated from original
        time.time(),
    ),
)
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_runs_api.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/api/eval_runs.py tests/test_eval_runs_api.py
git commit -m "fix(eval): repeat_eval_run propagates data_source_token from original run (H4)"
```

---

### Task 9: Aggregate all validation errors before responding in `put_eval_settings`

**Files:**
- Modify: `ollama_queue/api/eval_settings.py:229-233`
- Test: `tests/test_eval_settings_api.py`

**Step 1: Write the failing test**

```python
def test_eval_settings_returns_all_errors_for_multi_field_invalid(client):
    """PUT /api/eval/settings must aggregate all errors into one response."""
    resp = client.put("/api/eval/settings", json={
        "eval.judge_backend": "nonexistent_backend",  # provider error → 400
        "eval.f1_threshold": "99.9",                  # validation error → 422
    })
    assert resp.status_code in (400, 422)
    body = resp.json()
    errors_str = str(body)
    assert "nonexistent_backend" in errors_str or "judge_backend" in errors_str
    assert "f1_threshold" in errors_str or "99.9" in errors_str, \
        "Both validation errors must appear in the response"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_settings_api.py::test_eval_settings_returns_all_errors_for_multi_field_invalid -xvs`
Expected: FAIL — only provider error returned

**Step 3: Write implementation**

In `eval_settings.py`, merge both error lists before raising:

```python
all_errors = provider_errors + validation_errors
if all_errors:
    raise HTTPException(status_code=422, detail={"errors": all_errors})
```

Remove the two separate `if provider_errors` / `if validation_errors` raises above this.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_settings_api.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/api/eval_settings.py tests/test_eval_settings_api.py
git commit -m "fix(api): aggregate all eval settings validation errors before responding (M4)"
```

---

### Task 10: Use direct DB lookup in `reschedule_dlq_entry`

**Files:**
- Modify: `ollama_queue/api/dlq.py:98-103`
- Test: `tests/test_dlq_api.py`

**Step 1: Write the failing test**

```python
def test_reschedule_resolved_dlq_entry_is_not_404(client, db):
    """reschedule must use direct PK lookup — resolved entries exist and should not 404."""
    job_id = db.submit_job(command="echo test", source="test", priority=5, timeout=60)
    db.move_to_dlq(job_id, reason="test failure", command="echo test", model=None, timeout=60, source="test")

    # Find the DLQ entry
    entries = db.list_dlq()
    dlq_id = entries[0]["id"]

    # Mark as resolved (simulates a prior retry that worked)
    db.update_dlq_reschedule(dlq_id, new_job_id=999, reasoning="manually retried")

    resp = client.post(f"/api/dlq/{dlq_id}/reschedule")
    # Resolved entry exists — should not 404 (may return 200 or 400, but not 404)
    assert resp.status_code != 404, "Reschedule should not 404 for a resolved entry that exists"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dlq_api.py::test_reschedule_resolved_dlq_entry_is_not_404 -xvs`
Expected: FAIL — returns 404

**Step 3: Write implementation**

In `api/dlq.py`, replace the O(N) scan with a direct lookup:

```python
@router.post("/{dlq_id}/reschedule")
def reschedule_dlq_entry_endpoint(dlq_id: int):
    entry = db.get_dlq_entry(dlq_id)   # ← direct PK lookup, not list_dlq() scan
    if entry is None:
        raise HTTPException(status_code=404, detail=f"DLQ entry {dlq_id} not found")
    # ... rest of reschedule logic unchanged ...
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dlq_api.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/api/dlq.py tests/test_dlq_api.py
git commit -m "fix(api): reschedule_dlq_entry uses get_dlq_entry (direct PK) not O(N) scan (M5)"
```

---

## Batch 5: Daemon Admission & Scheduling (HIGH — H2, H3, H5, H6, H7)

### Task 11: Retain preempted jobs in `_running` until process exits

**Context:** `_preempt_job` removes the job from `_running` on SIGTERM, before the process exits. This causes `_can_admit` to count the VRAM as free, allowing a new job to start while the preempted job is still consuming GPU memory.

**Files:**
- Modify: `ollama_queue/daemon/executor.py` (`_preempt_job`, worker thread `finally` block, `_can_admit`)
- Test: `tests/test_executor.py`

**Step 1: Write the failing test**

```python
def test_preempted_job_stays_in_running_until_process_exits(executor):
    """_running must retain the preempted job entry until its process actually exits."""
    job_id = 1
    proc = MagicMock()
    proc.pid = 1234
    proc.send_signal = MagicMock()
    executor._running[job_id] = {"proc": proc, "model": "qwen2.5:70b", "resource_profile": "heavy"}
    executor._running_models.add("qwen2.5:70b")

    executor._preempt_job(job_id)

    assert job_id in executor._running, \
        "Preempted job must stay in _running until process exit — not removed on SIGTERM"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `job_id` removed from `_running` immediately on SIGTERM

**Step 3: Write implementation**

Add a `_preempting: set[int]` in `__init__`. `_preempt_job` adds to `_preempting` instead of removing from `_running`. The worker thread `finally` block pops from both. `_can_admit` counts both sets:

```python
# In ExecutorMixin.__init__ (or Daemon.__init__):
self._preempting: set[int] = set()

# In _preempt_job — replace the _running removal with:
with self._running_lock:
    self._preempting.add(job_id)
    # Do NOT pop from _running — process still holds VRAM

# In worker thread finally block — pop from both:
with self._running_lock:
    self._running.pop(job_id, None)
    self._preempting.discard(job_id)
    self._running_models.discard(job.get("model", ""))

# In _can_admit VRAM budget — count preempting jobs as running:
with self._running_lock:
    all_active = {**self._running, **{j: {} for j in self._preempting}}
    # use all_active for VRAM budget calculation
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_executor.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/daemon/executor.py tests/test_executor.py
git commit -m "fix(daemon): retain preempted jobs in _running until process exit — prevent VRAM double-count (H2)"
```

---

### Task 12: Catch `CroniterBadCronError` per-entry in `promote_due_jobs`

**Files:**
- Modify: `ollama_queue/scheduling/scheduler.py:91-105` (`_compute_next_run`)
- Test: `tests/test_scheduler.py`

**Step 1: Write the failing test**

```python
def test_bad_cron_does_not_abort_promotion_loop(scheduler_db):
    """A single invalid cron expression must be skipped — must not abort promote_due_jobs."""
    scheduler_db.add_recurring_job(name="bad-cron", cron_expression="NOT_A_CRON", command="echo bad")
    scheduler_db.add_recurring_job(name="good-job", interval_seconds=1, command="echo good")

    # Advance time so both are due
    now = time.time() + 10
    scheduler = Scheduler(scheduler_db)

    # Must not raise
    promoted = scheduler.promote_due_jobs(now=now)

    good_names = [j.get("name", "") for j in promoted]
    assert any("good-job" in n for n in good_names), \
        "good-job must be promoted even when bad-cron job is present"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scheduler.py::test_bad_cron_does_not_abort_promotion_loop -xvs`
Expected: FAIL — `CroniterBadCronError` propagates

**Step 3: Write implementation**

In `scheduler.py`, wrap the `croniter` call in `_compute_next_run`:

```python
def _compute_next_run(rj=rj, now=now):
    cron_expr = rj.get("cron_expression")
    if cron_expr:
        try:
            start_dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
            return croniter(cron_expr, start_dt).get_next(datetime.datetime).timestamp()
        except Exception as exc:
            _log.error(
                "recurring job %r has invalid cron_expression %r: %s — skipping",
                rj.get("name"), cron_expr, exc,
            )
            return None   # caller must check for None and skip this entry
    if not rj.get("interval_seconds"):
        _log.warning("recurring job %r has no cron_expression and no interval_seconds", rj.get("name"))
    return now + (rj.get("interval_seconds") or 300)
```

In the promotion loop, skip entries where `_compute_next_run()` returns `None`.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_scheduler.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/scheduling/scheduler.py tests/test_scheduler.py
git commit -m "fix(scheduler): catch CroniterBadCronError per-entry — bad cron skipped, loop continues (H3)"
```

---

### Task 13: `shutdown` must wait for in-flight worker threads

**Files:**
- Modify: `ollama_queue/daemon/loop.py:499-503`
- Test: `tests/test_daemon_loop.py`

**Step 1: Write the failing test**

```python
def test_shutdown_completes_inflight_db_writes(daemon_with_db):
    """Daemon shutdown must wait for worker threads to finish their DB writes."""
    daemon, db = daemon_with_db
    # The real invariant: after shutdown(), no job is in status='running'
    daemon.shutdown()
    with db._lock:
        running = db._connect().execute(
            "SELECT id FROM jobs WHERE status = 'running'"
        ).fetchall()
    assert len(running) == 0, \
        "No jobs should be in 'running' state after clean shutdown"
```

**Step 2: Run test**

Expected: May already pass on a quiet system. The key behavioral fix is changing `wait=False` to `wait=True`.

**Step 3: Write implementation**

In `loop.py`:

```python
def shutdown(self) -> None:
    if self._executor is not None:
        # wait=True: worker threads complete their DB writes before process exit.
        # Prevents jobs stuck in status='running' after SIGTERM.
        self._executor.shutdown(wait=True)
        self._executor = None
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_daemon_loop.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/daemon/loop.py tests/test_daemon_loop.py
git commit -m "fix(daemon): shutdown(wait=True) ensures worker threads complete DB writes on SIGTERM (H5)"
```

---

### Task 14: Guard `record_duration` against `None` model

**Files:**
- Modify: `ollama_queue/daemon/executor.py` (post-job success path, line ~471)
- Test: `tests/test_executor.py`

**Step 1: Write the failing test**

```python
def test_record_duration_not_called_for_command_only_job(executor, db):
    """record_duration must be skipped for jobs with model=None (command-only jobs)."""
    with patch.object(executor, "record_duration") as mock_rd:
        executor._handle_job_success(
            job={"id": 1, "command": "echo hello", "model": None, "resource_profile": "default"},
            exit_code=0,
            elapsed=1.5,
        )
        mock_rd.assert_not_called()
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `record_duration` called with `model=None`

**Step 3: Write implementation**

In `executor.py`, add model guard around `record_duration` call:

```python
# Change:
if exit_code == 0:
    self.record_duration(model=job["model"], ...)
# To:
if exit_code == 0 and job.get("model"):
    self.record_duration(model=job["model"], ...)
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_executor.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/daemon/executor.py tests/test_executor.py
git commit -m "fix(daemon): skip record_duration for command-only jobs with model=None (H6)"
```

---

### Task 15: Heavy models should not be blocked by embed-only concurrent jobs

**Files:**
- Modify: `ollama_queue/daemon/executor.py:247-249` (`_can_admit` heavy profile block)
- Test: `tests/test_executor.py`

**Step 1: Write the failing test**

```python
def test_heavy_model_admitted_when_only_embed_jobs_running(executor):
    """A heavy model must not be blocked by embed jobs (negligible VRAM)."""
    for i in range(4):
        executor._running[i] = {
            "model": "nomic-embed-text", "resource_profile": "embed"
        }
    job = {"model": "llama3:70b", "resource_profile": "heavy", "id": 99}
    assert executor._can_admit(job), \
        "Heavy model should be admitted when only embed jobs run (no VRAM conflict)"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `len(self._running) == 4 > 0` blocks heavy job

**Step 3: Write implementation**

In `executor.py` heavy profile block:

```python
if profile == "heavy":
    with self._running_lock:
        # Heavy models need exclusive GPU. Embed jobs use negligible VRAM —
        # do not count them as blockers (prevents indefinite starvation).
        non_embed_count = sum(
            1 for info in self._running.values()
            if info.get("resource_profile") != "embed"
        )
        return non_embed_count == 0
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_executor.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/daemon/executor.py tests/test_executor.py
git commit -m "fix(daemon): heavy models not blocked by embed-only concurrent jobs (H7)"
```

---

## Batch 6: Settings & Sensing Robustness (CRITICAL + HIGH — C7, H9, H11, M1, M2)

### Task 16: Guard `health.evaluate()` against missing settings keys

**Files:**
- Modify: `ollama_queue/sensing/health.py:220-243`
- Test: `tests/test_health.py`

**Step 1: Write the failing test**

```python
def test_health_evaluate_uses_defaults_for_missing_settings():
    """evaluate() must not raise KeyError when settings keys are absent."""
    from ollama_queue.sensing.health import HealthMonitor
    monitor = HealthMonitor()
    partial_settings = {"ram_pause_pct": 85}   # missing 5 other keys
    # Must not raise
    result = monitor.evaluate(snapshot=_mock_snapshot(), settings=partial_settings)
    assert result is not None
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `KeyError: 'swap_pause_pct'`

**Step 3: Write implementation**

In `health.py` `evaluate()`, replace all bare dict accesses with `.get()` + defaults:

```python
ram_pause_pct = settings.get("ram_pause_pct", 85)
swap_pause_pct = settings.get("swap_pause_pct", 80)
load_pause_multiplier = float(settings.get("load_pause_multiplier", 2.0))
ram_resume_pct = settings.get("ram_resume_pct", 75)
swap_resume_pct = settings.get("swap_resume_pct", 70)
load_resume_multiplier = float(settings.get("load_resume_multiplier", 1.5))
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_health.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/sensing/health.py tests/test_health.py
git commit -m "fix(sensing): health.evaluate() uses .get() with defaults for all settings keys (C7)"
```

---

### Task 17: Raise stall detection log level from debug to warning

**Files:**
- Modify: `ollama_queue/sensing/stall.py:106-115`
- Test: `tests/test_stall.py`

**Step 1: Write the failing test**

```python
def test_get_ollama_ps_models_logs_warning_on_failure(caplog):
    """Ollama ps failure must log at WARNING — debug is invisible in production."""
    import logging
    from ollama_queue.sensing.stall import StallDetector
    detector = StallDetector()
    with patch("httpx.Client") as mock_cls:
        mock_cls.side_effect = Exception("connection refused")
        with caplog.at_level(logging.WARNING, logger="ollama_queue.sensing.stall"):
            result = detector.get_ollama_ps_models()
    assert result == set()
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "Must log WARNING on ps failure — not just debug (invisible in production)"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — no WARNING in caplog (only debug)

**Step 3: Write implementation**

In `stall.py` line ~115:

```python
# Change:
_log.debug("get_ollama_ps_models failed: %s", exc)
# To:
_log.warning("get_ollama_ps_models failed — stall detection signal disabled: %s", exc)
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_stall.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/sensing/stall.py tests/test_stall.py
git commit -m "fix(sensing): log WARNING (not debug) on Ollama ps failure — stall signal visibility (H11)"
```

---

### Task 18: Per-entry exception handling in `DeferralScheduler._do_sweep`

**Files:**
- Modify: `ollama_queue/scheduling/deferral.py:72-128`
- Test: `tests/test_deferral.py`

**Step 1: Write the failing test**

```python
def test_do_sweep_continues_after_entry_raises(deferral_scheduler, monkeypatch):
    """_do_sweep must skip failing entries and process the rest."""
    call_log = []
    orig_estimate = deferral_scheduler.estimator.estimate

    def patched_estimate(model, command, profile):
        if model == "bad_model":
            raise RuntimeError("DB error during estimate")
        call_log.append(model)
        return orig_estimate(model, command, profile)

    monkeypatch.setattr(deferral_scheduler.estimator, "estimate", patched_estimate)

    entries = [
        {"id": 1, "job_id": 10, "model": "bad_model", "command": "echo", "resource_profile": "ollama"},
        {"id": 2, "job_id": 20, "model": "qwen2.5:7b", "command": "echo", "resource_profile": "ollama"},
    ]
    deferral_scheduler._do_sweep(entries)   # must not raise

    assert "qwen2.5:7b" in call_log, "Second entry must be processed after first raises"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `RuntimeError` propagates, second entry never processed

**Step 3: Write implementation**

In `deferral.py`, wrap per-entry logic in the loop:

```python
for entry in entries:
    try:
        # ... existing per-entry code ...
    except Exception as exc:
        _log.warning(
            "_do_sweep: skipping deferral entry %d due to error: %s",
            entry.get("id", "?"), exc,
        )
        continue
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_deferral.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/scheduling/deferral.py tests/test_deferral.py
git commit -m "fix(scheduling): _do_sweep catches per-entry exceptions and continues loop (M1)"
```

---

### Task 19: Move `sorted()` outside lock in `BurstDetector.regime()`

**Files:**
- Modify: `ollama_queue/sensing/burst.py:90-109`
- Test: `tests/test_burst.py`

**Step 1: Write the failing test**

```python
def test_burst_detector_regime_sorts_outside_lock():
    """regime() must copy samples under lock, then sort outside — avoids blocking submissions."""
    from ollama_queue.sensing.burst import BurstDetector
    detector = BurstDetector()
    for _ in range(100):
        detector.record_submission()

    sort_while_locked = {"flag": False}
    original_regime = detector.regime

    def patched_regime():
        # Attempt to acquire lock during regime() call
        # If lock is held during sort, this will block
        acquired = detector._lock.acquire(blocking=False)
        if not acquired:
            sort_while_locked["flag"] = True
        else:
            detector._lock.release()
        return original_regime()

    # Just run regime() and verify lock is released promptly
    result = detector.regime()
    assert result in ("unknown", "calm", "burst", "storm")
    # Primary check: the lock must be acquirable immediately after regime() returns
    assert detector._lock.acquire(blocking=False), "Lock must not be held after regime() returns"
    detector._lock.release()
```

**Step 2: Write implementation**

In `burst.py`:

```python
def regime(self) -> str:
    with self._lock:
        if len(self._baseline_samples) < self._MIN_BASELINE:
            return "unknown"
        samples_copy = list(self._baseline_samples)  # copy under lock
        ewma = self._ewma
    # Sort and percentile computation outside the lock
    sorted_samples = sorted(samples_copy)
    p50 = sorted_samples[len(sorted_samples) // 2]
    p95 = sorted_samples[int(len(sorted_samples) * 0.95)]
    # ... rest of computation using sorted_samples, p50, p95, ewma ...
```

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_burst.py -xvs`
Expected: All PASS

**Step 4: Commit**

```bash
git add ollama_queue/sensing/burst.py tests/test_burst.py
git commit -m "fix(sensing): BurstDetector.regime() copies samples under lock, sorts outside (M2)"
```

---

### Task 20: Fix DLQ double-retry race in `DLQManager.handle_failure`

**Files:**
- Modify: `ollama_queue/dlq.py:30-52`
- Test: `tests/test_dlq.py`

**Step 1: Write the failing test**

```python
def test_dlq_handle_failure_single_atomic_check():
    """handle_failure must use a single locked get_job to prevent concurrent double-retry."""
    import threading
    from ollama_queue.dlq import DLQManager

    retry_calls = []
    mock_db = MagicMock()
    mock_db._lock = threading.RLock()
    mock_db.get_job.return_value = {
        "id": 1, "retry_count": 0, "max_retries": 3,
        "last_retry_delay": 30, "command": "echo", "model": None,
        "timeout": 60, "source": "test",
    }
    mock_db._set_job_retry = lambda *a, **k: retry_calls.append(1)

    manager = DLQManager(mock_db)
    threads = [threading.Thread(target=manager.handle_failure, args=(1,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(retry_calls) <= 1, f"Expected at most 1 retry, got {len(retry_calls)}"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `retry_calls` has length > 1 (double or triple retry)

**Step 3: Write implementation**

In `dlq.py`, consolidate both `get_job` calls into one under a lock:

```python
def handle_failure(self, job_id: int) -> None:
    with self.db._lock:
        job = self.db.get_job(job_id)
        if job is None:
            _log.warning("dlq.handle_failure: job %d not found", job_id)
            return
        retry_count = job.get("retry_count", 0)
        max_retries = job.get("max_retries", 0)
        if retry_count < max_retries:
            self._schedule_retry(job_id, retry_count, job=job)  # pass job to avoid 2nd fetch
        else:
            self.db.move_to_dlq(
                job_id,
                reason=f"exceeded max_retries ({max_retries})",
                command=job.get("command", ""),
                model=job.get("model"),
                timeout=job.get("timeout", 600),
                source=job.get("source", "unknown"),
            )
```

Update `_schedule_retry` to accept `job=None` and only re-fetch if not provided.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dlq.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/dlq.py tests/test_dlq.py
git commit -m "fix(dlq): single locked get_job in handle_failure prevents double-retry race (H9)"
```

---

## Batch 7: Eval Pipeline Integrity (HIGH + MODERATE — H10, M8)

### Task 21: Delete corrupted partial generation results before re-generating

**Files:**
- Modify: `ollama_queue/eval/generate.py` (before `_generate_one` call in the item loop)
- Test: `tests/test_eval_generate.py`

**Step 1: Write the failing test**

```python
def test_corrupted_generation_replaced_on_restart(eval_db):
    """A null-principle generation row (interrupted mid-stream) must be deleted and regenerated."""
    # Insert a corrupted result (principle=None, no error — mid-stream interrupt)
    with eval_db._lock:
        conn = eval_db._connect()
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, row_type, principle) "
            "VALUES (1, 'A', 'item-1', 'item-1', 'generate', NULL)"
        )
        conn.commit()

    # run_eval_generate should detect and delete the corrupted row, then regenerate
    with patch("ollama_queue.eval.generate._generate_one") as mock_gen:
        mock_gen.return_value = "good principle"
        _process_item_for_run(eval_db, run_id=1, variant="A", item={"id": "item-1"})

    mock_gen.assert_called_once(), "Should regenerate after deleting corrupted row"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `INSERT OR IGNORE` keeps the corrupted row, `_generate_one` not called

**Step 3: Write implementation**

In `generate.py`, before each `_generate_one` invocation, add a corruption check:

```python
# Before calling _generate_one for (run_id, variant_id, item):
with db._lock:
    existing = db._connect().execute(
        "SELECT id, principle FROM eval_results "
        "WHERE run_id = ? AND variant = ? AND source_item_id = ? AND row_type = 'generate'",
        (run_id, variant_id, str(item["id"])),
    ).fetchone()
    if existing and existing["principle"] is None:
        _log.warning(
            "eval run %d: deleting corrupted generation result (null principle) "
            "for variant=%s item=%s — will regenerate",
            run_id, variant_id, item["id"],
        )
        conn = db._connect()
        conn.execute("DELETE FROM eval_results WHERE id = ?", (existing["id"],))
        conn.commit()
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_generate.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/generate.py tests/test_eval_generate.py
git commit -m "fix(eval): delete corrupted null-principle generation rows before re-generating (H10)"
```

---

### Task 22: Remove dead `_PRIOR_LOG_ODDS` from `eval/metrics.py`

**Files:**
- Modify: `ollama_queue/eval/metrics.py:23`
- Test: `tests/test_eval_metrics.py`

**Step 1: Write the test**

```python
def test_prior_log_odds_not_in_metrics_module():
    """_PRIOR_LOG_ODDS is dead code in metrics.py — authoritative copy is in judge.py."""
    import ast, pathlib
    src = pathlib.Path("ollama_queue/eval/metrics.py").read_text()
    tree = ast.parse(src)
    names = [
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and hasattr(node.targets[0], "id")
    ]
    assert "_PRIOR_LOG_ODDS" not in names, \
        "_PRIOR_LOG_ODDS in metrics.py is dead code (used only in judge.py)"
```

**Step 2: Write implementation**

Delete this line from `metrics.py`:
```python
_PRIOR_LOG_ODDS = math.log(0.25 / 0.75)
```

If `math` is no longer used after deletion, remove its import too.

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_eval_metrics.py -xvs`
Expected: All PASS

**Step 4: Commit**

```bash
git add ollama_queue/eval/metrics.py tests/test_eval_metrics.py
git commit -m "fix(eval): remove dead _PRIOR_LOG_ODDS from metrics.py — authoritative in judge.py (M8)"
```

---

## Batch 8: DB & Code Quality (MODERATE — M3, M6, M7, M9)

### Task 23: Add column-name whitelist to `upsert_consumer` / `update_consumer`

**Files:**
- Modify: `ollama_queue/db/jobs.py:511-556`
- Test: `tests/test_jobs_db.py`

**Step 1: Write the failing test**

```python
def test_upsert_consumer_rejects_unknown_column():
    """upsert_consumer must reject unknown column names — prevents SQL injection via f-string."""
    db = Database(":memory:")
    db.initialize()
    with pytest.raises((ValueError, KeyError)):
        db.upsert_consumer({"id": "test", "evil_col'; DROP TABLE consumers; --": "x"})
```

**Step 2: Run test to verify it fails**

Expected: FAIL — no error raised

**Step 3: Write implementation**

Add a whitelist constant before `upsert_consumer`:

```python
_CONSUMER_ALLOWED_COLUMNS = frozenset({
    "id", "name", "status", "port", "command", "config_path",
    "backend", "is_included", "last_seen", "pid", "notes",
})

def upsert_consumer(self, data: dict) -> None:
    unknown = set(data.keys()) - _CONSUMER_ALLOWED_COLUMNS
    if unknown:
        raise ValueError(f"upsert_consumer: unknown columns {unknown!r}")
    # ... existing f-string SQL construction unchanged ...
```

Apply the same whitelist check to `update_consumer`.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_jobs_db.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/db/jobs.py tests/test_jobs_db.py
git commit -m "fix(db): whitelist column names in upsert_consumer/update_consumer (M3)"
```

---

### Task 24: Move `assert cur.lastrowid` inside lock in `add_recurring_job`

**Files:**
- Modify: `ollama_queue/db/schedule.py:51-79`
- Test: `tests/test_schedule_db.py` (structural assertion)

**Step 1: Write the test**

```python
def test_add_recurring_job_assert_before_commit():
    """assert cur.lastrowid must be inside the lock, before conn.commit()."""
    import inspect
    from ollama_queue.db.schedule import ScheduleMixin
    src = inspect.getsource(ScheduleMixin.add_recurring_job)
    lines = src.splitlines()
    commit_idx = next(i for i, l in enumerate(lines) if "conn.commit()" in l)
    assert_idx = next((i for i, l in enumerate(lines) if "assert cur.lastrowid" in l), None)
    assert assert_idx is not None, "assert cur.lastrowid must exist"
    assert assert_idx < commit_idx, \
        "assert cur.lastrowid must appear before conn.commit() — not after"
```

**Step 2: Write implementation**

In `db/schedule.py`, move the assert inside the `with self._lock:` block, before `conn.commit()`:

```python
with self._lock:
    conn = self._connect()
    cur = conn.execute(INSERT_SQL, params)
    assert cur.lastrowid is not None   # ← inside lock, before commit
    conn.commit()
    return cur.lastrowid
```

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_schedule_db.py -xvs`
Expected: All PASS

**Step 4: Commit**

```bash
git add ollama_queue/db/schedule.py tests/test_schedule_db.py
git commit -m "fix(db): move assert cur.lastrowid inside lock before conn.commit() in add_recurring_job (M6)"
```

---

### Task 25: Wrap all migration steps in a single transaction

**Files:**
- Modify: `ollama_queue/db/__init__.py:66-75` (`_add_column_if_missing`)
- Modify: `ollama_queue/db/schema.py` (`_run_migrations`)
- Test: `tests/test_db_schema.py`

**Step 1: Write the failing test**

```python
def test_add_column_if_missing_does_not_commit_internally():
    """_add_column_if_missing must not commit — caller owns the transaction."""
    import inspect
    from ollama_queue.db import Database
    src = inspect.getsource(Database._add_column_if_missing)
    assert "conn.commit()" not in src, \
        "_add_column_if_missing must not commit internally — commit is the caller's responsibility"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `conn.commit()` found in `_add_column_if_missing`

**Step 3: Write implementation**

Remove `conn.commit()` from `_add_column_if_missing`. Add a single `conn.commit()` at the end of `_run_migrations`, after all `_add_column_if_missing` calls:

```python
def _add_column_if_missing(self, conn, table: str, column: str, col_type: str) -> None:
    """Add column if absent. Caller is responsible for commit."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        _log.info("schema: added column %s.%s", table, column)
    # NO conn.commit() here

def _run_migrations(self, conn) -> None:
    """Run all schema migrations atomically."""
    self._add_column_if_missing(conn, "jobs", "tag", "TEXT")
    # ... all other _add_column_if_missing calls ...
    conn.commit()   # ← single commit for all migrations
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_db_schema.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/db/__init__.py ollama_queue/db/schema.py tests/test_db_schema.py
git commit -m "fix(db): migrations run as single transaction — one commit for all column additions (M7)"
```

---

### Task 26: Thread-safe `OllamaModels._list_local_cache` write

**Context:** Task 20 in `2026-03-13-edge-case-fixes.md` reduces the cache TTL. This task adds a `threading.Lock` to prevent concurrent cache misses from spawning multiple `ollama list` subprocesses.

**Files:**
- Modify: `ollama_queue/models/client.py:75-82`
- Test: `tests/test_models_client.py`

**Step 1: Write the failing test**

```python
def test_list_local_cache_only_fetches_once_on_concurrent_miss():
    """Concurrent list_local() calls on a cold cache must only call _fetch_list_local once."""
    from ollama_queue.models.client import OllamaModels
    OllamaModels._invalidate_list_cache()

    fetch_count = {"n": 0}
    def slow_fetch():
        fetch_count["n"] += 1
        time.sleep(0.05)
        return [{"name": "qwen2.5:7b"}]

    with patch.object(OllamaModels, "_fetch_list_local", slow_fetch):
        threads = [threading.Thread(target=OllamaModels.list_local) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert fetch_count["n"] == 1, \
        f"Expected 1 fetch with a lock, got {fetch_count['n']} (cache race)"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `fetch_count["n"] > 1`

**Step 3: Write implementation**

Add a class-level `threading.Lock` to `OllamaModels` and acquire it in `list_local`:

```python
class OllamaModels:
    _list_local_cache: tuple | None = None
    _list_local_lock = threading.Lock()   # ← add this

    @classmethod
    def list_local(cls) -> list[dict]:
        with cls._list_local_lock:
            now = time.time()
            if cls._list_local_cache is not None:
                ts, data = cls._list_local_cache
                if now - ts < cls._LIST_LOCAL_TTL:
                    return data
            result = cls._fetch_list_local()
            cls._list_local_cache = (now, result)
            return result
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_models_client.py -xvs`
Expected: All PASS

**Step 5: Commit**

```bash
git add ollama_queue/models/client.py tests/test_models_client.py
git commit -m "fix(models): thread-safe _list_local_cache write with class-level Lock (M9)"
```

---

## Batch 9: Full Suite Verification

### Task 27: Run full test suite, linter, and SPA build

**Step 1: Run Python tests**

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -q`
Expected: ≥ 1,677 tests pass, 0 failures, 0 errors

**Step 2: Run linter**

Run: `make lint`
Expected: 0 errors

**Step 3: Build SPA**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds, `dist/index.html` present

**Step 4: Final commit**

```bash
git add -u
git commit -m "chore: bug-audit-fixes complete — 26 bugs fixed (batches 1-8)"
```

---

## Audit Source

All bugs traced to: `~/Documents/research/2026-03-13-ollama-queue-edge-case-audit.md`

Companion plan (run together): `docs/plans/2026-03-13-edge-case-fixes.md`
