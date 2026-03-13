"""Eval generation phase: prompt construction and generation orchestrator.

Builds prompts for principle extraction and runs the generation loop
that iterates over (variant, source_item) pairs.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import ollama_queue.eval.engine as _eng
from ollama_queue.eval.judge import _clean_principle

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt construction (migrated from lessons-db/eval.py)
# ---------------------------------------------------------------------------


def build_generation_prompt(
    template: dict,
    source_item: dict,
    cluster_items: list[dict] | None = None,
    diff_cluster_items: list[dict] | None = None,
) -> str:
    """Build the principle-extraction prompt for a given template + source item.

    template: row from eval_prompt_templates (id, label, instruction, format_spec,
              examples, is_chunked, is_contrastive)
    source_item: {id, title, one_liner, description, cluster_id, category}
    cluster_items: sibling items from same cluster (chunked + contrastive)
    diff_cluster_items: items from different clusters (contrastive only)
    """
    is_chunked = bool(template.get("is_chunked"))
    is_contrastive = bool(template.get("is_contrastive"))
    instruction = template.get("instruction") or ""
    examples_raw = template.get("examples")

    title = source_item.get("title") or ""
    one_liner = source_item.get("one_liner") or ""
    description = (source_item.get("description") or "")[:500]

    if is_contrastive and cluster_items and diff_cluster_items:
        return _build_contrastive_prompt(
            instruction,
            source_item,
            cluster_items,
            diff_cluster_items,
        )

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


def _build_contrastive_prompt(
    instruction: str,
    primary: dict,
    same_cluster_items: list[dict],
    diff_cluster_items: list[dict],
) -> str:
    """Contrastive variant: show same-cluster AND diff-cluster items.

    Forces specificity by requiring the principle to be TRUE for same-cluster
    items and FALSE/irrelevant for diff-cluster items.
    """
    same_lines = []
    all_same = [primary, *same_cluster_items]
    for i, item in enumerate(all_same, 1):
        t = item.get("title") or ""
        o = item.get("one_liner") or ""
        same_lines.append(f"  {i}. {t} — {o}")
    same_block = "\n".join(same_lines)

    diff_lines = []
    for i, item in enumerate(diff_cluster_items, 1):
        t = item.get("title") or ""
        o = item.get("one_liner") or ""
        diff_lines.append(f"  {i}. {t} — {o}")
    diff_block = "\n".join(diff_lines)

    return (
        f"{instruction}\n\n"
        "SAME PATTERN (these lessons share the same structural failure):\n"
        f"{same_block}\n\n"
        "DIFFERENT PATTERNS (these are UNRELATED failure types):\n"
        f"{diff_block}\n\n"
        "Extract ONE structural principle that:\n"
        "- Is TRUE for ALL lessons in the SAME PATTERN group\n"
        "- Is FALSE or IRRELEVANT for the DIFFERENT PATTERNS group\n"
        "- Names the structural pattern, not the technology\n\n"
        "The principle must be specific enough to DISTINGUISH this failure type "
        "from the others listed above.\n\n"
        "Causal form: '<pattern> causes <consequence> when <condition>'\n"
        "One sentence, 10-25 words. No technology names."
    )


def _build_self_critique_prompt(
    principle: str,
    diff_cluster_items: list[dict],
) -> str:
    """Build a self-critique prompt that tests if a principle is too general.

    Presents the principle alongside unrelated lessons and asks the model
    to refine it if it would match those lessons too.
    """
    diff_lines = []
    for i, item in enumerate(diff_cluster_items, 1):
        t = item.get("title") or ""
        o = item.get("one_liner") or ""
        diff_lines.append(f"  {i}. {t} — {o}")
    diff_block = "\n".join(diff_lines)

    return (
        f'You previously extracted this principle: "{principle}"\n\n'
        "Here are UNRELATED lessons from different failure categories:\n"
        f"{diff_block}\n\n"
        "Question: Does this principle also apply to ANY of the "
        "unrelated lessons above?\n\n"
        "If YES — the principle is too general. Rewrite it to be more "
        "specific, so it ONLY matches the original failure type and NOT "
        "the unrelated ones.\n"
        "If NO — the principle is specific enough. Return it unchanged.\n\n"
        "Return ONLY the (possibly refined) principle. One sentence. "
        "Causal form: '<pattern> causes <consequence> when <condition>'\n"
        "No explanation."
    )


def _self_critique(
    *,
    principle: str,
    diff_cluster_items: list[dict],
    model: str,
    temperature: float,
    num_ctx: int,
    http_base: str,
    source: str,
    extra_params: dict | None = None,
    system_prompt: str | None = None,
) -> str:
    """Run self-critique pass. Returns refined principle or original."""
    if not diff_cluster_items:
        return principle

    critique_prompt = _build_self_critique_prompt(principle, diff_cluster_items)
    refined, _ = _eng._call_proxy(
        http_base=http_base,
        model=model,
        prompt=critique_prompt,
        temperature=temperature,
        num_ctx=num_ctx,
        timeout=180,
        source=source,
        priority=2,
        extra_params=extra_params,
        system_prompt=system_prompt,
    )

    if refined and len(refined.strip()) > 10:
        return refined.strip()
    return principle


# ---------------------------------------------------------------------------
# Generation orchestrator
# ---------------------------------------------------------------------------


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
    is_contrastive = bool(template.get("is_contrastive"))
    cluster_items: list[dict] = []
    diff_cluster_items: list[dict] = []

    cid = str(source_item.get("cluster_id") or source_item.get("cluster_seed") or "")

    if is_chunked or is_contrastive:
        cluster_items = [it for it in items_by_cluster.get(cid, []) if str(it["id"]) != str(source_item["id"])][:3]

    if is_contrastive:
        all_diff = []
        for other_cid, other_items in sorted(items_by_cluster.items()):
            if other_cid != cid:
                all_diff.extend(other_items[:2])
        diff_cluster_items = all_diff[:4]

    prompt = build_generation_prompt(template, source_item, cluster_items, diff_cluster_items)
    t0 = time.monotonic()
    _extra_params = json.loads(variant.get("params") or "{}")
    _system_prompt = variant.get("system_prompt")
    text, queue_job_id = _eng._call_proxy(
        http_base=http_base,
        model=variant["model"],
        prompt=prompt,
        temperature=variant.get("temperature", 0.6),
        num_ctx=variant.get("num_ctx", 8192),
        timeout=300,
        source=f"eval-run-{run_id}",
        priority=2,
        extra_params=_extra_params or None,
        system_prompt=_system_prompt,
    )
    generation_time_s = round(time.monotonic() - t0, 1)

    # Self-critique pass: refine if principle is too general
    is_multi_stage = bool(template.get("is_multi_stage"))
    if text and is_multi_stage and diff_cluster_items:
        text = _self_critique(
            principle=text,
            diff_cluster_items=diff_cluster_items,
            model=variant["model"],
            temperature=variant.get("temperature", 0.6),
            num_ctx=variant.get("num_ctx", 8192),
            http_base=http_base,
            source=f"eval-run-{run_id}-critique",
            extra_params=_extra_params or None,
            system_prompt=_system_prompt,
        )

    # Clean CoT artifacts before storing
    if text:
        text = _clean_principle(text)

    _eng.insert_eval_result(
        db,
        run_id=run_id,
        variant=variant_id,
        source_item_id=str(source_item["id"]),
        source_item_title=source_item.get("title") or source_item.get("one_liner", ""),
        target_item_id=str(source_item["id"]),
        is_same_cluster=0,
        row_type="generate",
        principle=text,
        generation_time_s=generation_time_s,
        queue_job_id=queue_job_id,
        error=None if text else "generation_failed",
    )
    return text is not None


def run_eval_generate(  # noqa: PLR0911
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
    4. For each (variant, source_item) pair: apply mode-specific pre-job logic ->
       build prompt -> submit to proxy -> store result in eval_results.
    5. Circuit breaker: if failure rate > error_budget and submitted >= 10, abort.
    6. On completion: set status='judging'.
    """
    # Allow callers (and tests) to inject a sleep function; default is time.sleep.
    _sleep_fn = _sleep if _sleep is not None else time.sleep

    run = _eng.get_eval_run(db, run_id)
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
    _run_eb = run.get("error_budget")
    error_budget: float = float(_run_eb) if _run_eb is not None else 0.30
    data_source_token: str = _eng._get_eval_setting(db, "eval.data_source_token", "")

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
    _eng._ensure_seed(db, run_id, run)

    _eng.update_eval_run(db, run_id, status="generating", stage="fetch_items")

    items = _eng._fetch_items(data_source_url, data_source_token)
    if not items:
        _eng.update_eval_run(
            db, run_id, status="failed", error="no items from data source", completed_at=datetime.now(UTC).isoformat()
        )
        return

    items_by_cluster = _eng._build_items_by_cluster(items)

    if not run.get("item_ids"):
        _eng.update_eval_run(
            db,
            run_id,
            item_ids=json.dumps([str(it["id"]) for it in items]),
            item_count=len(items),
        )

    _eng.update_eval_run(db, run_id, stage="generating")
    submitted = 0
    failed = 0

    for variant_id in variant_ids:
        variant = _eng.get_eval_variant(db, variant_id)
        if variant is None:
            _log.warning("variant %s not found — skipping", variant_id)
            continue
        template = _eng.get_eval_template(db, variant["prompt_template_id"])
        if template is None:
            _log.warning("template %s not found for variant %s — skipping", variant["prompt_template_id"], variant_id)
            continue

        for source_item in items:
            # --- Cooperative cancellation: abort if run was externally stopped ---
            # Catches: _recover_orphans on restart, cancel endpoint, or run row deletion.
            _current = _eng.get_eval_run(db, run_id)
            if _current is None or _current.get("status") in ("failed", "cancelled"):
                _log.info(
                    "run_eval_generate: run_id=%d status=%s — aborting generate loop",
                    run_id,
                    _current.get("status") if _current else "deleted",
                )
                return

            # --- Circuit breaker (all modes) ---
            if submitted >= 10 and failed / submitted > error_budget:
                _log.error(
                    "circuit breaker triggered: %d/%d failed (%.0f%% > %.0f%%)",
                    failed,
                    submitted,
                    100 * failed / submitted,
                    100 * error_budget,
                )
                _eng.update_eval_run(
                    db,
                    run_id,
                    status="failed",
                    error=f"circuit_breaker: {failed}/{submitted} failed",
                    completed_at=datetime.now(UTC).isoformat(),
                )
                return

            # --- fill-open-slots: check limits before each job ---
            if run_mode == "fill-open-slots" and _eng._check_fill_open_slots_limit(
                run_id, submitted, max_runs, max_time_s, fill_start_time
            ):
                _eng.update_eval_run(db, run_id, status="judging", stage="judging")
                return

            # --- opportunistic: throttle before each job ---
            if run_mode == "opportunistic" and _eng._should_throttle(db):
                _log.info(
                    "opportunistic run %d: resources high — sleeping %ds before next job",
                    run_id,
                    _eng._OPPORTUNISTIC_THROTTLE_SLEEP_S,
                )
                _sleep_fn(_eng._OPPORTUNISTIC_THROTTLE_SLEEP_S)
                # Re-check after sleep: run may have been cancelled while waiting
                _current = _eng.get_eval_run(db, run_id)
                if _current is None or _current.get("status") in ("failed", "cancelled"):
                    _log.info(
                        "run_eval_generate: run_id=%d cancelled during throttle sleep — aborting",
                        run_id,
                    )
                    return

            # --- Corruption check: delete null-principle rows from interrupted runs ---
            # If a previous run was interrupted mid-stream (daemon restart, OOM, etc.),
            # eval_results may contain rows where principle IS NULL and error IS NULL.
            # insert_eval_result uses INSERT OR IGNORE, so these rows would be silently
            # skipped on the next run, leaving corrupted data in the DB indefinitely.
            with db._lock:
                conn = db._connect()
                existing = conn.execute(
                    "SELECT id FROM eval_results "
                    "WHERE run_id = ? AND variant = ? AND source_item_id = ? "
                    "AND row_type = 'generate' AND principle IS NULL AND error IS NULL",
                    (run_id, variant_id, str(source_item["id"])),
                ).fetchone()
                if existing:
                    _log.warning(
                        "eval run %d: deleting corrupted generation row (null principle, no error) "
                        "for variant=%s item=%s — will regenerate",
                        run_id,
                        variant_id,
                        source_item["id"],
                    )
                    conn.execute("DELETE FROM eval_results WHERE id = ?", (existing["id"],))
                    conn.commit()

            try:
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
            except _eng._ProxyDownError as exc:
                # Proxy is unreachable — service is restarting. Abort cleanly.
                # Do NOT count toward circuit breaker; this is a deployment event, not an Ollama failure.
                _log.warning("run_eval_generate: proxy down — aborting run_id=%d: %s", run_id, exc)
                _eng.update_eval_run(
                    db,
                    run_id,
                    status="failed",
                    error="proxy_unavailable",
                    completed_at=datetime.now(UTC).isoformat(),
                )
                return
            submitted += 1
            if not ok:
                failed += 1

            # Post-HTTP cancellation re-check: the blocking _generate_one() call
            # may have taken minutes. If the run was cancelled during that window,
            # stop immediately instead of processing remaining items.
            _post = _eng.get_eval_run(db, run_id)
            if _post is None or _post.get("status") in ("failed", "cancelled"):
                _log.info(
                    "run_eval_generate: cancelled during HTTP call for run_id=%d",
                    run_id,
                )
                return

    # Guard: if run was cancelled (e.g. during the last throttle sleep), don't overwrite status
    _final = _eng.get_eval_run(db, run_id)
    if _final is not None and _final.get("status") in ("failed", "cancelled"):
        _log.info(
            "run_eval_generate: run_id=%d status=%s at end — skipping judging transition",
            run_id,
            _final.get("status"),
        )
        return
    _eng.update_eval_run(db, run_id, status="judging", stage="judging", runs_completed=submitted)
