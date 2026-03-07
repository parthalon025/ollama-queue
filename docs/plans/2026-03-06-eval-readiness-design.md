# Eval Readiness Check Design

**Date:** 2026-03-06
**Status:** Approved

## Problem

The "Start Run" button in `RunTriggerPanel` fires unconditionally. If the lessons-db data source is offline or has no clustered lessons, the run fails silently after queuing Ollama jobs that return nothing. Users get a failed eval run with no indication of why or how to fix it.

## Solution

Pre-flight readiness check on `RunTriggerPanel` open. Three states with a one-click autofix for the priming case and a retry for the offline case.

## Readiness States

| State | Condition | Banner | Action |
|-------|-----------|--------|--------|
| ✓ Ready | reachable + cluster_count ≥ 2 + item_count ≥ 10 | Subtle green · N lessons · M clusters | None |
| ⚠ Needs priming | reachable but cluster_count < 2 or item_count < 10 | Yellow warning + counts | [Prime] |
| ✗ Offline | not reachable | Red error + URL | [Retry] |

**Thresholds:**
- `cluster_count < 2` → hard gate (cross-cluster scoring impossible without ≥2 clusters)
- `item_count < 10` → soft warning (too few lessons for meaningful sampling)

## Why Priming Works

`/eval/items` only returns lessons where `cluster_seed IS NOT NULL` AND the cluster has ≥3 members. Low `cluster_count` means lessons haven't had `cluster_seed` backfilled from the `cluster` field. The fix is a single SQL UPDATE (milliseconds):

```sql
UPDATE lessons SET cluster_seed = cluster
WHERE cluster IS NOT NULL AND cluster != '' AND cluster_seed IS NULL
```

## Architecture

### lessons-db — new `POST /eval/prime`

```python
@app.post("/eval/prime")
def eval_prime(authorization: str | None = Header(default=None)) -> dict:
    """Backfill cluster_seed from cluster field for lessons missing it.
    Mirrors the `lessons-db index --seed-only` CLI operation.
    Returns updated item_count and cluster_count (same schema as /eval/health).
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
            """SELECT COUNT(*) FROM (
                SELECT cluster_seed FROM lessons
                WHERE cluster_seed IS NOT NULL AND cluster_seed != ''
                GROUP BY cluster_seed HAVING COUNT(*) >= 3
            )"""
        ).fetchone()[0]
        return {"ok": True, "updated": updated, "item_count": item_count, "cluster_count": cluster_count}
    finally:
        conn.close()
```

**File:** `lessons-db/src/lessons_db/api.py`

### ollama-queue — new `POST /api/eval/datasource/prime`

```python
@app.post("/api/eval/datasource/prime")
def prime_eval_datasource():
    """Trigger cluster_seed backfill on the lessons-db data source.
    Calls POST {data_source_url}/eval/prime and returns the result.
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
        return {"ok": False, "updated": 0, "item_count": None, "cluster_count": None, "error": str(exc)[:200]}
```

**File:** `ollama-queue/ollama_queue/api.py`

### ollama-queue frontend

**`store.js` — add `primeDataSource()`:**
```js
export async function primeDataSource() {
  const res = await fetch(`${API}/eval/datasource/prime`, { method: 'POST' });
  if (!res.ok) throw new Error(`Prime failed: HTTP ${res.status}`);
  return res.json();
}
```

**`RunTriggerPanel.jsx` — readiness check on open:**

```jsx
// On mount (when open=true): check readiness
useEffect(() => {
  if (!open) return;
  setReadiness({ phase: 'checking' });
  testDataSource()
    .then(result => {
      if (!result || !result.ok) {
        setReadiness({ phase: 'offline', error: result?.error });
      } else if (result.cluster_count < 2 || result.item_count < 10) {
        setReadiness({ phase: 'needs_prime', item_count: result.item_count, cluster_count: result.cluster_count });
      } else {
        setReadiness({ phase: 'ready', item_count: result.item_count, cluster_count: result.cluster_count });
      }
    })
    .catch(err => setReadiness({ phase: 'offline', error: err.message }));
}, [open]);
```

Readiness banner renders above the form, outside the `{open && ...}` guard. Uses `useActionFeedback` for the Prime button.

## Scope

| File | Repo | Change |
|------|------|--------|
| `src/lessons_db/api.py` | lessons-db | Add `POST /eval/prime` |
| `tests/test_api.py` | lessons-db | 2 tests: prime backfills, prime returns counts |
| `ollama_queue/api.py` | ollama-queue | Add `POST /api/eval/datasource/prime` |
| `tests/test_api_eval_settings.py` | ollama-queue | 2 tests: prime proxies OK, prime handles offline |
| `spa/src/store.js` | ollama-queue | Add `primeDataSource()` |
| `spa/src/components/eval/RunTriggerPanel.jsx` | ollama-queue | Readiness check + banner + Prime/Retry buttons |

## Constraints

- No new npm dependencies
- `POST /eval/prime` is synchronous (SQL UPDATE runs in milliseconds)
- Banner does not block form submission (warn, don't gate — user may dry-run)
- Offline case: [Retry] re-calls `testDataSource()`, does not attempt a fix
- Auth token forwarded from `eval.data_source_token` setting (same as existing endpoints)
- Layman comments required on all new JSX
