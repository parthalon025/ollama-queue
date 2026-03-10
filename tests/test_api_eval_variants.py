"""Tests for eval variant and template API endpoints."""

import json

import pytest
from fastapi.testclient import TestClient

from ollama_queue.api import create_app
from ollama_queue.db import Database


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app)


@pytest.fixture
def client_and_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


# --- Variants ---


def test_list_variants_returns_system_variants(client):
    """After init there should be system variants (A-H + M)."""
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    assert len(variants) == 9
    ids = {v["id"] for v in variants}
    assert ids == {"A", "B", "C", "D", "E", "F", "G", "H", "M"}


def test_list_variants_includes_latest_f1_null_when_no_runs(client):
    """latest_f1 should be null when there are no completed runs."""
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    for v in resp.json():
        assert "latest_f1" in v
        assert v["latest_f1"] is None


def test_create_user_variant_returns_201(client):
    """POST /api/eval/variants should create a user variant and return 201."""
    body = {
        "label": "My custom variant",
        "prompt_template_id": "zero-shot-causal",
        "model": "qwen2.5:7b",
        "temperature": 0.5,
        "num_ctx": 4096,
    }
    resp = client.post("/api/eval/variants", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["label"] == "My custom variant"
    assert data["model"] == "qwen2.5:7b"
    assert data["is_system"] == 0


def test_create_user_variant_appears_in_list(client):
    """Created variant should appear in subsequent GET."""
    client.post(
        "/api/eval/variants",
        json={
            "label": "Test variant",
            "prompt_template_id": "fewshot",
            "model": "deepseek-r1:8b",
        },
    )
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    labels = [v["label"] for v in resp.json()]
    assert "Test variant" in labels


def test_create_variant_missing_required_fields_returns_400(client):
    """POST without required fields should return 400."""
    resp = client.post("/api/eval/variants", json={"label": "incomplete"})
    assert resp.status_code == 400


def test_create_variant_invalid_template_returns_404(client):
    """POST with a non-existent prompt_template_id should return 404."""
    resp = client.post(
        "/api/eval/variants",
        json={
            "label": "Bad template",
            "prompt_template_id": "does-not-exist",
            "model": "qwen2.5:7b",
        },
    )
    assert resp.status_code == 404


def test_update_user_variant_returns_updated_row(client):
    """PUT /api/eval/variants/{id} should update and return the updated row."""
    # Create a user variant first
    create_resp = client.post(
        "/api/eval/variants",
        json={
            "label": "Before update",
            "prompt_template_id": "zero-shot-causal",
            "model": "qwen2.5:7b",
        },
    )
    var_id = create_resp.json()["id"]

    update_resp = client.put(f"/api/eval/variants/{var_id}", json={"label": "After update"})
    assert update_resp.status_code == 200
    assert update_resp.json()["label"] == "After update"


def test_update_system_variant_returns_422(client):
    """PUT on a system variant should return 422."""
    resp = client.put("/api/eval/variants/A", json={"label": "Hacked"})
    assert resp.status_code == 422
    assert "clone" in resp.json()["detail"].lower()


def test_delete_user_variant_returns_204(client):
    """DELETE /api/eval/variants/{id} on a user variant should return 204."""
    create_resp = client.post(
        "/api/eval/variants",
        json={
            "label": "To be deleted",
            "prompt_template_id": "zero-shot-causal",
            "model": "qwen2.5:7b",
        },
    )
    var_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/eval/variants/{var_id}")
    assert del_resp.status_code == 204


def test_delete_system_variant_returns_422(client):
    """DELETE on a system variant should return 422."""
    resp = client.delete("/api/eval/variants/B")
    assert resp.status_code == 422
    assert "clone" in resp.json()["detail"].lower()


def test_clone_system_variant_creates_user_variant(client):
    """POST /api/eval/variants/{id}/clone should clone any variant into a new user variant."""
    resp = client.post("/api/eval/variants/E/clone", json={})
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] != "E"
    assert data["is_system"] == 0
    assert "E" in data["label"] or "copy" in data["label"]


