"""Post-hoc cost estimation for eval runs.

What it shows: Estimated USD cost of LLM calls in a run.
Decision it drives: Helps users choose cost-effective configurations.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import ollama_queue.eval.engine as _eng

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)

# USD per 1k tokens (rough estimates — ollama is free, cloud providers are not)
_COST_RATES: dict[str, float] = {
    "ollama": 0.0,
    "claude": 0.003,
    "openai": 0.002,
}

# Average token counts per call type
_TOKENS_PER_JUDGE_CALL = 1200
_TOKENS_PER_GEN_CALL = 1500


def _provider_cost(provider: str, token_count: int) -> float:
    """Compute estimated USD cost for a given provider and total token count."""
    rate = _COST_RATES.get(provider, 0.002)  # unknown providers default to openai rate
    return round(rate * token_count / 1000, 6)


def compute_run_cost(db: Database, run_id: int, settings: dict) -> None:
    """Compute post-hoc cost estimate for a completed eval run.

    Counts judge and generate rows from eval_results, applies per-provider
    token rate estimates, and stores the result as cost_json on the run row.

    Args:
        db: Database instance.
        run_id: The eval run to cost.
        settings: Dict of eval settings (from db.get_setting or similar).
    """
    judge_provider = settings.get("eval.judge_provider") or "ollama"
    gen_provider = settings.get("eval.generator_provider") or "ollama"

    with db._lock:
        conn = db._connect()
        judge_count = conn.execute(
            "SELECT COUNT(*) FROM eval_results WHERE run_id = ? AND row_type = 'judge'",
            (run_id,),
        ).fetchone()[0]
        gen_count = conn.execute(
            "SELECT COUNT(*) FROM eval_results WHERE run_id = ? AND row_type = 'generate'",
            (run_id,),
        ).fetchone()[0]

    judge_tokens = judge_count * _TOKENS_PER_JUDGE_CALL
    gen_tokens = gen_count * _TOKENS_PER_GEN_CALL

    judge_usd = _provider_cost(judge_provider, judge_tokens)
    gen_usd = _provider_cost(gen_provider, gen_tokens)
    total_usd = round(judge_usd + gen_usd, 6)

    cost_json = json.dumps(
        {
            "total_usd": total_usd,
            "judge_usd": judge_usd,
            "gen_usd": gen_usd,
            "judge_pairs": judge_count,
            "gen_count": gen_count,
            "judge_provider": judge_provider,
            "gen_provider": gen_provider,
            "note": "estimated",
        }
    )

    _eng.update_eval_run(db, run_id, cost_json=cost_json)
    _log.info(
        "cost: run %d — total=$%.4f (judge=%sx%d, gen=%sx%d)",
        run_id,
        total_usd,
        judge_provider,
        judge_count,
        gen_provider,
        gen_count,
    )
