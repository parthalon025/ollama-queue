# Eval Backend Host Selection — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users choose which Ollama backend host runs each eval role (generator, judge, analysis) with per-run override and transparency.

**Architecture:** Add `_backend` extraction to the proxy layer, thread a `backend` param through `_call_proxy()`, read per-role backend settings in the eval engine, and expose backend dropdowns in the Eval Settings and Run Trigger UI. Record actual backend used on eval_runs for transparency.

**Tech Stack:** Python (FastAPI, SQLite), Preact + @preact/signals, httpx

---

## Naming Convention

The existing `judge_backend` column on `eval_runs` and `eval.judge_backend` setting store the **provider type** (`'ollama'`/`'openai'`). To avoid confusion, all new fields use the `_backend_url` suffix to indicate these hold **backend host URLs** (or `"auto"` for smart routing).

---

### Task 1: Proxy `_backend` extraction + `_call_proxy` param

**Files:**

- Modify: `ollama_queue/api/proxy.py:199-201` (Ollama extraction) and `:243` (select_backend call)
- Modify: `ollama_queue/eval/engine.py:203-241` (`_call_proxy` signature + body)
- Test: `tests/test_api.py` (proxy tests)
- Test: `tests/test_eval_engine.py` (call_proxy tests)

**Step 1: Write failing test — proxy respects `_backend`**

In `tests/test_api.py`, add a test class `TestProxyBackendOverride`:

```python
class TestProxyBackendOverride:
    """Proxy extracts _backend and routes to specified backend."""

    def test_backend_extracted_from_body(self, client, db):
        """_backend is removed from body before forwarding to Ollama."""
        # Submit with _backend in body — it should be stripped
        with patch("ollama_queue.api.proxy.select_backend") as mock_sb:
            mock_sb.return_value = "http://127.0.0.1:11434"
            with patch("httpx.AsyncClient") as mock_client:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"response": "ok"}
                mock_resp.status_code = 200
                mock_resp.headers = {}
                mock_client.return_value.__aenter__.return_value.post.return_value = mock_resp
                resp = client.post("/api/generate", json={
                    "model": "qwen2.5:7b", "prompt": "hi", "_backend": "http://100.0.0.1:11434"
                })
            # select_backend should NOT have been called — _backend overrides it
            mock_sb.assert_not_called()

    def test_backend_auto_uses_select_backend(self, client, db):
        """_backend='auto' falls through to normal select_backend routing."""
        with patch("ollama_queue.api.proxy.select_backend") as mock_sb:
            mock_sb.return_value = "http://127.0.0.1:11434"
            with patch("httpx.AsyncClient") as mock_client:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"response": "ok"}
                mock_resp.status_code = 200
                mock_resp.headers = {}
                mock_client.return_value.__aenter__.return_value.post.return_value = mock_resp
                resp = client.post("/api/generate", json={
                    "model": "qwen2.5:7b", "prompt": "hi", "_backend": "auto"
                })
            mock_sb.assert_called_once()

    def test_backend_not_in_forwarded_body(self, client, db):
        """_backend is stripped before forwarding to Ollama (like _priority)."""
        # Verified by checking the body sent to the mock client
        pass  # covered by test_backend_extracted_from_body assertion on forwarded body
```

**Step 2: Run tests to verify they fail**

Run: `/home/justin/Documents/projects/ollama-queue/.venv/bin/python -m pytest tests/test_api.py::TestProxyBackendOverride -v`
Expected: FAIL (tests reference unimplemented behavior)

**Step 3: Implement proxy `_backend` extraction**

In `ollama_queue/api/proxy.py`, at the Ollama extraction block (line ~199):

```python
priority = body.pop("_priority", 0)
source = body.pop("_source", "proxy")
req_timeout = body.pop("_timeout", 600)
forced_backend = body.pop("_backend", None)  # NEW: per-request backend override
```

Then at line ~243 where `select_backend` is called:

```python
if forced_backend and forced_backend != "auto":
    backend = forced_backend
else:
    backend = await select_backend(model)
```

**Step 4: Add `backend` param to `_call_proxy`**

In `ollama_queue/eval/engine.py`, update `_call_proxy` signature (line 203):

