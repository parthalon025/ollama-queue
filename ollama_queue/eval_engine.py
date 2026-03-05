"""Eval engine: principle generation, judge scoring, metrics, and reporting.

Migrated from lessons-db/eval.py and adapted to use ollama-queue's DB and proxy.
Prompt construction and scoring logic is preserved from the original.
Orchestration is rewritten for the eval_runs / eval_results DB tables.
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

_RETRYABLE_CODES = {502, 503}
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 2.0


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
) -> int:
    """Insert a new eval_runs row with status='queued' and return the new id.

    Uses db._lock + db._connect() directly — do NOT modify db.py for this helper.
    data_source_url defaults to the 'eval.data_source_url' setting when not provided.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    now = _dt.now(UTC).isoformat()
    resolved_url = data_source_url or db.get_setting("eval.data_source_url") or "http://127.0.0.1:7685"
    # variants field (legacy TEXT NOT NULL) stores the single variant_id for backward compat
    with db._lock:
        conn = db._connect()
        cur = conn.execute(
            """INSERT INTO eval_runs
               (variant_id, variants, run_mode, label, cluster_id, scheduled_by,
                data_source_url, data_source_token, seed, item_ids, status,
                per_cluster, created_at, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 4, ?, ?)""",
            (
                variant_id,
                variant_id,  # legacy variants field
                run_mode,
                label,
                cluster_id,
                scheduled_by,
                resolved_url,
                data_source_token,
                seed,
                item_ids,
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
# Prompt construction (migrated from lessons-db/eval.py)
# ---------------------------------------------------------------------------


def build_generation_prompt(
    template: dict,
    source_item: dict,
    cluster_items: list[dict] | None = None,
) -> str:
    """Build the principle-extraction prompt for a given template + source item.

    template: row from eval_prompt_templates (id, label, instruction, format_spec,
              examples, is_chunked)
    source_item: {id, title, one_liner, description, cluster_id, category}
    cluster_items: sibling items from same cluster — only used when template.is_chunked=1
    """
    is_chunked = bool(template.get("is_chunked"))
    instruction = template.get("instruction") or ""
    examples_raw = template.get("examples")

    title = source_item.get("title") or ""
    one_liner = source_item.get("one_liner") or ""
    description = (source_item.get("description") or "")[:500]

    if is_chunked and cluster_items:
        return _build_chunked_prompt(instruction, source_item, cluster_items)

    # Detect fewshot by looking for examples JSON field
    if examples_raw:
        return _build_fewshot_prompt(instruction, title, one_liner, description, examples_raw)

    return _build_zero_shot_prompt(instruction, title, one_liner, description)


_FALLBACK_EXAMPLES = (
    "Examples of good principles:\n"
    "- 'Resources acquired in callbacks must be released in a symmetric teardown path.'\n"
    "- 'When two representations of the same data exist, one must be designated authoritative.'\n"
    "- 'Silent fallbacks that return default values mask upstream failures indefinitely.'\n"
    "- 'Integration boundaries require end-to-end value tracing, not per-layer unit tests.'\n\n"
)


def _parse_examples_block(examples_raw: str) -> str:
    """Parse examples JSON into a formatted block string. Returns empty string on failure."""
    try:
        examples = json.loads(examples_raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not examples:
        return ""
    ex_lines = ["Examples of good principles:"]
    for ex in examples[:4]:
        out = ex.get("output") or ex.get("principle") or "" if isinstance(ex, dict) else str(ex)
        if out:
            ex_lines.append(f"- '{out}'")
    return "\n".join(ex_lines) + "\n\n"


def _build_lesson_context(title: str, one_liner: str, description: str) -> str:
    """Build the 'Title / One-liner / Description' context block."""
    parts = []
    if title:
        parts.append(f"Title: {title}")
    if one_liner:
        parts.append(f"One-liner: {one_liner}")
    if description:
        parts.append(f"Description: {description}")
    return "\n".join(parts)


def _build_fewshot_prompt(
    instruction: str,
    title: str,
    one_liner: str,
    description: str,
    examples_raw: str | None,
) -> str:
    """Fewshot variant: show examples before extracting."""
    lesson_context = _build_lesson_context(title, one_liner, description)
    example_block = _parse_examples_block(examples_raw) if examples_raw else ""
    if not example_block:
        example_block = _FALLBACK_EXAMPLES

    return (
        f"{instruction}\n\n"
        "A GOOD principle:\n"
        "- Names the structural pattern, not the technology\n"
        "- Is falsifiable — someone could violate it\n"
        "- Applies to at least 3 different domains\n"
        "- Is one sentence, 10-25 words\n\n"
        f"{example_block}"
        f"Lesson:\n{lesson_context}\n\n"
        "Return ONLY the principle statement. One sentence. No quotes, no explanation."
    )


def _build_zero_shot_prompt(
    instruction: str,
    title: str,
    one_liner: str,
    description: str,
) -> str:
    """Zero-shot causal variant."""
    lesson_context = _build_lesson_context(title, one_liner, description)

    return (
        f"{instruction}\n\n"
        "Format: '<pattern> causes <consequence> when <condition>'\n\n"
        "Requirements:\n"
        "- One sentence, 10-25 words\n"
        "- No technology names, no fixes, no tool references\n"
        "- Name the structural pattern, not the specific bug\n\n"
        f"Lesson:\n{lesson_context}\n\n"
        "Return ONLY the causal principle. No quotes, no explanation."
    )


def _build_chunked_prompt(
    instruction: str,
    primary: dict,
    siblings: list[dict],
) -> str:
    """Chunked variant: show multiple sibling items from same cluster."""
    lines = []
    all_items = [primary, *siblings]
    for i, item in enumerate(all_items, 1):
        t = item.get("title") or ""
        o = item.get("one_liner") or ""
        lines.append(f"{i}. Title: {t}\n   One-liner: {o}")
    item_block = "\n".join(lines)

    return (
        f"{instruction}\n\n"
        "These lessons all share the same structural failure pattern "
        "across different technologies:\n\n"
        f"{item_block}\n\n"
        "What is the ONE structural principle that explains ALL of these?\n\n"
        "Causal form: '<pattern> causes <consequence> when <condition>'\n"
        "One sentence, 10-25 words. No technology names."
    )


# ---------------------------------------------------------------------------
# Judge prompt construction
# ---------------------------------------------------------------------------


def build_judge_prompt(principle: str, target_item: dict, is_same_cluster: bool) -> str:
    """Build rubric-based scoring prompt for a (principle, target_item) pair.

    Asks the judge to score on transfer, precision, actionability (1-5 each)
    and return a JSON object with those scores plus optional reasoning.
    is_same_cluster is included in context so callers can verify understanding,
    but is NOT passed to the judge (would bias the scoring).
    """
    title = target_item.get("title") or ""
    one_liner = target_item.get("one_liner") or ""
    description = (target_item.get("description") or "")[:300]

    return (
        "You are evaluating whether a structural principle helps recognize "
        "a pattern in a target lesson.\n\n"
        f'PRINCIPLE: "{principle}"\n\n'
        "TARGET LESSON:\n"
        f"Title: {title}\n"
        f"One-liner: {one_liner}\n"
        f"Description: {description}\n\n"
        "Score this (principle, target) pair on three criteria, each 1-5:\n\n"
        "1. **Transfer Recognition** — does the principle help identify the "
        "structural pattern in the target?\n"
        "   1=No connection  3=Vague connection  5=Clear structural match\n\n"
        "2. **Precision** — would this principle false-positive on unrelated lessons?\n"
        "   1=Would match anything  3=Somewhat specific  5=Only matches structurally similar\n\n"
        "3. **Actionability** — could an LLM use this principle to prevent this bug class?\n"
        "   1=Too abstract to act on  3=Useful with context  5=Immediately actionable\n\n"
        'Return ONLY a JSON object: {"transfer": N, "precision": N, "actionability": N, "reasoning": "one sentence"}\n'
        "No other text."
    )


# ---------------------------------------------------------------------------
# Judge response parsing
# ---------------------------------------------------------------------------


def parse_judge_response(raw: str) -> dict:
    """Extract JSON scores from judge response.

    Strips <think>...</think> blocks first, storing stripped content as
    judge_reasoning. Returns dict with transfer, precision, actionability (ints),
    reasoning (str), and judge_reasoning (str).

    On parse failure: returns defaults (1,1,1) with error='parse_failed'.
    """
    # Capture and strip think blocks
    think_match = re.search(r"<think>(.*?)</think>", raw, flags=re.DOTALL)
    judge_reasoning = think_match.group(1).strip() if think_match else ""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Find the outermost JSON object: from the first '{' to the last '}'.
    # Using rfind('}') handles reasoning strings that contain '}' characters
    # (e.g. "reasoning": "violates {pattern}") which would truncate a [^}]+ regex.
    _start = cleaned.find("{")
    _end = cleaned.rfind("}")
    _json_text = cleaned[_start : _end + 1] if _start >= 0 and _end > _start else None
    if not _json_text:
        return {
            "transfer": 1,
            "precision": 1,
            "actionability": 1,
            "reasoning": "",
            "judge_reasoning": raw,
            "error": "parse_failed",
        }

    try:
        data = json.loads(_json_text)
    except json.JSONDecodeError:
        return {
            "transfer": 1,
            "precision": 1,
            "actionability": 1,
            "reasoning": "",
            "judge_reasoning": raw,
            "error": "parse_failed",
        }

    required = {"transfer", "precision", "actionability"}
    if not required.issubset(data.keys()):
        return {
            "transfer": 1,
            "precision": 1,
            "actionability": 1,
            "reasoning": "",
            "judge_reasoning": raw,
            "error": "parse_failed",
        }

    def _clamp(v: Any) -> int:
        try:
            return max(1, min(5, int(v)))
        except (TypeError, ValueError):
            return 1

    return {
        "transfer": _clamp(data["transfer"]),
        "precision": _clamp(data["precision"]),
        "actionability": _clamp(data["actionability"]),
        "reasoning": str(data.get("reasoning") or ""),
        "judge_reasoning": judge_reasoning,
    }


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


def compute_metrics(results: list[dict]) -> dict[str, dict[str, float]]:
    """Compute per-variant F1, recall, precision, actionability from scored results.

    results: list of dicts with keys:
        variant, is_same_cluster,
        effective_score_transfer, effective_score_precision, effective_score_action

    effective_score = COALESCE(override_score, score) — caller pre-computes this.

    Returns: {variant_id: {f1, recall, precision, actionability, sample_count}}

    F1 definition (spec):
      recall    = avg transfer score on same_cluster pairs / 5.0
      precision = 1 - avg transfer score on diff_cluster pairs / 5.0
      f1        = 2 * recall * precision / (recall + precision)
    """
    by_variant: dict[str, list[dict]] = {}
    for r in results:
        by_variant.setdefault(r["variant"], []).append(r)

    metrics: dict[str, dict[str, float]] = {}
    for variant, pairs in by_variant.items():
        same = [p for p in pairs if p["is_same_cluster"]]
        diff = [p for p in pairs if not p["is_same_cluster"]]

        recall = sum(p["effective_score_transfer"] for p in same) / (len(same) * 5.0) if same else 0.0
        precision = 1.0 - sum(p["effective_score_transfer"] for p in diff) / (len(diff) * 5.0) if diff else 0.0

        f1 = 2.0 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0.0

        all_act = [p["effective_score_action"] for p in pairs]
        actionability = sum(all_act) / len(all_act) if all_act else 0.0

        metrics[variant] = {
            "f1": round(f1, 4),
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "actionability": round(actionability, 4),
            "sample_count": len(pairs),
        }

    return metrics


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_report(run_id: int, metrics: dict[str, dict[str, float]], db: Database) -> str:
    """Generate a markdown report summarizing the eval run.

    Shows per-variant F1, recall, precision, actionability in a table.
    Shows winner (highest F1). Returns markdown string (caller stores to DB).
    """
    lines: list[str] = []
    lines.append(f"# Transfer-Test Evaluation Report — Run #{run_id}\n")
    lines.append(f"Generated: {datetime.now(UTC).isoformat()}\n")

    if not metrics:
        lines.append("_No scored pairs — metrics unavailable._\n")
        return "\n".join(lines) + "\n"

    # Summary table
    lines.append("## Summary\n")
    lines.append(
        "| Variant | Quality (F1) | Catches Right (Recall)"
        " | Avoids False (Precision) | Useful (Actionability) | Samples |"
    )
    lines.append(
        "|---------|-------------|------------------------|--------------------------|------------------------|---------|"
    )
    for vid in sorted(metrics.keys()):
        m = metrics[vid]
        lines.append(
            f"| {vid} "
            f"| {m['f1']:.2f} "
            f"| {m['recall']:.2f} "
            f"| {m['precision']:.2f} "
            f"| {m['actionability']:.2f} "
            f"| {m['sample_count']} |"
        )

    # Winner
    lines.append("\n## Winner\n")
    winner = max(metrics.keys(), key=lambda v: metrics[v]["f1"])
    wm = metrics[winner]
    lines.append(
        f"**Variant {winner}** — Quality: {wm['f1']:.2f} "
        f"(Catches right: {wm['recall']:.2f}, Avoids false: {wm['precision']:.2f}, "
        f"Useful: {wm['actionability']:.2f})"
    )

    # Variant config details from DB
    variant_row = get_eval_variant(db, winner)
    if variant_row:
        template_row = get_eval_template(db, variant_row.get("prompt_template_id", ""))
        lines.append(f"\nModel: `{variant_row.get('model', 'N/A')}`")
        if template_row:
            lines.append(f"Template: `{template_row.get('label', 'N/A')}`")
        lines.append(
            f"Settings: temperature={variant_row.get('temperature', 'N/A')}, "
            f"num_ctx={variant_row.get('num_ctx', 'N/A')}"
        )

    return "\n".join(lines) + "\n"


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
        except Exception as exc:
            _log.warning("proxy error: %s", exc)
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
# Generation orchestrator
# ---------------------------------------------------------------------------


def _build_items_by_cluster(items: list[dict]) -> dict[str, list[dict]]:
    """Group items by cluster_id (or cluster_seed for compatibility)."""
    mapping: dict[str, list[dict]] = {}
    for item in items:
        cid = str(item.get("cluster_id") or item.get("cluster_seed") or "")
        mapping.setdefault(cid, []).append(item)
    return mapping


def _generate_one(
    *,
    db: Database,
    run_id: int,
    variant_id: str,
    variant: dict,
    template: dict,
    source_item: dict,
    items_by_cluster: dict[str, list[dict]],
    http_base: str,
) -> bool:
    """Generate a principle for one (variant, source_item) pair. Returns True on success."""
    is_chunked = bool(template.get("is_chunked"))
    cluster_items: list[dict] = []
    if is_chunked:
        cid = str(source_item.get("cluster_id") or source_item.get("cluster_seed") or "")
        cluster_items = [it for it in items_by_cluster.get(cid, []) if str(it["id"]) != str(source_item["id"])][:3]

    prompt = build_generation_prompt(template, source_item, cluster_items)
    t0 = time.monotonic()
    text, queue_job_id = _call_proxy(
        http_base=http_base,
        model=variant["model"],
        prompt=prompt,
        temperature=variant.get("temperature", 0.6),
        num_ctx=variant.get("num_ctx", 8192),
        timeout=300,
        source=f"eval-run-{run_id}",
        priority=2,
    )
    generation_time_s = round(time.monotonic() - t0, 1)
    insert_eval_result(
        db,
        run_id=run_id,
        variant=variant_id,
        source_item_id=str(source_item["id"]),
        target_item_id=str(source_item["id"]),
        is_same_cluster=0,
        row_type="generation",  # distinguishes from judge rows in queries
        principle=text,
        generation_time_s=generation_time_s,
        queue_job_id=queue_job_id,
        error=None if text else "generation_failed",
    )
    return text is not None


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


def run_eval_generate(
    run_id: int,
    db: Database,
    http_base: str = "http://127.0.0.1:7683",
    _sleep: Any = None,
) -> None:
    """Main generation loop. Supports batch, opportunistic, fill-open-slots, and scheduled modes.

    Scheduling modes (read from run["run_mode"], default "batch"):

    - batch: Submit all jobs immediately; circuit-break on error_budget.
    - opportunistic: Submit one job at a time; between jobs, poll system health and
      sleep 30s if any resource (ram_pct, vram_pct, cpu_pct) exceeds 80%.
    - fill-open-slots: Like batch but with a concurrency cap. Reads max_runs (count
      cap) and max_time_s (wall-clock cap) from the run record. Stops at whichever
      limit is reached first when both are set.
    - scheduled: Behaviorally identical to batch; logs trigger time.

    1. Fetch run config from DB.
    2. Fetch variant configs and templates.
    3. Fetch items from data source.
    4. For each (variant, source_item) pair: apply mode-specific pre-job logic →
       build prompt → submit to proxy → store result in eval_results.
    5. Circuit breaker: if failure rate > error_budget and submitted >= 10, abort.
    6. On completion: set status='judging'.
    """
    # Allow callers (and tests) to inject a sleep function; default is time.sleep.
    _sleep_fn = _sleep if _sleep is not None else time.sleep

    run = get_eval_run(db, run_id)
    if run is None:
        _log.error("run_eval_generate: run_id=%d not found", run_id)
        return

    data_source_url = run["data_source_url"]
    _raw_variants = run["variants"]
    try:
        variant_ids: list[str] = json.loads(_raw_variants)
        if not isinstance(variant_ids, list):
            variant_ids = [str(variant_ids)]
    except (json.JSONDecodeError, TypeError):
        # variants stored as a plain string (single variant ID) rather than JSON array
        variant_ids = [str(_raw_variants)]
    error_budget: float = run.get("error_budget") or 0.30
    data_source_token: str = _get_eval_setting(db, "eval.data_source_token", "")

    # --- Mode dispatch setup ---
    run_mode: str = run.get("run_mode") or "batch"
    _log.info("eval run %d starting in %s mode", run_id, run_mode)

    if run_mode == "scheduled":
        _log.info("scheduled run %d triggered at %s", run_id, datetime.now(UTC).isoformat())

    # fill-open-slots limits — read from run record
    max_runs: int | None = run.get("max_runs")
    max_time_s: int | None = run.get("max_time_s")
    fill_start_time: float = time.monotonic()

    # Ensure a seed exists before generation starts — required for reproducibility.
    # Delegated to _ensure_seed to keep run_eval_generate's cyclomatic complexity in check.
    _ensure_seed(db, run_id, run)

    update_eval_run(db, run_id, status="generating", stage="fetch_items")

    items = _fetch_items(data_source_url, data_source_token)
    if not items:
        update_eval_run(db, run_id, status="failed", error="no items from data source")
        return

    items_by_cluster = _build_items_by_cluster(items)

    if not run.get("item_ids"):
        update_eval_run(
            db,
            run_id,
            item_ids=json.dumps([str(it["id"]) for it in items]),
            item_count=len(items),
        )

    update_eval_run(db, run_id, stage="generating")
    submitted = 0
    failed = 0

    for variant_id in variant_ids:
        variant = get_eval_variant(db, variant_id)
        if variant is None:
            _log.warning("variant %s not found — skipping", variant_id)
            continue
        template = get_eval_template(db, variant["prompt_template_id"])
        if template is None:
            _log.warning("template %s not found for variant %s — skipping", variant["prompt_template_id"], variant_id)
            continue

        for source_item in items:
            # --- Circuit breaker (all modes) ---
            if submitted >= 10 and failed / submitted > error_budget:
                _log.error(
                    "circuit breaker triggered: %d/%d failed (%.0f%% > %.0f%%)",
                    failed,
                    submitted,
                    100 * failed / submitted,
                    100 * error_budget,
                )
                update_eval_run(db, run_id, status="failed", error=f"circuit_breaker: {failed}/{submitted} failed")
                return

            # --- fill-open-slots: check limits before each job ---
            if run_mode == "fill-open-slots" and _check_fill_open_slots_limit(
                run_id, submitted, max_runs, max_time_s, fill_start_time
            ):
                update_eval_run(db, run_id, status="judging", stage="judging")
                return

            # --- opportunistic: throttle before each job ---
            if run_mode == "opportunistic" and _should_throttle(db):
                _log.info(
                    "opportunistic run %d: resources high — sleeping %ds before next job",
                    run_id,
                    _OPPORTUNISTIC_THROTTLE_SLEEP_S,
                )
                _sleep_fn(_OPPORTUNISTIC_THROTTLE_SLEEP_S)

            ok = _generate_one(
                db=db,
                run_id=run_id,
                variant_id=variant_id,
                variant=variant,
                template=template,
                source_item=source_item,
                items_by_cluster=items_by_cluster,
                http_base=http_base,
            )
            submitted += 1
            if not ok:
                failed += 1

    update_eval_run(db, run_id, status="judging", stage="judging")


# ---------------------------------------------------------------------------
# Judge orchestrator helpers
# ---------------------------------------------------------------------------


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


def _judge_one_target(
    *,
    db: Database,
    run_id: int,
    variant: str,
    source_item_id: str,
    principle: str,
    target: dict,
    is_same: bool,
    judge_model: str,
    judge_temperature: float,
    source_tag: str,
    http_base: str,
) -> None:
    """Call judge for one (principle, target) pair and store the result."""
    judge_prompt = build_judge_prompt(principle, target, is_same)
    t0 = time.monotonic()
    raw_response, _ = _call_proxy(
        http_base=http_base,
        model=judge_model,
        prompt=judge_prompt,
        temperature=judge_temperature,
        num_ctx=4096,
        timeout=180,
        source=source_tag,
        priority=2,
    )
    judge_time_s = round(time.monotonic() - t0, 1)
    _judge_fail: dict = {
        "transfer": 1,
        "precision": 1,
        "actionability": 1,
        "reasoning": "",
        "judge_reasoning": "",
        "error": "judge_failed",
    }
    scores = parse_judge_response(raw_response) if raw_response is not None else _judge_fail
    insert_eval_result(
        db,
        run_id=run_id,
        variant=variant,
        source_item_id=source_item_id,
        target_item_id=str(target["id"]),
        is_same_cluster=1 if is_same else 0,
        row_type="judge",
        principle=principle,
        judge_reasoning=scores.get("judge_reasoning"),
        score_transfer=scores["transfer"],
        score_precision=scores["precision"],
        score_action=scores["actionability"],
        generation_time_s=judge_time_s,
        error=scores.get("error"),
    )


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
                          COALESCE(override_score_action, score_action) AS effective_score_action
                   FROM eval_results
                   WHERE run_id = ?
                     AND row_type = 'judge'
                     AND score_transfer IS NOT NULL
                     AND error IS NULL""",
                (run_id,),
            ).fetchall()
        ]


# ---------------------------------------------------------------------------
# Judge orchestrator
# ---------------------------------------------------------------------------


def run_eval_judge(
    run_id: int,
    db: Database,
    http_base: str = "http://127.0.0.1:7683",
) -> None:
    """Score generated principles against transfer targets.

    1. Fetch all eval_results with principle IS NOT NULL for this run.
    2. For each result, select same-cluster + diff-cluster targets from fetched items.
    3. Use run.seed for deterministic target selection.
    4. Submit judge calls to proxy, parse scores, store in eval_results.
    5. Compute metrics, store report_md and winner_variant.
    6. Set status='complete'.
    """
    run = get_eval_run(db, run_id)
    if run is None:
        _log.error("run_eval_judge: run_id=%d not found", run_id)
        return

    data_source_url = run["data_source_url"]
    seed: int | None = run.get("seed")
    judge_model: str = run.get("judge_model") or _get_eval_setting(db, "eval.judge_model", "deepseek-r1:8b")
    judge_temperature = float(_get_eval_setting(db, "eval.judge_temperature", 0.1))
    data_source_token: str = _get_eval_setting(db, "eval.data_source_token", "")
    same_cluster_targets: int = int(_get_eval_setting(db, "eval.same_cluster_targets", 2))
    diff_cluster_targets: int = int(_get_eval_setting(db, "eval.diff_cluster_targets", 2))

    update_eval_run(db, run_id, stage="fetch_targets")

    items = _fetch_items(data_source_url, data_source_token)
    if not items:
        update_eval_run(db, run_id, status="failed", error="no items for judging")
        return

    item_by_id: dict[str, dict] = {str(it["id"]): it for it in items}
    items_by_cluster = _build_items_by_cluster(items)

    with db._lock:
        conn = db._connect()
        gen_results = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM eval_results WHERE run_id = ? AND principle IS NOT NULL AND error IS NULL",
                (run_id,),
            ).fetchall()
        ]

    rng = random.Random(seed)  # noqa: S311 — not crypto, deterministic eval selection
    source_tag = f"eval-run-{run_id}-judge"
    update_eval_run(db, run_id, stage="judging")

    # Collect all (source_item_id, target_item_id) pairs so they can be persisted
    # for exact replay via the repeat endpoint.
    judge_pairs: list[list[str]] = []

    for gen_result in gen_results:
        source_item_id = str(gen_result["source_item_id"])
        principle = gen_result["principle"]
        source_item = item_by_id.get(source_item_id)
        if source_item is None:
            _log.warning("source item %s not found in fetched items", source_item_id)
            continue

        source_cid = str(source_item.get("cluster_id") or source_item.get("cluster_seed") or "")
        same_targets, diff_targets = _select_judge_targets(
            source_item_id=source_item_id,
            source_cid=source_cid,
            items=items,
            items_by_cluster=items_by_cluster,
            rng=rng,
            same_count=same_cluster_targets,
            diff_count=diff_cluster_targets,
        )

        for is_same, target_list in [(True, same_targets), (False, diff_targets)]:
            for target in target_list:
                judge_pairs.append([source_item_id, str(target["id"])])
                _judge_one_target(
                    db=db,
                    run_id=run_id,
                    variant=gen_result["variant"],
                    source_item_id=source_item_id,
                    principle=principle,
                    target=target,
                    is_same=is_same,
                    judge_model=judge_model,
                    judge_temperature=judge_temperature,
                    source_tag=source_tag,
                    http_base=http_base,
                )

    # Persist exact (source_item_id, target_item_id) pairs for reproducibility.
    # This overwrites the coarse source-only item_ids stored during generation.
    if judge_pairs:
        update_eval_run(db, run_id, item_ids=json.dumps(judge_pairs))
        _log.info("run_eval_judge: persisted %d judge pairs for run_id=%d", len(judge_pairs), run_id)

    scored_rows = _fetch_scored_rows(db, run_id)
    metrics = compute_metrics(scored_rows)
    winner = max(metrics.keys(), key=lambda v: metrics[v]["f1"]) if metrics else None
    report_md = render_report(run_id, metrics, db)

    # Persist full metrics snapshot and completion timestamp for trend analysis
    # and the repeat endpoint to verify reproducibility data is present.
    update_eval_run(
        db,
        run_id,
        status="complete",
        stage=None,
        metrics=json.dumps(metrics),
        winner_variant=winner,
        report_md=report_md,
        completed_at=datetime.now(UTC).isoformat(),
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
    try:
        run_eval_generate(run_id, db, http_base)
        # Check if generate phase failed
        run = get_eval_run(db, run_id)
        if run is None or run.get("status") == "failed":
            return
        run_eval_judge(run_id, db, http_base)
    except Exception as exc:
        _log.exception("run_eval_session run_id=%d unhandled error", run_id)
        try:
            update_eval_run(db, run_id, status="failed", error=str(exc))
        except Exception:
            _log.exception("failed to record error for run_id=%d", run_id)
