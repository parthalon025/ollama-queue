"""Tests for Forge DB mixin — CRUD for forge tables."""


def test_create_forge_run(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
        pairs_per_quartile=20,
    )
    assert isinstance(run_id, int)
    assert run_id > 0


def test_get_forge_run(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    run = db.get_forge_run(run_id)
    assert run is not None
    assert run["status"] == "queued"
    assert run["judge_model"] == "qwen3:14b"


def test_get_forge_run_not_found(db):
    assert db.get_forge_run(9999) is None


def test_update_forge_run_status(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.update_forge_run(run_id, status="judging")
    run = db.get_forge_run(run_id)
    assert run["status"] == "judging"


def test_insert_forge_result(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.insert_forge_result(
        run_id=run_id,
        source_item_id="101",
        target_item_id="102",
        embedding_similarity=0.85,
        quartile="q1_likely",
        judge_score=4,
    )
    results = db.get_forge_results(run_id)
    assert len(results) == 1
    assert results[0]["judge_score"] == 4
    assert results[0]["embedding_similarity"] == 0.85


def test_insert_forge_result_dedup(db):
    """INSERT OR IGNORE — duplicate pair skipped."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.insert_forge_result(
        run_id=run_id,
        source_item_id="101",
        target_item_id="102",
        embedding_similarity=0.85,
        quartile="q1_likely",
        judge_score=4,
    )
    db.insert_forge_result(
        run_id=run_id,
        source_item_id="101",
        target_item_id="102",
        embedding_similarity=0.85,
        quartile="q1_likely",
        judge_score=5,
    )
    results = db.get_forge_results(run_id)
    assert len(results) == 1
    assert results[0]["judge_score"] == 4  # first insert wins


def test_update_forge_result_oracle(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.insert_forge_result(
        run_id=run_id,
        source_item_id="101",
        target_item_id="102",
        embedding_similarity=0.85,
        quartile="q1_likely",
        judge_score=4,
    )
    results = db.get_forge_results(run_id)
    db.update_forge_result_oracle(results[0]["id"], oracle_score=3, oracle_reasoning="Looks ok")
    updated = db.get_forge_results(run_id)
    assert updated[0]["oracle_score"] == 3
    assert updated[0]["oracle_reasoning"] == "Looks ok"


def test_store_forge_embedding(db):
    db.store_forge_embedding("item-101", "abc123", [0.1, 0.2, 0.3])
    vec = db.get_forge_embedding("item-101", "abc123")
    assert vec is not None
    assert len(vec) == 3
    assert abs(vec[0] - 0.1) < 1e-6


def test_get_forge_embedding_miss(db):
    assert db.get_forge_embedding("missing", "abc123") is None


def test_list_forge_runs(db):
    db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="m",
        oracle_model="o",
    )
    db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="B",
        judge_model="m",
        oracle_model="o",
    )
    runs = db.list_forge_runs()
    assert len(runs) == 2
