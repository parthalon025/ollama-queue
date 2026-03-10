"""Eval engine shared core: DB helpers, HTTP proxy, metrics, report, and session orchestrator.

All DB-touching helpers live here. The generate, judge, and promote modules
import from this module for shared infrastructure.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)

_RETRYABLE_CODES = {429, 502, 503, 504}  # 429 = rate-limit; 504 = proxy claim-wait timeout; retry
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 2.0


class _ProxyDownError(Exception):
    """Raised when the ollama-queue proxy itself is unreachable (e.g. service restarting).

    This is distinct from Ollama returning a bad response — the proxy being down
    means the process hosting it is stopping. It should abort the run cleanly
    rather than counting toward the circuit breaker's error budget.
    """


# ---------------------------------------------------------------------------
# DB helper functions (standalone — do NOT modify db.py)
# ---------------------------------------------------------------------------


def get_eval_run(db: Database, run_id: int) -> dict | None:
    """Fetch a single eval_runs row by id."""
    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def create_eval_run(
    db: Database,
    variant_id: str,
    run_mode: str = "batch",
    label: str | None = None,
    cluster_id: str | None = None,
    scheduled_by: str | None = None,
    data_source_url: str | None = None,
    data_source_token: str | None = None,
    seed: int | None = None,
    item_ids: str | None = None,
    variants: list[str] | None = None,
    per_cluster: int = 4,
    max_runs: int | None = None,
    max_time_s: int | None = None,
) -> int:
    """Insert a new eval_runs row with status='queued' and return the new id.

    Uses db._lock + db._connect() directly — do NOT modify db.py for this helper.
    data_source_url defaults to the 'eval.data_source_url' setting when not provided.

    variants: if provided, overrides the variants column with a JSON array so
    run_eval_generate iterates all of them. variant_id is the primary/first variant.
    max_runs/max_time_s: fill-open-slots limits (NULL = unlimited).
    """
    import json as _json
    from datetime import UTC
    from datetime import datetime as _dt

    now = _dt.now(UTC).isoformat()
    resolved_url = data_source_url or db.get_setting("eval.data_source_url") or "http://127.0.0.1:7685"
    # variants column: JSON array when multiple variants supplied, plain id otherwise
    variants_value = _json.dumps(variants) if variants and len(variants) > 1 else variant_id
    with db._lock:
        conn = db._connect()
        cur = conn.execute(
            """INSERT INTO eval_runs
               (variant_id, variants, run_mode, label, cluster_id, scheduled_by,
                data_source_url, data_source_token, seed, item_ids, status,
                per_cluster, max_runs, max_time_s, created_at, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
            (
                variant_id,
                variants_value,
                run_mode,
                label,
                cluster_id,
                scheduled_by,
                resolved_url,
                data_source_token,
                seed,
                item_ids,
                per_cluster,
                max_runs,
                max_time_s,
                now,
                now,  # started_at = created_at for compatibility (NOT NULL constraint)
            ),
        )
        conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid


def get_eval_variant(db: Database, variant_id: str) -> dict | None:
    """Fetch a single eval_variants row by id."""
    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT * FROM eval_variants WHERE id = ?", (variant_id,)).fetchone()
        return dict(row) if row else None


def get_eval_template(db: Database, template_id: str) -> dict | None:
    """Fetch a single eval_prompt_templates row by id."""
    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT * FROM eval_prompt_templates WHERE id = ?", (template_id,)).fetchone()
        return dict(row) if row else None


def update_eval_run(db: Database, run_id: int, **kwargs: Any) -> None:
    """UPDATE eval_runs SET <kwargs> WHERE id=run_id."""
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = [*list(kwargs.values()), run_id]
    with db._lock:
        conn = db._connect()
        conn.execute(f"UPDATE eval_runs SET {cols} WHERE id = ?", vals)
        conn.commit()