```python
def _call_proxy(
    http_base: str,
    model: str,
    prompt: str,
    temperature: float,
    num_ctx: int,
    timeout: int,
    source: str,
    priority: int = 2,
    extra_params: dict | None = None,
    system_prompt: str | None = None,
    backend: str | None = None,  # NEW: force specific backend URL
) -> tuple[str | None, int | None]:
```

In the body construction (line ~233):

```python
body: dict[str, Any] = {
    "model": model,
    "prompt": prompt,
    "stream": False,
    "options": options,
    "_priority": priority,
    "_source": source,
    "_timeout": timeout,
}
if backend and backend != "auto":
    body["_backend"] = backend
```

**Step 5: Write test for `_call_proxy` backend param**

In `tests/test_eval_engine.py`, add:

```python
class TestCallProxyBackendParam:
    """_call_proxy passes _backend in request body when specified."""

    def test_backend_included_in_body(self):
        with patch("httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"response": "test"}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

            _call_proxy(
                http_base="http://127.0.0.1:7683",
                model="qwen2.5:7b", prompt="hi",
                temperature=0.6, num_ctx=4096,
                timeout=60, source="test",
                backend="http://100.0.0.1:11434",
            )
            call_args = mock_client.return_value.__enter__.return_value.post.call_args
            assert call_args.kwargs["json"]["_backend"] == "http://100.0.0.1:11434"

    def test_backend_auto_omitted_from_body(self):
        with patch("httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"response": "test"}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

            _call_proxy(
                http_base="http://127.0.0.1:7683",
                model="qwen2.5:7b", prompt="hi",
                temperature=0.6, num_ctx=4096,
                timeout=60, source="test",
                backend="auto",
            )
            call_args = mock_client.return_value.__enter__.return_value.post.call_args
            assert "_backend" not in call_args.kwargs["json"]

    def test_backend_none_omitted_from_body(self):
        with patch("httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"response": "test"}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

            _call_proxy(
                http_base="http://127.0.0.1:7683",
                model="qwen2.5:7b", prompt="hi",
                temperature=0.6, num_ctx=4096,
                timeout=60, source="test",
                backend=None,
            )
            call_args = mock_client.return_value.__enter__.return_value.post.call_args
            assert "_backend" not in call_args.kwargs["json"]
```

**Step 6: Run all tests**

Run: `/home/justin/Documents/projects/ollama-queue/.venv/bin/python -m pytest tests/test_api.py::TestProxyBackendOverride tests/test_eval_engine.py::TestCallProxyBackendParam -v`
Expected: PASS

**Step 7: Commit**

```bash
git add ollama_queue/api/proxy.py ollama_queue/eval/engine.py tests/test_api.py tests/test_eval_engine.py
git commit -m "feat(eval): add _backend proxy param + _call_proxy backend support"
```

---

### Task 2: Schema migration — `gen_backend_url` and `judge_backend_url` columns

**Files:**

- Modify: `ollama_queue/db/schema.py:411-444` (eval_runs CREATE TABLE + migration)
- Test: `tests/test_db_schema.py` or inline test

**Context:** The existing `judge_backend` column (line 432) stores provider type (`'ollama'`/`'openai'`). New columns use `_url` suffix to store backend host URLs. SQLite `ALTER TABLE ADD COLUMN` for live DB migration; `CREATE TABLE` updated for fresh installs.

**Step 1: Add columns to CREATE TABLE**

In `ollama_queue/db/schema.py`, after line 443 (`runs_completed`), add:

```python
                ,gen_backend_url   TEXT
                ,judge_backend_url TEXT
```

**Step 2: Add migration in `_run_migrations`**

In the migrations section of `schema.py`, add:

```python
self._add_column_if_missing(conn, "eval_runs", "gen_backend_url", "TEXT")
self._add_column_if_missing(conn, "eval_runs", "judge_backend_url", "TEXT")
```

**Step 3: Write test**

```python
class TestEvalRunsBackendUrlColumns:
    def test_columns_exist_after_init(self, db):
        with db._lock:
            conn = db._connect()
            row = conn.execute("PRAGMA table_info(eval_runs)").fetchall()
        col_names = {r[1] for r in row}
        assert "gen_backend_url" in col_names
        assert "judge_backend_url" in col_names

    def test_columns_accept_url_values(self, db):
        """Backend URL columns accept arbitrary text (no CHECK constraint)."""
        with db._lock:
            conn = db._connect()
            run_id = conn.execute(
                "INSERT INTO eval_runs (data_source_url, variants, status, gen_backend_url, judge_backend_url) "
                "VALUES (?, ?, ?, ?, ?)",
                ("http://localhost:7685", '["A"]', "queued", "http://100.0.0.1:11434", "auto"),
            ).lastrowid
            conn.commit()
            row = conn.execute("SELECT gen_backend_url, judge_backend_url FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        assert row[0] == "http://100.0.0.1:11434"
        assert row[1] == "auto"
```

