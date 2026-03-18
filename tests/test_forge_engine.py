"""Tests for Forge engine — pipeline orchestration."""

import json
from unittest.mock import MagicMock, patch

from ollama_queue.forge.engine import run_forge_cycle


def _mock_items():
    return [
        {"id": "1", "title": "Exception swallowed", "one_liner": "Bare except", "description": "Hides errors"},
        {"id": "2", "title": "Missing await", "one_liner": "Async without await", "description": "Silently skips"},
        {"id": "3", "title": "Schema drift", "one_liner": "Producer changed", "description": "Consumer breaks"},
        {"id": "4", "title": "Cold start", "one_liner": "Works steady", "description": "Fails on restart"},
    ]


def test_run_forge_cycle_happy_path(db):
    """Full pipeline with mocked HTTP calls."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="test-judge",
        oracle_model="test-oracle",
        pairs_per_quartile=2,
        seed=42,
    )

    # Mock data source
    mock_fetch = MagicMock(return_value=_mock_items())
    # Mock embedder
    mock_embed = MagicMock(
        return_value={
            "1": [1.0, 0.0, 0.0],
            "2": [0.8, 0.2, 0.0],
            "3": [0.0, 1.0, 0.0],
            "4": [0.0, 0.0, 1.0],
        }
    )
    # Mock judge LLM
    mock_judge_call = MagicMock(return_value=('{"transfer": 4, "reasoning": "good"}', {}, None))
    # Mock oracle LLM
    mock_oracle_call = MagicMock(return_value=('{"transfer": 4, "reasoning": "agree"}', {}, None))

    with (
        patch("ollama_queue.forge.engine._fetch_items", mock_fetch),
        patch("ollama_queue.forge.engine.embed_items", mock_embed),
        patch("ollama_queue.forge.engine._call_judge", mock_judge_call),
        patch("ollama_queue.forge.engine._call_oracle", mock_oracle_call),
    ):
        run_forge_cycle(db=db, run_id=run_id, http_base="http://127.0.0.1:7683")

    run = db.get_forge_run(run_id)
    assert run["status"] == "complete"
    assert run["metrics_json"] is not None
    metrics = json.loads(run["metrics_json"])
    assert "f1" in metrics
    assert "kappa" in metrics


def test_run_forge_cycle_marks_failed_on_error(db):
    """Pipeline marks run as failed on unhandled exception."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="test",
        oracle_model="test",
    )

    with patch("ollama_queue.forge.engine._fetch_items", side_effect=RuntimeError("boom")):
        run_forge_cycle(db=db, run_id=run_id, http_base="http://127.0.0.1:7683")

    run = db.get_forge_run(run_id)
    assert run["status"] == "failed"
    assert "boom" in run["error"]


def test_run_forge_cycle_respects_cancellation(db):
    """Pipeline exits early if run is cancelled mid-judge."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="test",
        oracle_model="test",
        pairs_per_quartile=5,
        seed=42,
    )

    call_count = 0

    def mock_judge(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            db.update_forge_run(run_id, status="cancelled")
        return ('{"transfer": 3, "reasoning": "ok"}', {}, None)

    mock_embed = MagicMock(
        return_value={
            "1": [1.0, 0.0],
            "2": [0.0, 1.0],
            "3": [0.5, 0.5],
            "4": [0.3, 0.7],
        }
    )

    with (
        patch("ollama_queue.forge.engine._fetch_items", return_value=_mock_items()),
        patch("ollama_queue.forge.engine.embed_items", mock_embed),
        patch("ollama_queue.forge.engine._call_judge", mock_judge),
    ):
        run_forge_cycle(db=db, run_id=run_id, http_base="http://127.0.0.1:7683")

    run = db.get_forge_run(run_id)
    assert run["status"] == "cancelled"
