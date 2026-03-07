# Eval Readiness Check Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show a pre-flight readiness banner in the Start Run panel that checks whether the lessons-db is primed (has clustered lessons), and lets the user fix it in one click.

**Architecture:** Three tasks across two repos. Task 1 adds `POST /eval/prime` to the lessons-db API (SQL backfill, milliseconds). Task 2 adds `POST /api/eval/datasource/prime` to the ollama-queue API (proxies to lessons-db). Task 3 wires the readiness check + banner into `RunTriggerPanel.jsx`.

**Tech Stack:** Python / FastAPI / httpx (backend), Preact signals + `useActionFeedback` hook (frontend), pytest (tests)

---

## Context: How priming works

The eval pipeline fetches lessons from `{data_source_url}/eval/items`. That endpoint only returns lessons where `cluster_seed IS NOT NULL AND cluster_seed != ''` AND the cluster has ≥3 members. Lessons without a `cluster_seed` are invisible to eval.

The "prime" operation runs one SQL UPDATE to copy the `cluster` field → `cluster_seed` for lessons missing it. It's fast (milliseconds) and idempotent.

```sql
UPDATE lessons SET cluster_seed = cluster
WHERE cluster IS NOT NULL AND cluster != '' AND cluster_seed IS NULL
```

**Readiness thresholds:**
- `cluster_count < 2` → needs priming (can't score cross-cluster without ≥2 clusters)
- `item_count < 10` → low coverage warning (also triggers Prime button)
- Not reachable → offline (Retry only, can't autofix)

---

## Task 1: lessons-db — `POST /eval/prime` endpoint

**Repo:** `~/Documents/projects/lessons-db`
**Files:**
- Modify: `src/lessons_db/api.py` — after `eval_health` function (~line 885, before `eval_items`)
- Test: `tests/test_api.py` — append at bottom

### Step 1: Write the failing tests

Append to `tests/test_api.py`:

```python
def test_post_eval_prime_returns_ok_on_empty_db(client):
    """POST /eval/prime on empty DB returns ok=True with zero counts."""
    resp = client.post("/eval/prime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["updated"] == 0
    assert data["item_count"] == 0
    assert data["cluster_count"] == 0


def test_post_eval_prime_backfills_cluster_seed(client, db_path):
    """POST /eval/prime sets cluster_seed = cluster for lessons missing it.

    Seeds 3 lessons in the same cluster with cluster_seed=NULL (cluster qualifies at >= 3).
    After prime, all 3 get cluster_seed set and appear in the cluster count.
    """
    import sqlite3
    from datetime import date

    conn = sqlite3.connect(str(db_path))
    for i in range(3):
        conn.execute(
            "INSERT INTO lessons (title, created_date, cluster) VALUES (?, ?, ?)",
            (f"Lesson {i}", date.today().isoformat(), "grp-A"),
        )
    conn.commit()
    conn.close()

    resp = client.post("/eval/prime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["updated"] == 3
    assert data["cluster_count"] >= 1
    assert data["item_count"] >= 3
```

### Step 2: Run tests to verify they fail

```bash
cd ~/Documents/projects/lessons-db
source .venv/bin/activate
pytest tests/test_api.py::test_post_eval_prime_returns_ok_on_empty_db tests/test_api.py::test_post_eval_prime_backfills_cluster_seed -v
```

Expected: FAIL — `404 Not Found` (endpoint doesn't exist yet)

### Step 3: Implement `POST /eval/prime` in `src/lessons_db/api.py`

Add after the closing `finally` block of `eval_health` (around line 885, before `@app.get("/eval/items")`):

```python
    @app.post("/eval/prime")
    def eval_prime(authorization: str | None = Header(default=None)) -> dict:
        """Backfill cluster_seed from cluster field for lessons that are missing it.

        Mirrors `lessons-db index --seed-only`. After this runs, lessons with a
        cluster assignment become eligible to appear in /eval/items (which requires
        cluster_seed to be set). Safe to call repeatedly — only affects NULL rows.
        """
        _check_eval_auth(authorization)
        conn = get_conn()
        try:
            cur = conn.execute(
                "UPDATE lessons SET cluster_seed = cluster "
                "WHERE cluster IS NOT NULL AND cluster != '' AND cluster_seed IS NULL"
            )
            updated = cur.rowcount
            conn.commit()
            item_count = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
            cluster_count = conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT cluster_seed
                    FROM lessons
                    WHERE cluster_seed IS NOT NULL AND cluster_seed != ''
                    GROUP BY cluster_seed
                    HAVING COUNT(*) >= 3
                )
                """
            ).fetchone()[0]
            return {"ok": True, "updated": updated, "item_count": item_count, "cluster_count": cluster_count}
        finally:
            conn.close()
```

### Step 4: Run tests to verify they pass

```bash
pytest tests/test_api.py::test_post_eval_prime_returns_ok_on_empty_db tests/test_api.py::test_post_eval_prime_backfills_cluster_seed -v
```

Expected: 2 PASSED

### Step 5: Run full lessons-db suite to verify no regressions

```bash
pytest --timeout=120 -x -q -n 6
```

Expected: all existing tests pass + 2 new

### Step 6: Commit

```bash
cd ~/Documents/projects/lessons-db
git add src/lessons_db/api.py tests/test_api.py
git commit -m "feat(eval): add POST /eval/prime endpoint — backfills cluster_seed for eval readiness"
```

---

## Task 2: ollama-queue — `POST /api/eval/datasource/prime` proxy endpoint

**Repo:** `~/Documents/projects/ollama-queue`
**Files:**
- Modify: `ollama_queue/api.py` — after `test_eval_datasource` function (~line 1344, before `# --- Eval: Settings ---`)
- Test: `tests/test_api_eval_settings.py` — append at bottom

### Step 1: Write the failing tests

Append to `tests/test_api_eval_settings.py`:

```python
def test_post_eval_datasource_prime_proxies_and_returns_result(client):
    """POST /api/eval/datasource/prime proxies to data source and returns its response."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True, "updated": 3, "item_count": 10, "cluster_count": 2}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response):
        resp = client.post("/api/eval/datasource/prime")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["updated"] == 3
    assert data["cluster_count"] == 2


def test_post_eval_datasource_prime_returns_ok_false_when_offline(client):
    """POST /api/eval/datasource/prime returns ok=False when data source is unreachable."""
    with patch("httpx.post", side_effect=Exception("Connection refused")):
        resp = client.post("/api/eval/datasource/prime")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data
    assert data["updated"] == 0
```

### Step 2: Run tests to verify they fail

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest tests/test_api_eval_settings.py::test_post_eval_datasource_prime_proxies_and_returns_result tests/test_api_eval_settings.py::test_post_eval_datasource_prime_returns_ok_false_when_offline -v
```

Expected: FAIL — `405 Method Not Allowed` or `404`

### Step 3: Implement `POST /api/eval/datasource/prime` in `ollama_queue/api.py`

Add after the closing `except` block of `test_eval_datasource` (after line ~1344, before `# --- Eval: Settings ---`):

```python
    @app.post("/api/eval/datasource/prime")
    def prime_eval_datasource():
        """Trigger cluster_seed backfill on the lessons-db data source.

        What it shows: nothing — fires a POST to the configured data source's /eval/prime endpoint.
        Decision it drives: after this runs, /eval/items returns lessons that were previously
          invisible because they had cluster set but cluster_seed missing.
        Calls POST {data_source_url}/eval/prime with a 15s timeout and returns the result.
        Returns ok=False with error message if the data source is unreachable.
        """
        data_source_url = db.get_setting("eval.data_source_url") or "http://127.0.0.1:7685"
        token = db.get_setting("eval.data_source_token") or ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{data_source_url.rstrip('/')}/eval/prime"
        try:
            resp = httpx.post(url, headers=headers, timeout=15.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            _log.warning("eval datasource prime failed: %s", exc)
            return {"ok": False, "updated": 0, "item_count": None, "cluster_count": None, "error": str(exc)[:200]}
```

### Step 4: Run tests to verify they pass

```bash
pytest tests/test_api_eval_settings.py::test_post_eval_datasource_prime_proxies_and_returns_result tests/test_api_eval_settings.py::test_post_eval_datasource_prime_returns_ok_false_when_offline -v
```

Expected: 2 PASSED

### Step 5: Run full ollama-queue suite

```bash
pytest --timeout=120 -x -q
```

Expected: all 541 existing + 2 new = 543 pass

### Step 6: Commit

```bash
git add ollama_queue/api.py tests/test_api_eval_settings.py
git commit -m "feat(eval): add POST /api/eval/datasource/prime proxy endpoint"
```

---

## Task 3: ollama-queue frontend — readiness banner in RunTriggerPanel

**Repo:** `~/Documents/projects/ollama-queue`
**Files:**
- Modify: `ollama_queue/dashboard/spa/src/store.js` — after `testDataSource()`
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`
- Build: `cd ollama_queue/dashboard/spa && npm run build`

No automated tests for this task — verify manually by opening the Runs tab.

### Step 1: Add `primeDataSource()` to `store.js`

In `store.js`, after the `testDataSource` function (after line ~140):

```js
// What it shows: nothing — triggers cluster_seed backfill on the data source
// Decision it drives: after this resolves, /eval/items will return lessons that
//   were previously missing cluster_seed, making them visible to the eval pipeline
export async function primeDataSource() {
  const res = await fetch(`${API}/eval/datasource/prime`, { method: 'POST' });
  if (!res.ok) throw new Error(`Prime failed: HTTP ${res.status}`);
  return res.json();
}
```

### Step 2: Wire readiness check into `RunTriggerPanel.jsx`

**Import change** — add `primeDataSource` to the import from store:

```jsx
import {
  evalVariants, evalSettings,
  triggerEvalRun, fetchEvalRuns, startEvalPoll, evalActiveRun,
  testDataSource, primeDataSource,
} from '../../store.js';
```

**Add readiness state** — after the existing `useState` calls, before `useActionFeedback`:

```jsx
// null = not checked yet, 'checking', 'ready', 'needs_prime', 'offline'
const [readiness, setReadiness] = useState(null);
const [primeFb, primeAct] = useActionFeedback();
```

Note: `useActionFeedback` is imported from `'../../hooks/useActionFeedback.js'` — it's already in the file. Add a second instance `primeFb/primeAct` for the Prime button. The existing `fb/act` stays for the Start Run button.

**Add readiness check effect** — after existing `useState` calls, before `handleSubmit`:

```jsx
// Check readiness when panel opens. Re-runs whenever `open` toggles true.
useEffect(() => {
  if (!open) return;
  setReadiness({ phase: 'checking' });
  testDataSource()
    .then(result => {
      if (!result || !result.ok) {
        setReadiness({ phase: 'offline', error: result?.error || 'No response' });
      } else if (result.cluster_count < 2 || result.item_count < 10) {
        setReadiness({ phase: 'needs_prime', item_count: result.item_count ?? 0, cluster_count: result.cluster_count ?? 0 });
      } else {
        setReadiness({ phase: 'ready', item_count: result.item_count, cluster_count: result.cluster_count });
      }
    })
    .catch(err => setReadiness({ phase: 'offline', error: err.message }));
}, [open]);
```

**Add readiness banner** — inside the `{open && (...)}` block, as the FIRST child before the `<form>`:

```jsx
{/* Readiness banner — shows whether lessons-db has enough data to run eval.
    Checks on open; Prime button triggers cluster_seed backfill; Retry re-checks. */}
{readiness && readiness.phase !== 'checking' && (
  <div class={`eval-readiness eval-readiness--${readiness.phase}`}>
    {readiness.phase === 'ready' && (
      <span>✓ Ready · {readiness.item_count} lessons · {readiness.cluster_count} clusters</span>
    )}
    {readiness.phase === 'needs_prime' && (
      <span>
        ⚠ Needs priming · {readiness.item_count} lessons · {readiness.cluster_count} clusters
        {' '}
        <button
          type="button"
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '2px 8px', marginLeft: '0.5rem' }}
          disabled={primeFb.phase === 'loading'}
          onClick={() => primeAct('Priming…', () => primeDataSource(), result => {
            // After prime succeeds, update readiness from returned counts
            if (result.cluster_count >= 2 && result.item_count >= 10) {
              setReadiness({ phase: 'ready', item_count: result.item_count, cluster_count: result.cluster_count });
            } else {
              setReadiness({ phase: 'needs_prime', item_count: result.item_count ?? 0, cluster_count: result.cluster_count ?? 0 });
            }
            return `Primed · ${result.updated} updated`;
          })}
        >
          {primeFb.phase === 'loading' ? 'Priming…' : 'Prime'}
        </button>
        {primeFb.msg && <span class={`action-fb action-fb--${primeFb.phase}`} style={{ marginLeft: '0.5rem' }}>{primeFb.msg}</span>}
      </span>
    )}
    {readiness.phase === 'offline' && (
      <span>
        ✗ Data source offline · {readiness.error}
        {' '}
        <button
          type="button"
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '2px 8px', marginLeft: '0.5rem' }}
          onClick={() => {
            setReadiness({ phase: 'checking' });
            testDataSource()
              .then(result => {
                if (!result || !result.ok) {
                  setReadiness({ phase: 'offline', error: result?.error || 'No response' });
                } else if (result.cluster_count < 2 || result.item_count < 10) {
                  setReadiness({ phase: 'needs_prime', item_count: result.item_count ?? 0, cluster_count: result.cluster_count ?? 0 });
                } else {
                  setReadiness({ phase: 'ready', item_count: result.item_count, cluster_count: result.cluster_count });
                }
              })
              .catch(err => setReadiness({ phase: 'offline', error: err.message }));
          }}
        >
          Retry
        </button>
      </span>
    )}
  </div>
)}
```

**Add CSS** — in `src/index.css`, append:

```css
.eval-readiness { padding: 6px 10px; border-radius: 4px; font-family: var(--font-mono); font-size: var(--type-label); margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.5rem; }
.eval-readiness--ready { background: var(--status-ok, #1a3a1a); color: var(--text-secondary); }
.eval-readiness--needs_prime { background: var(--status-warn, #2a2010); color: var(--status-warn-text, #c8a040); }
.eval-readiness--offline { background: var(--status-error, #2a1010); color: var(--status-error-text, #c84040); }
```

### Step 3: Build the SPA

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build
```

Expected output: `dist/bundle.js  ~237kb` — no errors

### Step 4: Manual smoke test

```bash
systemctl --user restart ollama-queue.service
```

Open `/queue/ui/` → Eval tab → Runs sub-tab → expand "Configure run".
Verify:
- ✓ Ready shows when lessons-db is running with data
- ⚠ Needs priming shows when cluster_count < 2 (temporarily run `UPDATE lessons SET cluster_seed = NULL` on the test DB to simulate)
- ✗ Offline shows when lessons-db is stopped (`systemctl --user stop lessons-db.service`)
- Prime button updates banner to ✓ Ready on success
- Retry re-checks connectivity

### Step 5: Commit

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/store.js \
        ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx \
        ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(eval): readiness banner in RunTriggerPanel — Prime/Retry from UI"
```

---

## Final verification

Run full test suite to confirm nothing broke:

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
```

Expected: 543 passed (541 existing + 2 new prime proxy tests)

Then update `CLAUDE.md` test count: 541 → 543, `test_api_eval_settings.py` 18 → 20.

Push both repos:

```bash
cd ~/Documents/projects/ollama-queue && git push origin main
cd ~/Documents/projects/lessons-db && git push origin main
```