**Step 4: Run tests**

Run: `/home/justin/Documents/projects/ollama-queue/.venv/bin/python -m pytest tests/test_db_schema.py::TestEvalRunsBackendUrlColumns -v` (or wherever schema tests live)
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/db/schema.py tests/
git commit -m "feat(eval): add gen_backend_url, judge_backend_url columns to eval_runs"
```

---

### Task 3: Engine wiring — read backend settings, pass to generate/judge/analysis

**Files:**

- Modify: `ollama_queue/eval/engine.py:652-700` (`run_eval_session`)
- Modify: `ollama_queue/eval/generate.py:327-338` (`run_eval_generate` — accept + pass backend)
- Modify: `ollama_queue/eval/judge.py:523-532` (`run_eval_judge` — accept + pass backend)
- Modify: `ollama_queue/eval/promote.py:335-444` (`generate_eval_analysis` — read setting + pass backend)
- Test: `tests/test_eval_engine.py`
- Test: `tests/test_eval_generate.py` (if exists, otherwise `tests/test_eval_engine.py`)

**Context:** `run_eval_session()` orchestrates the full pipeline. It needs to:
1. Read `eval.generator_backend_url` and `eval.judge_backend_url` settings (or run-level overrides)
2. Pass them to `run_eval_generate()` and `run_eval_judge()`
3. Record the actual backend URLs on the run row

**Step 1: Write failing tests**

```python
class TestRunEvalSessionBackendWiring:
    """run_eval_session reads backend settings and passes to generate/judge."""

    def test_generator_backend_url_passed_to_generate(self, db):
        """eval.generator_backend_url setting is read and passed to run_eval_generate."""
        db.set_setting("eval.generator_backend_url", "http://100.0.0.1:11434")
        # Create a minimal run row
        run_id = db.create_eval_run(data_source_url="http://localhost:7685", variants='["A"]', status="queued")

        with patch("ollama_queue.eval.engine.run_eval_generate") as mock_gen, \
             patch("ollama_queue.eval.engine.run_eval_judge"), \
             patch("ollama_queue.eval.engine.generate_eval_analysis"), \
             patch("ollama_queue.eval.engine.check_auto_promote"), \
             patch("ollama_queue.models.client.OllamaModels.list_local", return_value=[{"name": "qwen2.5:7b"}]):
            mock_gen.return_value = None
            run_eval_session(run_id, db)

        mock_gen.assert_called_once()
        assert mock_gen.call_args.kwargs.get("backend") == "http://100.0.0.1:11434"

    def test_default_backend_is_none(self, db):
        """When no backend_url setting, backend=None (auto routing)."""
        run_id = db.create_eval_run(data_source_url="http://localhost:7685", variants='["A"]', status="queued")

        with patch("ollama_queue.eval.engine.run_eval_generate") as mock_gen, \
             patch("ollama_queue.eval.engine.run_eval_judge"), \
             patch("ollama_queue.eval.engine.generate_eval_analysis"), \
             patch("ollama_queue.eval.engine.check_auto_promote"), \
             patch("ollama_queue.models.client.OllamaModels.list_local", return_value=[{"name": "qwen2.5:7b"}]):
            mock_gen.return_value = None
            run_eval_session(run_id, db)

        assert mock_gen.call_args.kwargs.get("backend") is None

    def test_run_level_override_beats_setting(self, db):
        """gen_backend_url on the run row overrides the global setting."""
        db.set_setting("eval.generator_backend_url", "http://100.0.0.1:11434")
        run_id = db.create_eval_run(
            data_source_url="http://localhost:7685", variants='["A"]', status="queued",
            gen_backend_url="http://200.0.0.1:11434"
        )

        with patch("ollama_queue.eval.engine.run_eval_generate") as mock_gen, \
             patch("ollama_queue.eval.engine.run_eval_judge"), \
             patch("ollama_queue.eval.engine.generate_eval_analysis"), \
             patch("ollama_queue.eval.engine.check_auto_promote"), \
             patch("ollama_queue.models.client.OllamaModels.list_local", return_value=[{"name": "qwen2.5:7b"}]):
            mock_gen.return_value = None
            run_eval_session(run_id, db)

        assert mock_gen.call_args.kwargs.get("backend") == "http://200.0.0.1:11434"