def test_clone_variant_with_custom_label(client):
    """Clone should use provided label if given."""
    resp = client.post("/api/eval/variants/D/clone", json={"label": "My D clone"})
    assert resp.status_code == 201
    assert resp.json()["label"] == "My D clone"


def test_clone_creates_variant_with_different_id(client):
    """Cloned variant must have a different ID from the original."""
    resp = client.post("/api/eval/variants/A/clone", json={})
    assert resp.status_code == 201
    assert resp.json()["id"] != "A"


def test_generate_variants_creates_one_per_model(client):
    """POST /api/eval/variants/generate should create N variants for N models."""
    resp = client.post(
        "/api/eval/variants/generate",
        json={
            "models": ["qwen2.5:7b", "deepseek-r1:8b", "llama3.2:3b"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 3
    assert len(data["variants"]) == 3


def test_generate_variants_uses_provided_template(client):
    """POST /api/eval/variants/generate with template_id should use that template."""
    resp = client.post(
        "/api/eval/variants/generate",
        json={
            "models": ["qwen2.5:7b"],
            "template_id": "fewshot",
        },
    )
    assert resp.status_code == 200
    variant = resp.json()["variants"][0]
    assert variant["prompt_template_id"] == "fewshot"


def test_generate_variants_invalid_template_returns_404(client):
    """generate with unknown template_id should return 404."""
    resp = client.post(
        "/api/eval/variants/generate",
        json={
            "models": ["qwen2.5:7b"],
            "template_id": "non-existent",
        },
    )
    assert resp.status_code == 404


def test_generate_preview_returns_count_without_creating(client):
    """GET /api/eval/variants/generate/preview should return proposed count/names without DB writes."""
    resp = client.get("/api/eval/variants/generate/preview?models=qwen2.5:7b,deepseek-r1:8b")
    assert resp.status_code == 200
    data = resp.json()
    assert data["would_create"] == 2
    assert len(data["names"]) == 2

    # Confirm list count is still 9 (unchanged — preview doesn't create)
    list_resp = client.get("/api/eval/variants")
    assert len(list_resp.json()) == 9


def test_generate_preview_empty_models_returns_zero(client):
    """Preview with empty models param returns zero."""
    resp = client.get("/api/eval/variants/generate/preview?models=")
    assert resp.status_code == 200
    assert resp.json()["would_create"] == 0


def test_variant_history_returns_empty_when_no_runs(client):
    """GET /api/eval/variants/{id}/history should return empty list when no completed runs."""
    resp = client.get("/api/eval/variants/A/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_variant_history_returns_404_for_unknown_variant(client):
    """GET history for non-existent variant should return 404."""
    resp = client.get("/api/eval/variants/ZZZZ/history")
    assert resp.status_code == 404


def test_export_variants_returns_json_with_user_variants(client):
    """GET /api/eval/variants/export should include user variants and their templates."""
    # Create a user variant first
    client.post(
        "/api/eval/variants",
        json={
            "label": "Export me",
            "prompt_template_id": "fewshot",
            "model": "qwen2.5:7b",
        },
    )
    resp = client.get("/api/eval/variants/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "variants" in data
    assert "templates" in data
    assert "exported_at" in data
    # Only user variants (is_system=0) exported
    assert all(v["is_system"] == 0 for v in data["variants"])
    labels = [v["label"] for v in data["variants"]]
    assert "Export me" in labels


def test_export_excludes_system_variants(client):
    """Export should not include system variants (A-E)."""
    resp = client.get("/api/eval/variants/export")
    data = resp.json()
    system_ids = {v["id"] for v in data["variants"]}
    assert not system_ids.intersection({"A", "B", "C", "D", "E"})


def test_import_variants_creates_new_entries(client):
    """POST /api/eval/variants/import should create variants from the provided list."""
    import uuid

    new_id = str(uuid.uuid4())[:8]
    resp = client.post(
        "/api/eval/variants/import",
        json={
            "variants": [
                {
                    "id": new_id,
                    "label": "Imported variant",
                    "prompt_template_id": "zero-shot-causal",
                    "model": "qwen2.5:7b",
                    "temperature": 0.6,
                    "num_ctx": 8192,
                }
            ],
            "templates": [],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["variants_imported"] >= 1


def test_import_is_idempotent(client):
    """Importing the same variant twice should not fail (INSERT OR IGNORE)."""
    import uuid

    new_id = str(uuid.uuid4())[:8]
    payload = {
        "variants": [
            {
                "id": new_id,
                "label": "Idempotent import",
                "prompt_template_id": "zero-shot-causal",
                "model": "qwen2.5:7b",
            }
        ],
        "templates": [],
    }
    resp1 = client.post("/api/eval/variants/import", json=payload)
    resp2 = client.post("/api/eval/variants/import", json=payload)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Second import should not duplicate
    assert resp2.json()["variants_imported"] == 0


# --- Templates ---


def test_list_templates_returns_system_templates(client):
    """After init there should be system templates (3 original + contrastive + multistage + mechanism)."""
    resp = client.get("/api/eval/templates")
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) == 6
    ids = {t["id"] for t in templates}
    assert ids == {"fewshot", "zero-shot-causal", "chunked", "contrastive", "contrastive-multistage", "mechanism"}


def test_update_system_template_returns_422(client):
    """PUT on a system template should return 422."""
    resp = client.put("/api/eval/templates/fewshot", json={"label": "Hacked"})
    assert resp.status_code == 422
    assert "clone" in resp.json()["detail"].lower()


def test_clone_system_template_creates_user_template(client):
    """POST /api/eval/templates/{id}/clone should create a new user template."""
    resp = client.post("/api/eval/templates/fewshot/clone", json={})
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] != "fewshot"
    assert data["is_system"] == 0
    assert "fewshot" in data["label"].lower() or "copy" in data["label"].lower()


def test_clone_template_with_custom_label(client):
    """Clone should use provided label if given."""
    resp = client.post("/api/eval/templates/chunked/clone", json={"label": "My chunked"})
    assert resp.status_code == 201
    assert resp.json()["label"] == "My chunked"


def test_update_user_template(client):
    """PUT on a cloned (user) template should update it."""
    clone_resp = client.post("/api/eval/templates/zero-shot-causal/clone", json={})
    tmpl_id = clone_resp.json()["id"]

    update_resp = client.put(
        f"/api/eval/templates/{tmpl_id}",
        json={
            "label": "Updated label",
            "instruction": "Updated instruction",
        },
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["label"] == "Updated label"
    assert data["instruction"] == "Updated instruction"


def test_clone_template_missing_template_returns_404(client):
    """Cloning a non-existent template should return 404."""
    resp = client.post("/api/eval/templates/does-not-exist/clone", json={})
    assert resp.status_code == 404


def test_list_variants_includes_description(client):
    """GET /api/eval/variants should include a non-empty description for all system variants."""
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    system_variants = [v for v in variants if v.get("is_system")]
    assert len(system_variants) >= 9
    for v in system_variants:
        assert "description" in v, f"Variant {v['id']} missing description key"
        assert (
            v["description"] and len(v["description"]) > 10
        ), f"Variant {v['id']} has empty description in API response"


# --- Stability ---


def test_variant_stability(client_and_db):
    """GET /api/eval/variants/stability returns cross-run stdev per variant."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        for run_id, f1 in [(1, 0.70), (2, 0.72), (3, 0.71)]:
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
                "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', ?)",
                (run_id, json.dumps({"A": {"f1": f1}})),
            )
        conn.commit()
    resp = client.get("/api/eval/variants/stability")
    assert resp.status_code == 200
    data = resp.json()
    assert "A" in data
    assert data["A"]["n_runs"] == 3
    assert data["A"]["stable"] is True


def test_variant_stability_empty(client):
    resp = client.get("/api/eval/variants/stability")
    assert resp.status_code == 200
    assert resp.json() == {}


# --- Variant Diff ---


def test_variant_diff(client_and_db):
    """GET /api/eval/variants/A/diff/B returns config differences."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO eval_variants "
            "(id, label, prompt_template_id, model, temperature, num_ctx, is_system, created_at) "
            "VALUES ('TEST_A', 'Test A', 'zero-shot-causal', 'qwen2.5:7b', 0.6, 4096, 1, datetime('now'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO eval_variants "
            "(id, label, prompt_template_id, model, temperature, num_ctx, is_system, created_at) "
            "VALUES ('TEST_B', 'Test B', 'zero-shot-causal', 'qwen3:14b', 0.8, 8192, 1, datetime('now'))"
        )
        conn.commit()
    resp = client.get("/api/eval/variants/TEST_A/diff/TEST_B")
    assert resp.status_code == 200
    data = resp.json()
    assert "changes" in data
    assert len(data["changes"]) >= 2


def test_variant_diff_identical(client_and_db):
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO eval_variants "
            "(id, label, prompt_template_id, model, temperature, num_ctx, is_system, created_at) "
            "VALUES ('TEST_X', 'Test X', 'zero-shot-causal', 'qwen2.5:7b', 0.6, 4096, 1, datetime('now'))"
        )
        conn.commit()
    resp = client.get("/api/eval/variants/TEST_X/diff/TEST_X")
    assert resp.status_code == 200
    assert resp.json()["changes"] == []


def test_variant_diff_not_found(client):
    resp = client.get("/api/eval/variants/NOPE/diff/ALSO_NOPE")
    assert resp.status_code == 404


# --- Coverage gap tests: variant/template/trends edge cases ---


def test_list_variants_latest_f1_from_completed_runs(client_and_db):
    """latest_f1 computed from eval_runs.metrics JSON. Covers lines 1128-1141."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', ?)",
            (json.dumps({"A": {"f1": 0.85, "precision": 0.9, "recall": 0.8}}),),
        )
        conn.commit()
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    a_var = next(v for v in variants if v["id"] == "A")
    assert a_var["latest_f1"] == 0.85


def test_list_variants_skips_bad_metrics_json(client_and_db):
    """Runs with invalid metrics JSON are skipped. Covers lines 1132-1133."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', 'not-valid-json')"
        )
        conn.commit()
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    a_var = next(v for v in variants if v["id"] == "A")
    assert a_var["latest_f1"] is None


def test_list_variants_skips_non_dict_metrics(client_and_db):
    """Non-dict variant metrics in the JSON are skipped. Covers lines 1136-1137."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', ?)",
            (json.dumps({"A": "not-a-dict", "B": {"f1": 0.7}}),),
        )
        conn.commit()
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    a_var = next(v for v in variants if v["id"] == "A")
    assert a_var["latest_f1"] is None
    b_var = next(v for v in variants if v["id"] == "B")
    assert b_var["latest_f1"] == 0.7


def test_list_variants_auc_for_bayesian_runs(client_and_db):
    """AUC used for bayesian judge mode runs. Covers lines 1134, 1139-1140."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, judge_mode) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', ?, 'bayesian')",
            (json.dumps({"A": {"auc": 0.92, "f1": 0.5}}),),
        )
        conn.commit()
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    a_var = next(v for v in variants if v["id"] == "A")
    # Should use auc (0.92), not f1 (0.5)
    assert a_var["latest_f1"] == 0.92


def test_import_variants_with_templates(client):
    """Import with templates creates them first. Covers lines 1207-1222."""
    import uuid

    tmpl_id = f"custom-{str(uuid.uuid4())[:6]}"
    var_id = f"v-{str(uuid.uuid4())[:6]}"
    resp = client.post(
        "/api/eval/variants/import",
        json={
            "templates": [
                {
                    "id": tmpl_id,
                    "label": "Custom Template",
                    "instruction": "Do the thing",
                    "format_spec": "JSON",
                    "examples": None,
                    "is_chunked": 0,
                }
            ],
            "variants": [
                {
                    "id": var_id,
                    "label": "Imported with template",
                    "prompt_template_id": tmpl_id,
                    "model": "test",
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["templates_imported"] == 1
    assert data["variants_imported"] == 1


def test_generate_variants_empty_models_returns_400(client):
    """Generate with empty models list returns 400. Covers line 1258."""
    resp = client.post("/api/eval/variants/generate", json={"models": []})
    assert resp.status_code == 400


def test_variant_stability_with_data_source_filter(client_and_db):
    """Stability with data_source filter. Covers line 1346."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        for run_id, f1, ds in [(1, 0.70, "http://a"), (2, 0.72, "http://a"), (3, 0.71, "http://b")]:
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
                "VALUES (?, ?, '[\"A\"]', 'A', 'complete', ?)",
                (run_id, ds, json.dumps({"A": {"f1": f1}})),
            )
        conn.commit()
    resp = client.get("/api/eval/variants/stability?data_source=http://a")
    assert resp.status_code == 200
    data = resp.json()
    assert "A" in data
    assert data["A"]["n_runs"] == 2


def test_variant_stability_bad_metrics_json(client_and_db):
    """Stability skips rows with bad metrics JSON. Covers lines 1360-1361."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', 'bad-json')"
        )
        conn.commit()
    resp = client.get("/api/eval/variants/stability")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_variant_history_with_completed_runs(client_and_db):
    """Variant history returns metrics from completed runs. Covers lines 1407-1416."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        for run_id, f1 in [(1, 0.7), (2, 0.8)]:
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, started_at) "
                "VALUES (?, 'http://localhost', '[\"A\"]', 'A', 'complete', ?, '2026-03-01')",
                (run_id, json.dumps({"A": {"f1": f1, "precision": 0.9, "recall": f1 - 0.1}})),
            )
        conn.commit()
    resp = client.get("/api/eval/variants/A/history")
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 2
    assert history[0]["f1"] == 0.7
    assert history[1]["f1"] == 0.8


def test_variant_history_skips_bad_metrics(client_and_db):
    """Variant history skips runs with bad metrics. Covers lines 1411-1412."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', 'bad')"
        )
        conn.commit()
    resp = client.get("/api/eval/variants/A/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_update_variant_no_updatable_fields(client):
    """Update with no updatable fields returns current variant unchanged. Covers line 1479."""
    create_resp = client.post(
        "/api/eval/variants",
        json={
            "label": "Test",
            "prompt_template_id": "zero-shot-causal",
            "model": "test",
        },
    )
    var_id = create_resp.json()["id"]
    resp = client.put(f"/api/eval/variants/{var_id}", json={"unrelated_field": "value"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "Test"


def test_update_variant_validates_template(client):
    """Update with bad prompt_template_id returns 404. Covers line 1482."""
    create_resp = client.post(
        "/api/eval/variants",
        json={
            "label": "Test",
            "prompt_template_id": "zero-shot-causal",
            "model": "test",
        },
    )
    var_id = create_resp.json()["id"]
    resp = client.put(
        f"/api/eval/variants/{var_id}",
        json={"prompt_template_id": "nonexistent-template"},
    )
    assert resp.status_code == 404


def test_update_template_no_updatable_fields(client):
    """Update template with no updatable fields returns current template. Covers line 1533."""
    clone_resp = client.post("/api/eval/templates/fewshot/clone", json={})
    tmpl_id = clone_resp.json()["id"]
    resp = client.put(f"/api/eval/templates/{tmpl_id}", json={"unrelated": "value"})
    assert resp.status_code == 200
    assert resp.json()["id"] == tmpl_id


def test_trends_with_completed_runs(client_and_db):
    """Trends computes direction and stability from run metrics. Covers lines 1605-1671."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        # Insert 5 runs with improving F1 to trigger "improving" direction
        for run_id in range(1, 6):
            f1 = 0.5 + run_id * 0.05
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, "
                "started_at, item_count, item_ids, judge_mode) "
                "VALUES (?, 'http://localhost', '[\"A\"]', 'A', 'complete', ?, "
                "'2026-03-01', 10, '[1,2,3]', 'binary')",
                (run_id, json.dumps({"A": {"f1": f1, "precision": 0.9, "recall": f1}})),
            )
        conn.commit()
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert "A" in data["variants"]
    variant_data = data["variants"]["A"]
    assert variant_data["trend_direction"] == "improving"
    assert variant_data["stability"] is not None


def test_trends_bad_metrics_json_skipped(client_and_db):
    """Trends skips runs with invalid metrics JSON. Covers lines 1608-1609."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, "
            "started_at, item_count, item_ids, judge_mode) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', 'not-json', "
            "'2026-03-01', 10, '', 'binary')"
        )
        conn.commit()
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"] == {}


def test_trends_non_dict_variant_metrics_skipped(client_and_db):
    """Trends skips non-dict variant metrics. Covers lines 1613-1614."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, "
            "started_at, item_count, item_ids, judge_mode) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', ?, "
            "'2026-03-01', 10, '', 'binary')",
            (json.dumps({"A": "string-not-dict"}),),
        )
        conn.commit()
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"] == {}


def test_trends_agreement_rate(client_and_db):
    """Trends computes judge_agreement_rate from eval_results. Covers lines 1635-1637."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, "
            "started_at, item_count, item_ids, judge_mode) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', ?, "
            "'2026-03-01', 10, '', 'binary')",
            (json.dumps({"A": {"f1": 0.8}}),),
        )
        # 3 of 4 results have score_transfer > 1 (agreed)
        for i, score in enumerate([3, 4, 5, 1], start=1):
            conn.execute(
                "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
                "is_same_cluster, score_transfer, row_type) "
                "VALUES (1, 'A', ?, ?, 1, ?, 'judge')",
                (str(i), str(i + 10), score),
            )
        conn.commit()
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    variant_data = resp.json()["variants"]["A"]
    assert variant_data["judge_agreement_rate"] == 0.75


def test_trends_regressing_direction(client_and_db):
    """Trends with declining F1 shows 'regressing'. Covers lines 1670-1671."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        for run_id in range(1, 6):
            f1 = 0.9 - run_id * 0.05
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, "
                "started_at, item_count, item_ids, judge_mode) "
                "VALUES (?, 'http://localhost', '[\"A\"]', 'A', 'complete', ?, "
                "'2026-03-01', 10, '', 'binary')",
                (run_id, json.dumps({"A": {"f1": f1}})),
            )
        conn.commit()
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"]["A"]["trend_direction"] == "regressing"


def test_trends_item_sets_differ(client_and_db):
    """Trends detects when item_ids vary across runs. Covers line 1682."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        for run_id, item_ids in [(1, "[1,2,3]"), (2, "[4,5,6]")]:
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, "
                "started_at, item_count, item_ids, judge_mode) "
                "VALUES (?, 'http://localhost', '[\"A\"]', 'A', 'complete', ?, "
                "'2026-03-01', 3, ?, 'binary')",
                (run_id, json.dumps({"A": {"f1": 0.8}}), item_ids),
            )
        conn.commit()
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["item_sets_differ"] is True


def test_trends_empty_metrics_skipped(client_and_db):
    """Trends skips runs with null metrics. Covers line 1604-1605."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, "
            "started_at, item_count, item_ids, judge_mode) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', NULL, "
            "'2026-03-01', 10, '', 'binary')"
        )
        conn.commit()
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"] == {}
