"""Eval judge phase: judge prompts, response parsing, signal extraction, and judge orchestrator.

Scores generated principles against transfer targets using rubric, binary,
tournament, or Bayesian modes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import ollama_queue.eval.engine as _eng

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Principle cleaning (used by both generate and judge phases)
# ---------------------------------------------------------------------------


def _clean_principle(text: str) -> str:
    """Strip Chain-of-Thought artifacts from a generated principle.

    deepseek-r1 often includes reasoning traces, lesson-by-lesson analysis,
    and "This principle applies because..." explanations.  The judge should
    score the principle statement alone, not the surrounding rationale.
    """
    if not text:
        return text

    text = text.strip()

    # 1. If text starts with CoT preamble, try to find actual principle below
    cot_start = re.match(
        r"^(okay|let me|let's|the lessons|here's|i'll|to analyze|looking at)",
        text,
        re.IGNORECASE,
    )
    if cot_start:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        for para in paragraphs[1:]:
            if para.startswith("*") or para.startswith("-"):
                continue
            if len(para) > 20:
                text = para
                break

    # 2. Extract text after "**Principle:**" or "The principle is:" markers
    marker = re.search(
        r"(?:\*\*Principle:\*\*|The principle is:)\s*(.+?)(?:\n\n|$)",
        text,
        re.DOTALL,
    )
    if marker:
        text = marker.group(1).strip()

    # 3. Take only the first paragraph (strip trailing explanations)
    if "\n\n" in text:
        text = text.split("\n\n")[0].strip()

    # 4. Strip markdown bold markers
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)

    # 5. Strip trailing parenthetical explanations like "*(This principle...)"
    text = re.sub(r"\s*\*?\(This principle\b.*", "", text, flags=re.DOTALL)

    return text.strip()


# ---------------------------------------------------------------------------
# Judge prompt construction
# ---------------------------------------------------------------------------


def build_judge_prompt(principle: str, target_item: dict, is_same_cluster: bool) -> str:
    """Build rubric-based scoring prompt with calibration anchors.

    Cleans CoT artifacts from the principle before embedding in the prompt.
    Includes concrete scored examples so the judge's internal scale is
    anchored, reducing score inflation on cross-cluster pairs.
    is_same_cluster is available for caller verification but is NOT
    passed to the judge (would bias the scoring).
    """
    principle = _clean_principle(principle)
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
        "Score this (principle, target) pair on three criteria, each 1-5.\n\n"
        "## Scoring Guide with Examples\n\n"
        "**Transfer Recognition** — does the principle structurally match the target?\n"
        "  1 = No structural connection. E.g. principle about resource cleanup → target about naming conventions → 1\n"
        "  3 = Vague thematic overlap but different mechanism. "
        "E.g. error handling principle → logging gaps target → 3\n"
        "  5 = Same structural pattern, different technology. "
        "E.g. resource cleanup principle → unclosed DB connections → 5\n\n"
        "**Precision** — would this principle false-positive on unrelated lessons?\n"
        "  1 = So general it matches everything (e.g. 'always test your code')\n"
        "  3 = Matches a broad category but not everything\n"
        "  5 = Only matches lessons with the same specific structural failure\n\n"
        "**Actionability** — could an LLM use this to prevent this class of bug?\n"
        "  1 = Too abstract to act on (e.g. 'be careful with state')\n"
        "  3 = Useful with additional context\n"
        "  5 = Specific enough to implement a check or review step\n\n"
        "IMPORTANT: Be skeptical. Most principles do NOT transfer to unrelated lessons. "
        "Default to low transfer scores unless there is a clear structural match.\n\n"
        'Return ONLY a JSON object: {"transfer": N, "precision": N, "actionability": N, "reasoning": "one sentence"}\n'
        "No other text."
    )


# ---------------------------------------------------------------------------
# Analysis prompt
# ---------------------------------------------------------------------------


def build_analysis_prompt(
    run_id: int,
    variants: list[str],
    item_count: int,
    judge_model: str,
    metrics: dict[str, dict[str, float]],
    winner: str | None,
    top_pairs: list[dict],
    bottom_pairs: list[dict],
) -> str:
    """Build the Ollama prompt for post-run analysis.

    Feeds the model: run context, per-variant metrics table, best-performing
    and worst-performing same-cluster pairs. Asks for three plain-text sections:
    SUMMARY / WHY / RECOMMENDATIONS.
    """
    lines: list[str] = []
    lines.append(
        "You are analyzing the results of a prompt evaluation run.\n"
        "The eval tests how well an AI model extracts transferable principles from lessons\n"
        "and applies them to recognize related lessons in the same problem cluster."
    )
    lines.append(f"\nRun #{run_id}")
    lines.append(f"Variants tested: {', '.join(variants) if variants else 'none'}")
    lines.append(f"Items evaluated: {item_count}")
    lines.append(f"Scorer model: {judge_model}\n")

    lines.append("## Results")
    lines.append(
        "Recall = how often the principle matched a correct same-cluster target (higher = better).\n"
        "Precision = 1 minus how often the principle matched an incorrect diff-cluster target (higher = better).\n"
        "F1 = harmonic mean of recall + precision.\n"
        "Actionability = mean score of how useful/specific the generated principles were (1-5).\n"
    )
    lines.append("| Config | F1 | Recall | Precision | Actionability |")
    lines.append("|--------|----|--------|-----------|---------------|")
    for vid in sorted(metrics.keys()):
        m = metrics[vid]
        mark = " (winner)" if vid == winner else ""
        lines.append(
            f"| {vid}{mark} | {m['f1']:.2f} | {m['recall']:.2f} | {m['precision']:.2f} | {m['actionability']:.2f}/5 |"
        )
    lines.append("")

    if top_pairs:
        lines.append("## Best-performing examples (same-cluster pairs, highest transfer scores)")
        for p in top_pairs:
            principle_snippet = str(p.get("principle") or "").replace("\n", " ")[:180]
            lines.append(f"- Config {p['variant']}, score {p.get('score_transfer', '?')}/5: {principle_snippet}")
        lines.append("")

    if bottom_pairs:
        lines.append("## Worst-performing examples (same-cluster pairs, lowest transfer scores)")
        for p in bottom_pairs:
            principle_snippet = str(p.get("principle") or "").replace("\n", " ")[:180]
            lines.append(f"- Config {p['variant']}, score {p.get('score_transfer', '?')}/5: {principle_snippet}")
        lines.append("")

    lines.append(
        "## Task\n"
        "Analyze this eval run. Respond with exactly three plain-text sections (no markdown).\n\n"
        "SUMMARY: One sentence — did this run succeed? What was the best config?\n\n"
        "WHY: 2-3 sentences on what the metrics reveal. What caused high/low recall or precision?"
        " What do the example principles suggest about model behavior?\n\n"
        "RECOMMENDATIONS: Three numbered, concrete next steps. Reference specific config IDs,"
        " metric patterns, or templates.\n\n"
        "Keep your response under 250 words. Do not define recall or precision in general —"
        " focus on what these specific results reveal."
    )
    return "\n".join(lines)


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
# Paired tournament prompt + parser (ported from lessons-db/eval.py)
# ---------------------------------------------------------------------------


def build_paired_judge_prompt(
    principle: str,
    same_target: dict[str, Any],
    diff_target: dict[str, Any],
    position_seed: int | None = None,
) -> tuple[str, bool]:
    """Paired comparison prompt -- which target does the principle apply to more?

    Randomizes A/B position to eliminate position bias.
    Returns (prompt_text, same_is_a) where same_is_a indicates if the same-group
    target was placed in position A.
    """
    principle = re.sub(r"<think>.*?</think>", "", principle, flags=re.DOTALL | re.IGNORECASE).strip()
    principle = _clean_principle(principle)

    if position_seed is None:
        position_seed = int(hashlib.md5(principle.encode(), usedforsecurity=False).hexdigest()[:8], 16)
    swap = position_seed % 2 == 0

    target_a = diff_target if swap else same_target
    target_b = same_target if swap else diff_target

    def _fmt(t: dict[str, Any]) -> str:
        title = t.get("title") or ""
        one_liner = t.get("one_liner") or ""
        desc = (t.get("description") or "")[:200]
        return f"Title: {title}\nOne-liner: {one_liner}\nDescription: {desc}"

    prompt = (
        f'PRINCIPLE: "{principle}"\n\n'
        f"TARGET A:\n{_fmt(target_a)}\n\n"
        f"TARGET B:\n{_fmt(target_b)}\n\n"
        "Which target does this principle apply to MORE specifically?\n"
        "Consider the STRUCTURAL failure mechanism, not surface-level topic similarity.\n\n"
        "Rules:\n"
        "- Pick the target where the principle identifies the EXACT same bug class.\n"
        "- If neither applies well, answer NEITHER.\n\n"
        "Answer ONLY: A, B, or NEITHER"
    )
    same_is_a = not swap
    return prompt, same_is_a


def parse_paired_judge(response: str) -> str | None:
    """Parse A/B/NEITHER from paired comparison response."""
    if not response:
        return None
    text = response.strip().upper()
    text = re.sub(r"<THINK>.*?</THINK>", "", text, flags=re.DOTALL).strip()
    if text.startswith("A"):
        return "A"
    if text.startswith("B"):
        return "B"
    if "NEITHER" in text:
        return "NEITHER"
    for ch in ["A", "B"]:
        if ch in text and len(text) < 30:
            return ch
    return None


# ---------------------------------------------------------------------------
# Mechanism extraction prompt + parser (ported from lessons-db/eval.py)
# ---------------------------------------------------------------------------


def build_mechanism_extraction_prompt(lesson_a: dict, lesson_b: dict) -> str:
    """Extract shared failure mechanism as a triplet from two lessons."""

    def _fmt(lesson: dict) -> str:
        return (
            f"Title: {lesson.get('title', '')}\n"
            f"One-liner: {lesson.get('one_liner', '')}\n"
            f"Description: {(lesson.get('description', '') or '')[:300]}"
        )

    return (
        "You are analyzing two software engineering lessons that share a failure pattern.\n\n"
        f"LESSON A:\n{_fmt(lesson_a)}\n\n"
        f"LESSON B:\n{_fmt(lesson_b)}\n\n"
        "Extract the SPECIFIC structural mechanism these two lessons share.\n\n"
        "Format your answer as exactly three lines:\n"
        "TRIGGER: [what condition causes the bug, 3-10 words]\n"
        "TARGET: [what component/resource breaks, 3-10 words]\n"
        "FIX: [what structural change prevents it, 3-10 words]\n\n"
        "Rules:\n"
        "- Be SPECIFIC — 'error handling' is too vague. "
        "'Uncaught exception in cleanup path' is specific.\n"
        "- Name the MECHANISM, not the topic. Two lessons about 'testing' may have "
        "completely different mechanisms.\n"
        "- If these lessons do NOT share a specific mechanism, answer: NONE"
    )


def parse_mechanism_triplet(response: str) -> dict[str, str] | None:
    """Parse TRIGGER/TARGET/FIX triplet from mechanism extraction response."""
    if not response:
        return None
    text = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL | re.IGNORECASE).strip()
    if "NONE" in text.upper() and len(text) < 50:
        return None
    trigger = re.search(r"TRIGGER:\s*(.+)", text, re.IGNORECASE)
    target = re.search(r"TARGET:\s*(.+)", text, re.IGNORECASE)
    fix = re.search(r"FIX:\s*(.+)", text, re.IGNORECASE)
    if not trigger or not target or not fix:
        return None
    return {
        "trigger": trigger.group(1).strip()[:100],
        "target": target.group(1).strip()[:100],
        "fix": fix.group(1).strip()[:100],
    }


# ---------------------------------------------------------------------------
# Signal extractors — log-likelihood ratios for Bayesian fusion
# (ported from lessons-db/eval.py)
# ---------------------------------------------------------------------------


def compute_paired_signal(winner: str) -> float:
    """Convert paired comparison outcome to log-likelihood ratio.

    - "same": judge picked same-group target -> strong positive evidence
    - "diff": judge picked diff-group target -> strong negative evidence
    - "neither": judge couldn't decide -> uninformative
    """
    return {"same": 2.5, "diff": -2.5, "neither": 0.0}.get(winner, 0.0)


def compute_embedding_signal(cosine_sim: float) -> float:
    """Convert cosine similarity to log-likelihood ratio.

    Thresholds calibrated from embedding AUC=0.707 baseline.
    """
    if cosine_sim >= 0.7:
        return 1.5
    elif cosine_sim >= 0.5:
        return 0.5
    elif cosine_sim >= 0.3:
        return -0.5
    else:
        return -1.5


def compute_scope_signal(principle_scopes: set, target_scopes: set) -> float:
    """Convert scope tag overlap (Jaccard) to log-likelihood ratio.

    Empty scope on either side -> uninformative (0.0).
    """
    if not principle_scopes or not target_scopes:
        return 0.0
    overlap = len(principle_scopes & target_scopes) / len(principle_scopes | target_scopes)
    if overlap >= 0.5:
        return 1.0
    elif overlap > 0:
        return 0.3
    else:
        return -0.5


def compute_mechanism_signal(mechanism_match: bool | None) -> float:
    """Convert mechanism-naming match to log-likelihood ratio.

    None means mechanism data unavailable -> uninformative.
    """
    if mechanism_match is True:
        return 2.0
    elif mechanism_match is False:
        return -1.5
    else:
        return 0.0


# ---------------------------------------------------------------------------
# Bayesian fusion — compute_transfer_posterior
# (ported from lessons-db/eval.py)
# ---------------------------------------------------------------------------

# Prior: P(transfers) = 0.25 — most principles DON'T transfer to arbitrary targets
_PRIOR_LOG_ODDS = math.log(0.25 / 0.75)  # approx -1.10


def compute_transfer_posterior(
    paired_signal: float,
    embedding_signal: float,
    scope_signal: float,
    mechanism_signal: float,
) -> float:
    """Compute P(transfers | signals) via naive Bayes log-odds fusion.

    Each signal is a log-likelihood ratio from an independent evidence source.
    Combines via addition in log-odds space, then sigmoid to probability.
    """
    log_odds = _PRIOR_LOG_ODDS + paired_signal + embedding_signal + scope_signal + mechanism_signal
    return 1.0 / (1.0 + math.exp(-log_odds))


# ---------------------------------------------------------------------------
# Judge orchestrator helpers
# ---------------------------------------------------------------------------


def _judge_one_target(
    *,
    db: Database,
    run_id: int,
    variant: str,
    source_item_id: str,
    source_item_title: str = "",
    principle: str,
    target: dict,
    is_same: bool,
    judge_model: str,
    judge_temperature: float,
    source_tag: str,
    http_base: str,
    source_cluster_id: str = "",
    judge_mode: str = "rubric",
    diff_target: dict | None = None,
    backend: str | None = None,
) -> bool:
    """Call judge for one (principle, target) pair and store the result.

    judge_mode controls the scoring approach:
    - "rubric": existing 1-5 rubric scoring (default, backward compatible)
    - "binary": YES/NO transfer match
    - "tournament": paired A/B comparison (requires diff_target)
    - "bayesian": paired comparison + signal fusion (requires diff_target)

    Returns True if a parse failure occurred, False otherwise.
    """
    t0 = time.monotonic()
    extra_cols: dict[str, Any] = {}

    if judge_mode in ("tournament", "bayesian") and diff_target is not None:
        # Paired comparison: same_target vs diff_target
        prompt, same_is_a = build_paired_judge_prompt(principle, target, diff_target)
        raw_response, _ = _eng._call_proxy(
            http_base=http_base,
            model=judge_model,
            prompt=prompt,
            temperature=judge_temperature,
            num_ctx=4096,
            timeout=180,
            source=source_tag,
            priority=2,
            backend=backend,
        )
        answer = parse_paired_judge(raw_response) if raw_response else None

        if answer is None:
            paired_winner = "neither"
        elif (answer == "A" and same_is_a) or (answer == "B" and not same_is_a):
            paired_winner = "same"
        elif (answer == "A" and not same_is_a) or (answer == "B" and same_is_a):
            paired_winner = "diff"
        else:
            paired_winner = "neither"

        extra_cols["score_paired_winner"] = paired_winner

        # For bayesian mode: compute posterior from available signals
        if judge_mode == "bayesian":
            p_signal = compute_paired_signal(paired_winner)
            # Embedding and scope signals default to 0 (uninformative) when not available
            e_signal = 0.0
            s_signal = 0.0
            m_signal = 0.0
            posterior = compute_transfer_posterior(p_signal, e_signal, s_signal, m_signal)
            extra_cols["score_posterior"] = round(posterior, 4)

        # Map paired winner to rubric-like transfer score for metrics compatibility
        transfer_score = {"same": 5, "diff": 1, "neither": 3}.get(paired_winner, 1)
        scores = {
            "transfer": transfer_score,
            "precision": 3,
            "actionability": 3,
            "reasoning": f"paired:{paired_winner}",
            "judge_reasoning": raw_response or "",
        }
    else:
        # Standard rubric or binary mode
        judge_prompt = build_judge_prompt(principle, target, is_same)
        raw_response, _ = _eng._call_proxy(
            http_base=http_base,
            model=judge_model,
            prompt=judge_prompt,
            temperature=judge_temperature,
            num_ctx=4096,
            timeout=180,
            source=source_tag,
            priority=2,
            backend=backend,
        )
        _judge_fail: dict = {
            "transfer": 1,
            "precision": 1,
            "actionability": 1,
            "reasoning": "",
            "judge_reasoning": "",
            "error": "judge_failed",
        }
        scores = parse_judge_response(raw_response) if raw_response is not None else _judge_fail

    judge_time_s = round(time.monotonic() - t0, 1)
    _eng.insert_eval_result(
        db,
        run_id=run_id,
        variant=variant,
        source_item_id=source_item_id,
        source_item_title=source_item_title,
        target_item_id=str(target["id"]),
        target_item_title=target.get("title") or target.get("one_liner", ""),
        is_same_cluster=1 if is_same else 0,
        target_cluster_id=str(target.get("cluster_id") or target.get("cluster_seed") or ""),
        source_cluster_id=source_cluster_id,
        row_type="judge",
        principle=principle,
        judge_reasoning=scores.get("judge_reasoning"),
        score_transfer=scores["transfer"],
        score_precision=scores["precision"],
        score_action=scores["actionability"],
        generation_time_s=judge_time_s,
        error=scores.get("error"),
        **extra_cols,
    )
    return scores.get("error") == "parse_failed"


# ---------------------------------------------------------------------------
# Judge orchestrator
# ---------------------------------------------------------------------------


def run_eval_judge(  # noqa: PLR0911
    run_id: int,
    db: Database,
    http_base: str = "http://127.0.0.1:7683",
    backend: str | None = None,
) -> None:
    """Score generated principles against transfer targets.

    1. Fetch all eval_results with principle IS NOT NULL for this run.
    2. For each result, select same-cluster + diff-cluster targets from fetched items.
    3. Use run.seed for deterministic target selection.
    4. Submit judge calls to proxy, parse scores, store in eval_results.
    5. Compute metrics, store report_md and winner_variant.
    6. Set status='complete'.
    """
    run = _eng.get_eval_run(db, run_id)
    if run is None:
        _log.error("run_eval_judge: run_id=%d not found", run_id)
        return

    data_source_url = run["data_source_url"]
    seed: int | None = run.get("seed")
    judge_model: str = run.get("judge_model") or _eng._get_eval_setting(db, "eval.judge_model", "")
    if not judge_model:
        _log.error("No judge model configured for run %d — set eval.judge_model in settings", run_id)
        _eng.update_eval_run(
            db,
            run_id,
            status="failed",
            error="No judge model configured — set eval.judge_model in settings",
            completed_at=datetime.now(UTC).isoformat(),
        )
        return
    judge_temperature = float(_eng._get_eval_setting(db, "eval.judge_temperature", 0.1))
    data_source_token: str = _eng._get_eval_setting(db, "eval.data_source_token", "")
    same_cluster_targets: int = int(_eng._get_eval_setting(db, "eval.same_cluster_targets", 2))
    diff_cluster_targets: int = int(_eng._get_eval_setting(db, "eval.diff_cluster_targets", 2))
    judge_mode: str = run.get("judge_mode") or "rubric"

    _eng.update_eval_run(db, run_id, stage="fetch_targets")

    items = _eng._fetch_items(data_source_url, data_source_token)
    if not items:
        _eng.update_eval_run(
            db, run_id, status="failed", error="no items for judging", completed_at=datetime.now(UTC).isoformat()
        )
        return

    item_by_id: dict[str, dict] = {str(it["id"]): it for it in items}
    items_by_cluster = _eng._build_items_by_cluster(items)

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
    _eng.update_eval_run(db, run_id, stage="judging")

    # Collect all (source_item_id, target_item_id) pairs so they can be persisted
    # for exact replay via the repeat endpoint.
    judge_pairs: list[list[str]] = []
    parse_failures: int = 0

    for gen_result in gen_results:
        # Cooperative cancellation: stop if run was externally cancelled/failed.
        _jcurrent = _eng.get_eval_run(db, run_id)
        if _jcurrent is None or _jcurrent.get("status") in ("failed", "cancelled"):
            _log.info(
                "run_eval_judge: run_id=%d status=%s — aborting judge loop",
                run_id,
                _jcurrent.get("status") if _jcurrent else "deleted",
            )
            return

        source_item_id = str(gen_result["source_item_id"])
        principle = gen_result["principle"]
        source_item = item_by_id.get(source_item_id)
        if source_item is None:
            _log.warning("source item %s not found in fetched items", source_item_id)
            continue

        source_cid = str(source_item.get("cluster_id") or source_item.get("cluster_seed") or "")
        _source_title = source_item.get("title") or source_item.get("one_liner", "")
        same_targets, diff_targets = _eng._select_judge_targets(
            source_item_id=source_item_id,
            source_cid=source_cid,
            items=items,
            items_by_cluster=items_by_cluster,
            rng=rng,
            same_count=same_cluster_targets,
            diff_count=diff_cluster_targets,
        )

        if judge_mode in ("tournament", "bayesian"):
            # Paired modes: zip same + diff targets into pairs
            for i in range(min(len(same_targets), len(diff_targets))):
                same_t = same_targets[i]
                diff_t = diff_targets[i]
                judge_pairs.append([source_item_id, str(same_t["id"])])
                try:
                    _was_parse_fail = _judge_one_target(
                        db=db,
                        run_id=run_id,
                        variant=gen_result["variant"],
                        source_item_id=source_item_id,
                        source_item_title=_source_title,
                        principle=principle,
                        target=same_t,
                        is_same=True,
                        judge_model=judge_model,
                        judge_temperature=judge_temperature,
                        source_tag=source_tag,
                        http_base=http_base,
                        source_cluster_id=source_cid,
                        judge_mode=judge_mode,
                        diff_target=diff_t,
                        backend=backend,
                    )
                    if _was_parse_fail:
                        parse_failures += 1
                except _eng._ProxyDownError as exc:
                    _log.warning("run_eval_judge: proxy down — aborting run_id=%d: %s", run_id, exc)
                    _eng.update_eval_run(
                        db,
                        run_id,
                        status="failed",
                        error="proxy_unavailable",
                        completed_at=datetime.now(UTC).isoformat(),
                    )
                    return

                # Post-HTTP cancellation re-check (paired mode)
                _jpost = _eng.get_eval_run(db, run_id)
                if _jpost is None or _jpost.get("status") in ("failed", "cancelled"):
                    _log.info("run_eval_judge: cancelled during HTTP call for run_id=%d", run_id)
                    return
        else:
            # Standard rubric/binary modes: judge each target independently
            for is_same, target_list in [(True, same_targets), (False, diff_targets)]:
                for target in target_list:
                    judge_pairs.append([source_item_id, str(target["id"])])
                    try:
                        _was_parse_fail = _judge_one_target(
                            db=db,
                            run_id=run_id,
                            variant=gen_result["variant"],
                            source_item_id=source_item_id,
                            source_item_title=_source_title,
                            principle=principle,
                            target=target,
                            is_same=is_same,
                            judge_model=judge_model,
                            judge_temperature=judge_temperature,
                            source_tag=source_tag,
                            http_base=http_base,
                            source_cluster_id=source_cid,
                            judge_mode=judge_mode,
                            backend=backend,
                        )
                        if _was_parse_fail:
                            parse_failures += 1
                    except _eng._ProxyDownError as exc:
                        _log.warning("run_eval_judge: proxy down — aborting run_id=%d: %s", run_id, exc)
                        _eng.update_eval_run(
                            db,
                            run_id,
                            status="failed",
                            error="proxy_unavailable",
                            completed_at=datetime.now(UTC).isoformat(),
                        )
                        return

                    # Post-HTTP cancellation re-check (rubric/binary mode)
                    _jpost = _eng.get_eval_run(db, run_id)
                    if _jpost is None or _jpost.get("status") in ("failed", "cancelled"):
                        _log.info("run_eval_judge: cancelled during HTTP call for run_id=%d", run_id)
                        return

    # Persist exact (source_item_id, target_item_id) pairs for reproducibility.
    # This overwrites the coarse source-only item_ids stored during generation.
    if judge_pairs:
        _eng.update_eval_run(db, run_id, item_ids=json.dumps(judge_pairs))
        _log.info("run_eval_judge: persisted %d judge pairs for run_id=%d", len(judge_pairs), run_id)

    # Log and persist judge parse failures for observability (#22)
    if parse_failures > 0:
        _log.warning(
            "run_eval_judge: run_id=%d had %d judge parse failures",
            run_id,
            parse_failures,
        )
        _eng.update_eval_run(db, run_id, judge_parse_failures=parse_failures)

    scored_rows = _eng._fetch_scored_rows(db, run_id)
    if judge_mode in ("tournament", "bayesian"):
        # V2 metrics: use paired/Bayesian metrics instead of F1
        v2_rows = _eng._fetch_v2_scored_rows(db, run_id)
        metrics = _eng.compute_tournament_metrics(v2_rows) if judge_mode == "tournament" else {}
        bayesian_m = _eng.compute_bayesian_metrics(v2_rows) if judge_mode == "bayesian" else {}
        # Merge bayesian AUC into metrics for winner selection
        for vid, bm in bayesian_m.items():
            metrics.setdefault(vid, {}).update(bm)
        winner = max(metrics.keys(), key=lambda v: metrics[v].get("auc", 0)) if metrics else None
    else:
        metrics = _eng.compute_metrics(scored_rows)
        winner = max(metrics.keys(), key=lambda v: metrics[v]["f1"]) if metrics else None
    report_md = _eng.render_report(run_id, metrics, db)

    # Persist full metrics snapshot and completion timestamp for trend analysis
    # and the repeat endpoint to verify reproducibility data is present.
    _eng.update_eval_run(
        db,
        run_id,
        status="complete",
        stage=None,
        metrics=json.dumps(metrics),
        winner_variant=winner,
        report_md=report_md,
        completed_at=datetime.now(UTC).isoformat(),
    )