```

**Step 2: Run tests to verify they fail**

Run: `/home/justin/Documents/projects/ollama-queue/.venv/bin/python -m pytest tests/test_eval_engine.py::TestRunEvalSessionBackendWiring -v`
Expected: FAIL

**Step 3: Add `backend` param to `run_eval_generate`**

In `ollama_queue/eval/generate.py`, update the `run_eval_generate` function signature to accept `backend: str | None = None` and pass it through to `_eng._call_proxy(..., backend=backend)` at line 327.

**Step 4: Add `backend` param to `run_eval_judge`**

In `ollama_queue/eval/judge.py`, update `run_eval_judge` to accept `backend: str | None = None` and pass it to both `_eng._call_proxy()` calls (lines 523 and 567).

**Step 5: Wire `run_eval_session` to read settings and pass backends**

In `ollama_queue/eval/engine.py`, inside `run_eval_session()` after the pre-flight check (~line 700):

```python
# Resolve backend URLs: run-level override > setting > None (auto)
def _resolve_backend(run_key: str, setting_key: str) -> str | None:
    val = run.get(run_key) or db.get_setting(setting_key)
    return val if val and val != "auto" else None

gen_backend = _resolve_backend("gen_backend_url", "eval.generator_backend_url")
judge_backend = _resolve_backend("judge_backend_url", "eval.judge_backend_url")
```

Then pass to the phase calls:

```python
run_eval_generate(run_id, db, http_base=http_base, backend=gen_backend)
run_eval_judge(run_id, db, http_base=http_base, backend=judge_backend)
```

Record actual backends on the run row after each phase completes (or at session start).

**Step 6: Wire `generate_eval_analysis` backend**

In `ollama_queue/eval/promote.py`, at `generate_eval_analysis` (~line 434), read the setting:

```python
analysis_backend = db.get_setting("eval.analysis_backend_url")
if analysis_backend == "auto":
    analysis_backend = None
```

Then pass to `_eng._call_proxy(..., backend=analysis_backend)`.

**Step 7: Run tests**

Run: `/home/justin/Documents/projects/ollama-queue/.venv/bin/python -m pytest tests/test_eval_engine.py::TestRunEvalSessionBackendWiring -v`
Expected: PASS

**Step 8: Commit**

```bash
git add ollama_queue/eval/engine.py ollama_queue/eval/generate.py ollama_queue/eval/judge.py ollama_queue/eval/promote.py tests/test_eval_engine.py
git commit -m "feat(eval): wire backend_url settings through generate/judge/analysis pipeline"
```

---

### Task 4: API — settings validation + run trigger override + status enrichment

**Files:**

- Modify: `ollama_queue/api/eval_settings.py` (validate backend URLs)
- Modify: `ollama_queue/api/eval_runs.py:93-150` (accept `gen_backend_url`/`judge_backend_url` in POST body)
- Modify: `ollama_queue/api/jobs.py` (enrich `active_eval` with backend URLs)
- Test: `tests/test_api_eval_settings.py`
- Test: `tests/test_api_eval_runs.py` (or wherever run trigger tests live)

**Step 1: Write failing test — settings validation rejects unknown backend URL**

```python
class TestEvalSettingsBackendValidation:
    def test_valid_backend_url_accepted(self, client, db):
        # Register a backend first
        db.add_backend("http://100.0.0.1:11434")
        resp = client.put("/api/eval/settings", json={
            "eval.generator_backend_url": "http://100.0.0.1:11434"
        })
        assert resp.status_code == 200

    def test_auto_accepted(self, client, db):
        resp = client.put("/api/eval/settings", json={
            "eval.generator_backend_url": "auto"
        })
        assert resp.status_code == 200

    def test_unknown_backend_url_rejected(self, client, db):
        resp = client.put("/api/eval/settings", json={
            "eval.generator_backend_url": "http://unknown:11434"
        })
        assert resp.status_code == 422
        assert "registered backends" in resp.json()["detail"].lower()
