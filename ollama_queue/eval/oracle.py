"""Oracle validation — cross-validates judge decisions with a reference model.

What it shows: Agreement rate between judge and oracle reference model.
Decision it drives: Detects judge unreliability before trusting auto-promote.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import ollama_queue.eval.engine as _eng

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)

# Uniform 5-class assumption: sum of (1/5)^2 * 5 = 0.2
_PE_UNIFORM_5CLASS = 0.2


def _is_oracle_enabled(settings: dict) -> bool:
    """Return True if oracle_enabled is truthy.

    Handles bool False, strings 'false'/'0'/'', and None.
    """
    val = settings.get("eval.oracle_enabled")
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "")
    return bool(val)


def _parse_oracle_score(text: str | None) -> int | None:
    """Extract a 1-5 integer score from oracle response text.

    Looks for the first standalone digit 1-5.
    Returns None if parsing fails.
    """
    import re

    if not text:
        return None
    match = re.search(r"\b([1-5])\b", text)
    if match:
        return int(match.group(1))
    return None


def run_oracle_validation(db: Database, run_id: int, settings: dict) -> None:
    """Cross-validate judge decisions with an oracle reference model.

    Computes Cohen's kappa between judge scores and oracle scores on a sample
    of judge results for the run. Stores the result as oracle_json on the run row.

    Args:
        db: Database instance.
        run_id: The eval run to validate.
        settings: Dict of eval settings (from db.get_setting or similar).
    """
    if not _is_oracle_enabled(settings):
        _log.debug("oracle: disabled for run %d — skipping", run_id)
        return

    oracle_model = settings.get("eval.oracle_model") or ""

    if not oracle_model:
        _log.warning("oracle: no oracle_model configured — skipping run %d", run_id)
        return

    # Fetch up to 20 judge results with non-null score_transfer
    with db._lock:
        conn = db._connect()
        rows = [
            dict(r)
            for r in conn.execute(
                """SELECT principle, score_transfer, target_item_id, is_same_cluster,
                          source_item_id
                   FROM eval_results
                   WHERE run_id = ? AND row_type = 'judge' AND score_transfer IS NOT NULL
                   LIMIT 20""",
                (run_id,),
            ).fetchall()
        ]

    if len(rows) < 3:
        _log.info("oracle: fewer than 3 judge results for run %d — skipping", run_id)
        return

    # Import judge prompt builder
    from ollama_queue.eval.judge import build_judge_prompt

    # Resolve HTTP base from engine default
    http_base = "http://127.0.0.1:7683"

    agreement_count = 0
    disagreement_count = 0
    oracle_scores: list[int] = []
    judge_scores: list[int] = []

    for row in rows:
        principle = row.get("principle") or ""
        judge_score = row.get("score_transfer")
        is_same = bool(row.get("is_same_cluster", 0))

        if not principle or judge_score is None:
            continue

        # Build a minimal target_item from available row data
        target_item = {
            "id": row.get("target_item_id", ""),
            "title": "",
            "one_liner": "",
            "description": "",
        }

        prompt = build_judge_prompt(
            principle=principle,
            target_item=target_item,
            is_same_cluster=is_same,
        )

        try:
            response_text, _ = _eng._call_proxy(
                http_base=http_base,
                model=oracle_model,
                prompt=prompt,
                temperature=0.1,
                num_ctx=2048,
                timeout=60,
                source=f"eval-oracle-{run_id}",
                priority=9,
            )
        except _eng._ProxyDownError as exc:
            _log.warning("oracle: proxy down for run %d: %s", run_id, exc)
            return

        oracle_score = _parse_oracle_score(response_text)
        if oracle_score is None:
            continue

        judge_scores.append(int(judge_score))
        oracle_scores.append(oracle_score)

        if abs(oracle_score - int(judge_score)) <= 1:
            agreement_count += 1
        else:
            disagreement_count += 1

    sample_size = len(judge_scores)
    if sample_size == 0:
        _log.warning("oracle: no parseable oracle scores for run %d", run_id)
        return

    po = agreement_count / sample_size
    pe = _PE_UNIFORM_5CLASS
    kappa = (po - pe) / (1 - pe) if (1 - pe) > 0 else 0.0

    oracle_json = json.dumps(
        {
            "kappa": round(kappa, 4),
            "agreement_pct": round(po * 100, 2),
            "disagreement_count": disagreement_count,
            "sample_size": sample_size,
            "oracle_model": oracle_model,
            "run_at": datetime.now(UTC).isoformat(),
        }
    )

    _eng.update_eval_run(db, run_id, oracle_json=oracle_json)
    _log.info(
        "oracle: run %d — kappa=%.3f, agreement=%.1f%%, sample=%d",
        run_id,
        kappa,
        po * 100,
        sample_size,
    )
