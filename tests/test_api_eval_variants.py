"""Tests for eval variant and template API endpoints."""

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
    """After init there should be system variants (A-H)."""
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    assert len(variants) == 8
    ids = {v["id"] for v in variants}
    assert ids == {"A", "B", "C", "D", "E", "F", "G", "H"}


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

    # Confirm list count is still 8 (unchanged — preview doesn't create)
    list_resp = client.get("/api/eval/variants")
    assert len(list_resp.json()) == 8


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
    """After init there should be system templates (3 original + contrastive + multistage)."""
    resp = client.get("/api/eval/templates")
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) == 5
    ids = {t["id"] for t in templates}
    assert ids == {"fewshot", "zero-shot-causal", "chunked", "contrastive", "contrastive-multistage"}


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
