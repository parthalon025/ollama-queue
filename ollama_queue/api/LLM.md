# api/ LLM Guide

## What You Must Know

The API layer exposes 90+ FastAPI endpoints across 12 router modules. Every route accesses the database through a closure-captured module-level reference, not dependency injection.

## DB Access Pattern

Every route module follows this pattern:

```python
import ollama_queue.api as _api

router = APIRouter()

@router.get("/api/something")
def get_something():
    db = _api.db  # module-level ref, set once at startup
    # use db...
```

`register_routes(app, db_instance)` in `__init__.py` sets `_api.db` and includes all routers. Test injection: `create_app(db)` passes the test DB.

## Route Registration Order

**Fixed paths before parameterized paths.** FastAPI matches routes in registration order.

```python
# In __init__.py — eval_variants MUST come before eval_runs
app.include_router(eval_variants.router)   # has /api/eval/variants/stability
app.include_router(eval_runs.router)       # has /api/eval/runs/{run_id}
```

Within `eval_variants.py`, `/stability` must be defined before `/{variant_id}`. Otherwise `GET /api/eval/variants/stability` matches `variant_id="stability"` and returns 404.

## Adding a New Endpoint

1. Add to existing `api/<domain>.py` or create new file with `router = APIRouter()`
2. If new file: import and `app.include_router()` in `__init__.py`
3. Use `db = _api.db` at handler top (not as parameter)
4. For write operations, wrap DB calls in `with db._lock:`
5. Return dicts -- FastAPI auto-serializes to JSON

### New endpoint template:

```python
@router.post("/api/myfeature/action")
def my_action(body: dict = Body(...)):
    db = _api.db
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE ...", (...))
        conn.commit()
    return {"ok": True}
```

## Proxy Endpoints

`/api/generate` and `/api/embed` proxy to Ollama at `:11434` with queue serialization:

- Poll `try_claim_for_proxy()` until sentinel `current_job_id=-1` is set
- Extract `_priority`, `_source`, `_timeout` from body before forwarding
- Streaming: `_iter_ndjson()` yields line-aligned NDJSON, releases claim on `done=true`
- Never call the proxy from within a queue job (deadlock -- daemon holds the sentinel)

## Background Threads

Long operations MUST spawn daemon threads. The daemon does NOT poll `eval_runs` for pending work.

```python
import threading
threading.Thread(target=run_eval_session, args=(db, run_id, ...), daemon=True).start()
```

Key cases: `repeat_eval_run` (must start thread, not just create DB row), `judge_rerun` (must copy gen_results first), description generation.

## Security

- **Token masking**: `GET /api/eval/settings` returns `"***"` for `eval.data_source_token`
- **SSRF protection**: `PUT /api/eval/settings` rejects `data_source_url` not targeting `127.0.0.1`/`localhost`
- **SPA path traversal**: `spa_static()` in `app.py` resolves paths and checks `is_relative_to(spa_dir)`

## Action Button Feedback (SPA)

All action buttons use the `useActionFeedback` hook:

```jsx
const [fb, act] = useActionFeedback();
<button disabled={fb.phase === 'loading'}
  onClick={() => act('Loading...', fn, result => `Done: ${result.id}`)}>
```

Rules: hook calls before any `return null` guard (Rules of Hooks), specific success labels ("Run #12 started" not "Done"), one hook instance per button.

## Error Handling

- `fetch()` resolves on 4xx/5xx -- always check `res.ok` and throw on failure
- Eval endpoints: run-not-found -> 404, not-complete/no-winner -> 400, upstream failure -> 502
- `prime_eval_datasource` raises HTTP 502 on upstream failure, not 200 with `ok=False`

## Testing

```bash
pytest tests/test_api.py tests/test_api_eval_runs.py tests/test_api_eval_settings.py -x
pytest tests/test_api_cov_{a,b,c,d}.py -x  # coverage gap tests
pytest tests/test_proxy.py tests/test_embed_proxy.py -x
```

Test pattern: `TestClient(create_app(db))` with `db` fixture from conftest.

## Dependencies

- **Depends on**: db/, eval/, models/, config/, sensing/, scheduling/
- **Depended on by**: app.py (registration), eval/engine.py (proxy HTTP calls to self)