def update_eval_variant(db: Database, variant_id: str, **kwargs: Any) -> None:
    """UPDATE eval_variants SET <kwargs> WHERE id=variant_id."""
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = [*list(kwargs.values()), variant_id]
    with db._lock:
        conn = db._connect()
        conn.execute(f"UPDATE eval_variants SET {cols} WHERE id = ?", vals)
        conn.commit()


def insert_eval_result(db: Database, **kwargs: Any) -> int:
    """INSERT OR IGNORE INTO eval_results and return the new (or existing) row id.

    INSERT OR IGNORE handles restarts mid-judge-run: if the UNIQUE constraint on
    (run_id, variant, source_item_id, target_item_id, row_type) fires, the row
    already exists and we skip the insert without raising.
    """
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    vals = list(kwargs.values())
    with db._lock:
        conn = db._connect()
        cur = conn.execute(f"INSERT OR IGNORE INTO eval_results ({cols}) VALUES ({placeholders})", vals)
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        # Row already existed — fetch its id via the unique key
        row_type = kwargs.get("row_type", "judge")
        _q = (
            "SELECT id FROM eval_results"
            " WHERE run_id=? AND variant=? AND source_item_id=? AND target_item_id=? AND row_type=?"
        )
        existing = conn.execute(
            _q,
            (kwargs["run_id"], kwargs["variant"], kwargs["source_item_id"], kwargs["target_item_id"], row_type),
        ).fetchone()
        if existing is None:
            raise RuntimeError("insert_eval_result: row not found after INSERT OR IGNORE")
        return existing[0]


def update_eval_result(db: Database, result_id: int, **kwargs: Any) -> None:
    """UPDATE eval_results SET <kwargs> WHERE id=result_id."""
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = [*list(kwargs.values()), result_id]
    with db._lock:
        conn = db._connect()
        conn.execute(f"UPDATE eval_results SET {cols} WHERE id = ?", vals)
        conn.commit()


# ---------------------------------------------------------------------------
# HTTP helpers (httpx — already in requirements)
# ---------------------------------------------------------------------------


