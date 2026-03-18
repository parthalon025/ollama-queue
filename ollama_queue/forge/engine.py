"""Forge engine — orchestrates the full evaluation cycle.

Flow: fetch items -> embed -> select pairs -> judge -> oracle -> calibrate -> metrics.
Each step delegates to a focused module. The engine is the only module
that imports from all the others.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import httpx

from ollama_queue.forge.calibrator import apply_calibration, fit_calibration
from ollama_queue.forge.embedder import embed_items
from ollama_queue.forge.engine_evolve import run_evolve_phase
from ollama_queue.forge.judge import build_judge_prompt, parse_judge_response
from ollama_queue.forge.metrics import compute_forge_metrics
from ollama_queue.forge.oracle import compute_kappa, select_oracle_sample
from ollama_queue.forge.pairs import build_similarity_matrix, select_stratified_pairs
from ollama_queue.forge.settings import get_forge_setting

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


def _fetch_items(data_source_url: str, limit: int = 100) -> list[dict]:
    """Fetch items from a remote ForgeDataSource via HTTP."""
    resp = httpx.get(f"{data_source_url}/eval/items", params={"limit": limit}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _call_judge(*, http_base: str, model: str, prompt: str, temperature: float) -> tuple[str, dict, int | None]:
    """Call judge LLM via queue proxy."""
    from ollama_queue.eval.providers import get_provider

    provider = get_provider("ollama", http_base)
    return provider.generate(model=model, prompt=prompt, temperature=temperature)


def _call_oracle(
    *,
    http_base: str,
    provider_name: str,
    model: str,
    prompt: str,
    api_key: str | None = None,
) -> tuple[str, dict, int | None]:
    """Call oracle LLM via configured provider."""
    from ollama_queue.eval.providers import get_provider

    provider = get_provider(provider_name, http_base, api_key=api_key)
    return provider.generate(model=model, prompt=prompt, temperature=0.1)


def _is_cancelled(db: Database, run_id: int) -> bool:
    """Check if run has been cancelled or failed externally."""
    run = db.get_forge_run(run_id)
    if run is None:
        return True
    return run["status"] in ("cancelled", "failed")


def _fail_run(db: Database, run_id: int, error: str) -> None:
    """Mark a run as failed with an error message."""
    db.update_forge_run(run_id, status="failed", error=error, completed_at=time.time())


def _run_judge_phase(
    db: Database,
    run_id: int,
    pairs: list[dict],
    item_lookup: dict,
    http_base: str,
    judge_model: str,
    judge_temp: float,
) -> bool:
    """Score all pairs with the judge LLM. Returns False if cancelled."""
    for pair in pairs:
        if _is_cancelled(db, run_id):
            return False

        source = item_lookup.get(pair["item_a"], {})
        target = item_lookup.get(pair["item_b"], {})
        principle = f"{source.get('title', '')}: {source.get('one_liner', '')}"
        prompt = build_judge_prompt(principle=principle, target=target)

        try:
            text, _, _ = _call_judge(http_base=http_base, model=judge_model, prompt=prompt, temperature=judge_temp)
        except Exception as exc:
            _log.warning("forge judge: call failed for pair %s->%s: %s", pair["item_a"], pair["item_b"], exc)
            text = ""

        parsed = parse_judge_response(text)
        db.insert_forge_result(
            run_id=run_id,
            source_item_id=pair["item_a"],
            target_item_id=pair["item_b"],
            embedding_similarity=pair["similarity"],
            quartile=pair["quartile"],
            judge_score=parsed["transfer"],
            judge_reasoning=parsed.get("judge_reasoning") or parsed.get("reasoning"),
        )
    return True


def _run_oracle_phase(
    db: Database,
    run_id: int,
    oracle_sample: list[dict],
    item_lookup: dict,
    http_base: str,
    oracle_model: str,
    oracle_provider: str,
) -> bool:
    """Re-score a sample with the oracle LLM. Returns False if cancelled."""
    for result in oracle_sample:
        if _is_cancelled(db, run_id):
            return False

        source = item_lookup.get(result["source_item_id"], {})
        target = item_lookup.get(result["target_item_id"], {})
        principle = f"{source.get('title', '')}: {source.get('one_liner', '')}"
        prompt = build_judge_prompt(principle=principle, target=target)

        try:
            text, _, _ = _call_oracle(
                http_base=http_base, provider_name=oracle_provider, model=oracle_model, prompt=prompt
            )
        except Exception as exc:
            _log.warning("forge oracle: call failed for result %s: %s", result["id"], exc)
            continue

        parsed = parse_judge_response(text)
        db.update_forge_result_oracle(
            result["id"], oracle_score=parsed["transfer"], oracle_reasoning=parsed.get("reasoning")
        )
    return True


def _run_calibrate_and_metrics(
    db: Database,
    run_id: int,
    run: dict,
) -> None:
    """Calibrate scores and compute final metrics."""
    results = db.get_forge_results(run_id)

    oracle_pairs = [r for r in results if r.get("oracle_score") is not None]
    judge_scores = [r["judge_score"] for r in oracle_pairs]
    oracle_scores = [r["oracle_score"] for r in oracle_pairs]

    cal = fit_calibration(judge_scores, oracle_scores)
    cal_json = json.dumps(cal) if cal else None

    # Apply calibration to all results
    for r in results:
        if r.get("judge_score") is not None:
            calibrated = apply_calibration(cal, r["judge_score"])
            with db._lock:
                conn = db._connect()
                conn.execute(
                    "UPDATE forge_results SET calibrated_score = ? WHERE id = ?",
                    (round(calibrated, 4), r["id"]),
                )
                conn.commit()

    # Compute metrics
    results = db.get_forge_results(run_id)
    threshold = get_forge_setting(db, "forge.positive_threshold", int)
    metrics = compute_forge_metrics(results, positive_threshold=threshold)

    # Oracle agreement JSON
    oracle_json = None
    if oracle_pairs:
        kappa = compute_kappa(judge_scores, oracle_scores, tolerance=1)
        agree_count = sum(1 for j, o in zip(judge_scores, oracle_scores, strict=False) if abs(j - o) <= 1)
        oracle_json = json.dumps(
            {
                "kappa": round(kappa, 4),
                "agreement_pct": round(agree_count / len(judge_scores) * 100, 2),
                "sample_size": len(oracle_pairs),
                "oracle_model": run["oracle_model"],
            }
        )

    db.update_forge_run(
        run_id,
        status="complete",
        metrics_json=json.dumps(metrics),
        calibration_json=cal_json,
        oracle_json=oracle_json,
        completed_at=time.time(),
    )

    _log.info(
        "forge engine: run %d complete — F1=%s, kappa=%s, spearman=%s",
        run_id,
        metrics.get("f1"),
        metrics.get("kappa"),
        metrics.get("spearman"),
    )

    run_evolve_phase(db=db, run_id=run_id, run=run, results=results, metrics=metrics)


def run_forge_cycle(*, db: Database, run_id: int, http_base: str) -> None:
    """Execute one full Forge evaluation cycle.

    Steps: embed -> pair -> judge -> oracle -> calibrate -> metrics.
    Never raises — marks run as failed on error.
    """
    try:
        _run_forge_cycle_inner(db=db, run_id=run_id, http_base=http_base)
    except Exception as exc:
        _log.exception("forge engine: run %d failed: %s", run_id, exc)
        _fail_run(db, run_id, str(exc))


def _run_setup_phase(db: Database, run_id: int, run: dict, http_base: str) -> tuple | None:
    """Fetch items, embed, select pairs. Returns (pairs, item_lookup) or None if failed."""
    items = _fetch_items(run["data_source_url"])
    if len(items) < 2:
        _fail_run(db, run_id, "Need at least 2 items")
        return None

    embedding_model = get_forge_setting(db, "forge.embedding_model", str)
    embeddings = embed_items(db=db, items=items, model=embedding_model, http_base=http_base)
    if len(embeddings) < 2:
        _fail_run(db, run_id, "Could not embed enough items")
        return None

    matrix = build_similarity_matrix(embeddings)
    per_q = run.get("pairs_per_quartile") or 20
    pairs = select_stratified_pairs(matrix, per_quartile=per_q, seed=run.get("seed"))
    if not pairs:
        _fail_run(db, run_id, "No pairs selected")
        return None

    return pairs, {item["id"]: item for item in items}


def _run_forge_cycle_inner(*, db: Database, run_id: int, http_base: str) -> None:
    run = db.get_forge_run(run_id)
    if run is None:
        return

    db.update_forge_run(run_id, status="embedding", started_at=time.time())

    # Steps 1-3: Fetch, embed, pair-select
    setup = _run_setup_phase(db, run_id, run, http_base)
    if setup is None:
        return
    pairs, item_lookup = setup

    # Step 4: Judge
    db.update_forge_run(run_id, status="judging")
    judge_temp = get_forge_setting(db, "forge.judge_temperature", float)
    if not _run_judge_phase(db, run_id, pairs, item_lookup, http_base, run["judge_model"], judge_temp):
        return

    if _is_cancelled(db, run_id):
        return

    # Step 5: Oracle
    db.update_forge_run(run_id, status="oracle")
    results = db.get_forge_results(run_id)
    oracle_fraction = get_forge_setting(db, "forge.oracle_fraction", float)
    oracle_budget = get_forge_setting(db, "forge.oracle_budget", int)
    oracle_sample = select_oracle_sample(results, fraction=oracle_fraction, budget=oracle_budget, seed=run.get("seed"))
    oracle_provider = get_forge_setting(db, "forge.oracle_provider", str)
    if not _run_oracle_phase(db, run_id, oracle_sample, item_lookup, http_base, run["oracle_model"], oracle_provider):
        return

    if _is_cancelled(db, run_id):
        return

    # Steps 6+7: Calibrate + Metrics
    db.update_forge_run(run_id, status="calibrating")
    _run_calibrate_and_metrics(db, run_id, run)