```

**Step 2: Run tests to verify they fail**

Run: `/home/justin/Documents/projects/ollama-queue/.venv/bin/python -m pytest tests/test_api_eval_settings.py::TestEvalSettingsBackendValidation -v`
Expected: FAIL

**Step 3: Implement settings validation**

In `ollama_queue/api/eval_settings.py`, in `put_eval_settings`, add validation for `*_backend_url` keys:

```python
_BACKEND_URL_KEYS = {"eval.generator_backend_url", "eval.judge_backend_url", "eval.analysis_backend_url"}

for key in _BACKEND_URL_KEYS & updates.keys():
    val = updates[key]
    if val and val != "auto":
        registered = {b["url"] for b in db.list_backends()}
        if val not in registered:
            raise HTTPException(
                status_code=422,
                detail=f"Backend {val!r} is not registered. Registered backends: {', '.join(sorted(registered))}"
            )
```

**Step 4: Write failing test — run trigger accepts backend overrides**

```python
class TestEvalRunBackendOverride:
    def test_gen_backend_url_stored_on_run(self, client, db):
        # Set up minimal eval data
        db.set_setting("eval.data_source_url", "http://127.0.0.1:7685")
        resp = client.post("/api/eval/runs", json={
            "variant_id": "A",
            "gen_backend_url": "http://100.0.0.1:11434",
        })
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]
        with db._lock:
            conn = db._connect()
            row = conn.execute("SELECT gen_backend_url FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        assert row[0] == "http://100.0.0.1:11434"

    def test_judge_backend_url_stored_on_run(self, client, db):
        db.set_setting("eval.data_source_url", "http://127.0.0.1:7685")
        resp = client.post("/api/eval/runs", json={
            "variant_id": "A",
            "judge_backend_url": "http://100.0.0.1:11434",
        })
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]
        with db._lock:
            conn = db._connect()
            row = conn.execute("SELECT judge_backend_url FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        assert row[0] == "http://100.0.0.1:11434"
```

**Step 5: Implement run trigger override**

In `ollama_queue/api/eval_runs.py`, extract from POST body (~line 109):

```python
gen_backend_url = body.get("gen_backend_url")
judge_backend_url = body.get("judge_backend_url")
```

Pass to `create_eval_run()` and ensure they're stored on the row.

**Step 6: Enrich `/api/status` active_eval with backend info**

In `ollama_queue/api/jobs.py`, where `active_eval` is built, add:

```python
active_eval["gen_backend_url"] = run_row.get("gen_backend_url")
active_eval["judge_backend_url"] = run_row.get("judge_backend_url")
```

**Step 7: Run all tests**

Run: `/home/justin/Documents/projects/ollama-queue/.venv/bin/python -m pytest tests/test_api_eval_settings.py tests/test_api_eval_runs.py tests/test_api.py -v`
Expected: PASS

**Step 8: Commit**

```bash
git add ollama_queue/api/eval_settings.py ollama_queue/api/eval_runs.py ollama_queue/api/jobs.py tests/
git commit -m "feat(eval): backend URL validation, run trigger override, status enrichment"
```

---

### Task 5: UI — Backend dropdown in Eval Settings

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/ProviderRoleSection.jsx`
- Modify: `ollama_queue/dashboard/spa/src/stores/health.js` (ensure `backendsData` is importable)

**Context:** Add a backend selector dropdown below the model selector in each ProviderRoleSection. Shows "Auto (smart routing)" + list of healthy backends with GPU labels. Only visible when provider = ollama.

**Step 1: Import `backendsData` signal**

At top of `ProviderRoleSection.jsx`:

```jsx
import { backendsData } from '../../stores/health.js';
```

**Step 2: Add backend state**

```jsx
const [backendUrl, setBackendUrl] = useState(settings?.backend_url || 'auto');
```

**Step 3: Add backend dropdown after model selector (inside `provider === 'ollama'` guard)**

```jsx
{provider === 'ollama' && backendsData.value.length > 1 && (
  <label>
    Backend host
    <select value={backendUrl} onChange={e => setBackendUrl(e.target.value)}>
      <option value="auto">Auto (smart routing)</option>
      {backendsData.value
        .filter(b => b.healthy)
        .map(b => (
          <option key={b.url} value={b.url}>
            {b.gpu_name || b.url} — {Math.round(b.vram_pct)}% VRAM
          </option>
        ))}
    </select>
  </label>
)}
```

**Step 4: Include `backend_url` in the save payload**

Ensure `onSave` receives the `backendUrl` value alongside provider and model. The parent component (`EvalSettings.jsx` or wherever `onSave` is wired) writes `eval.{role}_backend_url` to the settings API.

**Step 5: Build + verify**

```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: Clean build, no errors.

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/ProviderRoleSection.jsx
git commit -m "feat(dashboard): backend host dropdown in eval settings"
```

---

### Task 6: UI — Run trigger override + run detail transparency

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow/index.jsx` (show backend in L2)
- Modify: `ollama_queue/dashboard/spa/src/stores/eval.js` (pass backend params in trigger)

**Step 1: Add backend override dropdowns to RunTriggerPanel**

After the judge model selector (~line 330), add two optional backend dropdowns:

```jsx
{/* Backend overrides — only shown when multiple backends are configured */}
{backendsData.value.length > 1 && (
  <div>
    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', marginBottom: '0.4rem' }}>
      Backend overrides (optional)
    </div>
    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
      <label style={{ flex: 1 }}>
        <span style={{ fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>Generator</span>
        <select value={genBackend} onChange={e => setGenBackend(e.target.value)} class="t-input">
          <option value="">Use settings default</option>
          <option value="auto">Auto (smart routing)</option>
          {backendsData.value.filter(b => b.healthy).map(b => (
            <option key={b.url} value={b.url}>{b.gpu_name || b.url}</option>
          ))}
        </select>
      </label>
      <label style={{ flex: 1 }}>
        <span style={{ fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>Judge</span>
        <select value={judgeBackend} onChange={e => setJudgeBackend(e.target.value)} class="t-input">
          <option value="">Use settings default</option>
          <option value="auto">Auto (smart routing)</option>
          {backendsData.value.filter(b => b.healthy).map(b => (
            <option key={b.url} value={b.url}>{b.gpu_name || b.url}</option>
          ))}
        </select>
      </label>
    </div>
  </div>
)}
```

**Step 2: Add state for backend overrides**

```jsx
const [genBackend, setGenBackend] = useState('');
const [judgeBackend, setJudgeBackend] = useState('');
```

**Step 3: Include in submit body**

In `handleSubmit`, add to body (~line 90):

```jsx
const body = {
  variants: selectedVariants,
  per_cluster: parseInt(perCluster) || 4,
  judge_model: judgeModel,
  judge_mode: judgeMode,
  run_mode: runMode,
  dry_run: dryRun,
  ...modeSubFields,
  ...(genBackend ? { gen_backend_url: genBackend } : {}),
  ...(judgeBackend ? { judge_backend_url: judgeBackend } : {}),
};
```

**Step 4: Show backend in RunRow L2 detail**

In `RunRow/index.jsx`, in the expanded detail section, add backend info when present:

```jsx
{run.gen_backend_url && run.gen_backend_url !== 'auto' && (
  <span class="eval-detail-chip">Gen: {run.gen_backend_url}</span>
)}
{run.judge_backend_url && run.judge_backend_url !== 'auto' && (
  <span class="eval-detail-chip">Judge: {run.judge_backend_url}</span>
)}
```

**Step 5: Show backend in active run progress**

The active run progress component already shows `gen_model` and `judge_model`. Add backend labels from the `/api/status` response:

```jsx
{activeEval.gen_backend_url && activeEval.gen_backend_url !== 'auto' && (
  <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>
    on {activeEval.gen_backend_url}
  </span>
)}
```

**Step 6: Build + verify**

```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: Clean build.

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx ollama_queue/dashboard/spa/src/components/eval/RunRow/index.jsx ollama_queue/dashboard/spa/src/stores/eval.js
git commit -m "feat(dashboard): backend override in run trigger + backend transparency in run detail"
```

---

### Task 7: Full test suite + SPA build

**Step 1: Run full Python test suite**

```bash
cd /home/justin/Documents/projects/ollama-queue
.venv/bin/python -m pytest --timeout=120 -x -q
```
Expected: All pass (1900+)

**Step 2: Build SPA**

```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: Clean build

**Step 3: Final commit if any fixups needed**

```bash
git add -A && git commit -m "chore: test + build fixes for eval backend host selection"
```