def _call_proxy(
    http_base: str,
    model: str,
    prompt: str,
    temperature: float,
    num_ctx: int,
    timeout: int,
    source: str,
    priority: int = 2,
) -> tuple[str | None, int | None]:
    """POST to the ollama-queue proxy and return (response_text, queue_job_id).

    Returns (None, None) on any error. Retries on 502/503.
    Strips <think>...</think> from the raw response text.
    """
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
        "_priority": priority,
        "_source": source,
        "_timeout": timeout,
    }

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
            text = (data.get("response") or "").strip()
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            text = text.strip("\"'").strip()
            queue_job_id = data.get("_queue_job_id")
            return (text if text else None, queue_job_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRYABLE_CODES and attempt < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                _log.warning("proxy %d retry in %.0fs", exc.response.status_code, delay)
                time.sleep(delay)
                last_exc = exc
                continue
            _log.warning("proxy HTTP error: %s", exc)
            return None, None
        except httpx.ConnectError as exc:
            # The proxy itself is down — the service is likely restarting.
            # Raise _ProxyDownError so loops can abort cleanly without tripping the circuit breaker.
            raise _ProxyDownError(str(exc)) from exc
        except httpx.TimeoutException:
            _log.warning("proxy timeout for model=%s (attempt %d/%d)", model, attempt + 1, _MAX_RETRIES + 1)
            last_exc = Exception(f"timeout model={model}")
            return None, None
        except Exception as exc:
            _log.exception("proxy unexpected error for model=%s: %s", model, exc)
            last_exc = exc
            return None, None

    _log.warning("proxy exhausted retries: %s", last_exc)
    return None, None


def _fetch_items(data_source_url: str, token: str = "") -> list[dict]:
    """GET {data_source_url}/eval/items and return list of item dicts."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{data_source_url}/eval/items", headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        _log.error("fetch items failed: %s", exc)
        return []


def _fetch_clusters(data_source_url: str, token: str = "") -> list[dict]:
    """GET {data_source_url}/eval/clusters and return list of cluster dicts."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{data_source_url}/eval/clusters", headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        _log.error("fetch clusters failed: %s", exc)
        return []


def _get_eval_setting(db: Database, key: str, default: Any = None) -> Any:
    """Read a single eval.* setting from the settings table (JSON-decoded)."""
    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _build_items_by_cluster(items: list[dict]) -> dict[str, list[dict]]:
    """Group items by cluster_id (or cluster_seed for compatibility)."""
    mapping: dict[str, list[dict]] = {}
    for item in items:
        cid = str(item.get("cluster_id") or item.get("cluster_seed") or "")
        mapping.setdefault(cid, []).append(item)
    return mapping


_OPPORTUNISTIC_THROTTLE_SLEEP_S = 30  # seconds to sleep when resources are high
_RESOURCE_HIGH_THRESHOLD = 80  # percent threshold for opportunistic throttling


def _should_throttle(db: Database) -> bool:
    """Return True if system resources are too high for opportunistic scheduling.

    Checks ram_pct, vram_pct, and cpu_pct from db.get_current_health().
    Falls open on any failure — a health check error should not stall the eval run.
    Uses the most recent health_log row as a fallback when get_current_health()
    is not available on the Database instance.
    """
    try:
        if hasattr(db, "get_current_health"):
            health = db.get_current_health()
        else:
            rows = db.get_health_log(hours=1)
            if not rows:
                return False
            health = rows[0]

        return (
            health.get("ram_pct", 0) > _RESOURCE_HIGH_THRESHOLD
            or health.get("vram_pct", 0) > _RESOURCE_HIGH_THRESHOLD
            or health.get("cpu_pct", 0) > _RESOURCE_HIGH_THRESHOLD
        )
    except Exception as exc:
        _log.warning("health check failed during throttle check: %s", exc)
        return False  # fail open — don't stall on health check failure


def _check_fill_open_slots_limit(
    run_id: int,
    submitted: int,
    max_runs: int | None,
    max_time_s: int | None,
    fill_start_time: float,
) -> bool:
    """Return True if a fill-open-slots limit has been reached.

    Checks count limit (max_runs) before time limit (max_time_s) so that when
    both are set, the count cap is evaluated first.
    """
    if max_runs is not None and submitted >= max_runs:
        _log.info(
            "fill-open-slots run %d: max_runs=%d reached after %d submitted — stopping",
            run_id,
            max_runs,
            submitted,
        )
        return True
    if max_time_s is not None:
        elapsed = time.monotonic() - fill_start_time
        if elapsed >= max_time_s:
            _log.info(
                "fill-open-slots run %d: max_time_s=%d reached (elapsed=%.1fs) after %d submitted — stopping",
                run_id,
                max_time_s,
                elapsed,
                submitted,
            )
            return True
    return False


def _ensure_seed(db: Database, run_id: int, run: dict) -> None:
    """Generate and persist a random seed if the run has none.

    Called before the generation loop so that run_eval_judge() can use the
    same seed for deterministic target selection on the first run.
    """
    if run.get("seed") is None:
        generated_seed = random.randint(0, 2**31 - 1)  # noqa: S311 — not crypto
        update_eval_run(db, run_id, seed=generated_seed)
        _log.info("_ensure_seed: generated seed=%d for run_id=%d", generated_seed, run_id)


def _select_judge_targets(
    *,
    source_item_id: str,
    source_cid: str,
    items: list[dict],
    items_by_cluster: dict[str, list[dict]],
    rng: random.Random,
    same_count: int,
    diff_count: int,
) -> tuple[list[dict], list[dict]]:
    """Return (same_targets, diff_targets) for one gen_result, deterministically."""
    # Sort by id before slicing/shuffling so selection is stable regardless of
    # the order items arrive from the remote data source endpoint.
    same_pool = sorted(
        (it for it in items_by_cluster.get(source_cid, []) if str(it["id"]) != source_item_id),
        key=lambda it: str(it["id"]),
    )
    same_targets = same_pool[:same_count]

    diff_pool = sorted(
        (
            it
            for it in items
            if str(it.get("cluster_id") or it.get("cluster_seed") or "") != source_cid
            and str(it["id"]) != source_item_id
        ),
        key=lambda it: str(it["id"]),
    )
    rng_copy = random.Random(rng.random())  # noqa: S311 — not crypto, deterministic eval selection
    rng_copy.shuffle(diff_pool)
    diff_targets = diff_pool[:diff_count]
    return same_targets, diff_targets


def _fetch_scored_rows(db: Database, run_id: int) -> list[dict]:
    """Fetch all scored eval_results for a run (effective scores, no errors)."""
    with db._lock:
        conn = db._connect()
        return [
            dict(r)
            for r in conn.execute(
                """SELECT variant, is_same_cluster,
                          COALESCE(override_score_transfer, score_transfer) AS effective_score_transfer,
                          COALESCE(override_score_precision, score_precision) AS effective_score_precision,
                          COALESCE(override_score_action, score_action) AS effective_score_action,
                          source_cluster_id, target_cluster_id
                   FROM eval_results
                   WHERE run_id = ?
                     AND row_type = 'judge'
                     AND score_transfer IS NOT NULL
                     AND error IS NULL""",
                (run_id,),
            ).fetchall()
        ]


def _fetch_v2_scored_rows(db: Database, run_id: int) -> list[dict]:
    """Fetch V2 scored rows with paired/Bayesian columns for tournament and fusion metrics."""
    with db._lock:
        conn = db._connect()
        return [
            dict(r)
            for r in conn.execute(
                """SELECT variant, is_same_cluster AS is_same_group,
                          score_paired_winner, score_posterior AS posterior,
                          score_embedding_sim, score_mechanism_match,
                          mechanism_trigger, mechanism_target, mechanism_fix,
                          principle
                   FROM eval_results
                   WHERE run_id = ?
                     AND row_type = 'judge'
                     AND error IS NULL""",
                (run_id,),
            ).fetchall()
        ]


def _fetch_analysis_samples(
    db: Database,
    run_id: int,
    n: int = 4,
) -> tuple[list[dict], list[dict]]:
    """Return (top_n, bottom_n) same-cluster judge pairs for the analysis prompt.

    top_n: highest effective transfer scores (shows what worked).
    bottom_n: lowest effective transfer scores (shows where the model struggled).
    Only same-cluster pairs used: these are the true positives / false negatives
    and give the most diagnostic signal for why recall is high or low.
    """
    with db._lock:
        conn = db._connect()
        top = [
            dict(r)
            for r in conn.execute(
                """SELECT variant, principle, score_transfer
                   FROM eval_results
                   WHERE run_id = ? AND row_type = 'judge' AND is_same_cluster = 1
                     AND score_transfer IS NOT NULL AND error IS NULL AND principle IS NOT NULL
                   ORDER BY COALESCE(override_score_transfer, score_transfer) DESC
                   LIMIT ?""",
                (run_id, n),
            ).fetchall()
        ]
        bottom = [
            dict(r)
            for r in conn.execute(
                """SELECT variant, principle, score_transfer
                   FROM eval_results
                   WHERE run_id = ? AND row_type = 'judge' AND is_same_cluster = 1
                     AND score_transfer IS NOT NULL AND error IS NULL AND principle IS NOT NULL
                   ORDER BY COALESCE(override_score_transfer, score_transfer) ASC
                   LIMIT ?""",
                (run_id, n),
            ).fetchall()
        ]
    return top, bottom


from ollama_queue.eval.metrics import (  # noqa: F401
    compute_bayesian_metrics,
    compute_metrics,
    compute_tournament_metrics,
    render_report,
)

# ---------------------------------------------------------------------------
# Post-run structured analysis (analysis_json)
# ---------------------------------------------------------------------------


def compute_run_analysis(run_id: int, db: Database) -> None:
    """Compute structured analysis and store as analysis_json on eval_runs.

    Never raises — all exceptions caught and logged (same pattern as
    check_auto_promote).
    """
    try:
        _compute_run_analysis_inner(run_id, db)
    except Exception:
        _log.exception("compute_run_analysis failed for run %s (non-fatal)", run_id)


def _compute_run_analysis_inner(run_id: int, db: Database) -> None:
    """Inner implementation — may raise."""
    from ollama_queue.eval.analysis import (
        bootstrap_f1_ci,
        compute_per_item_breakdown,
        extract_failure_cases,
    )

    with db._lock:
        conn = db._connect()
        run = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            _log.warning("compute_run_analysis: run %s not found", run_id)
            return

        rows = conn.execute(
            "SELECT * FROM eval_results WHERE run_id = ? AND row_type = 'judge' AND error IS NULL",
            (run_id,),
        ).fetchall()

    if not rows:
        _log.info("compute_run_analysis: no scored rows for run %s", run_id)
        return

    scored = [dict(r) for r in rows]

    # Read positive threshold from settings
    threshold = 3
    try:
        with db._lock:
            conn = db._connect()
            row = conn.execute("SELECT value FROM settings WHERE key = 'eval.positive_threshold'").fetchone()
            if row:
                threshold = int(json.loads(row["value"]))
    except Exception:
        _log.debug("compute_run_analysis: could not read positive_threshold, using default=%d", threshold)

    # Parse variant IDs from run
    variants_raw = run["variants"]
    try:
        variant_ids = json.loads(variants_raw) if isinstance(variants_raw, str) else [variants_raw]
    except (json.JSONDecodeError, TypeError):
        variant_ids = []

    # Compute all three analysis types
    per_item = compute_per_item_breakdown(scored, positive_threshold=threshold)
    failures = extract_failure_cases(scored, positive_threshold=threshold)
    ci: dict[str, Any] = {}
    for vid in variant_ids:
        result = bootstrap_f1_ci(scored, vid, positive_threshold=threshold, seed=run_id)
        if result is not None:
            ci[vid] = result

    analysis: dict[str, Any] = {
        "computed_at": datetime.now(UTC).isoformat(),
        "positive_threshold": threshold,
        "per_item": per_item,
        "failures": failures,
        "confidence_intervals": ci,
    }

    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_runs SET analysis_json = ? WHERE id = ?",
            (json.dumps(analysis), run_id),
        )
        conn.commit()

    _log.info(
        "compute_run_analysis: stored analysis for run %s (%d items, %d failures)",
        run_id,
        len(per_item),
        len(failures),
    )


# ---------------------------------------------------------------------------
# Top-level session orchestrator
# ---------------------------------------------------------------------------


def run_eval_session(
    run_id: int,
    db: Database,
    http_base: str = "http://127.0.0.1:7683",
) -> None:
    """Top-level: calls run_eval_generate() then run_eval_judge() in sequence.

    Handles unhandled exceptions by setting status='failed' with the error message.
    Intended to be called as a FastAPI background task (runs in thread pool).
    """
    from ollama_queue.eval.generate import run_eval_generate
    from ollama_queue.eval.judge import run_eval_judge
    from ollama_queue.eval.promote import check_auto_promote, generate_eval_analysis

    try:
        run_eval_generate(run_id, db, http_base)
        # Check if generate phase failed
        run = get_eval_run(db, run_id)
        if run is None or run.get("status") in ("failed", "cancelled"):
            return
        run_eval_judge(run_id, db, http_base)
        # Generate Ollama analysis after judging (non-blocking for run status —
        # failures here are logged but never change the completed run record)
        run = get_eval_run(db, run_id)
        if run is not None and run.get("status") == "complete":
            compute_run_analysis(run_id, db)
            generate_eval_analysis(db, run_id, http_base)
            check_auto_promote(db, run_id, http_base)
    except Exception as exc:
        _log.exception("run_eval_session run_id=%d unhandled error", run_id)
        try:
            update_eval_run(db, run_id, status="failed", error=str(exc), completed_at=datetime.now(UTC).isoformat())
        except Exception:
            _log.exception("failed to record error for run_id=%d", run_id)
