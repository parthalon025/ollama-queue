"""Tests for eval settings and datasource API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import EVAL_SETTINGS_DEFAULTS, Database


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


# --- Eval Settings ---


def test_get_eval_settings_returns_all_eval_keys(client):
    """GET /api/eval/settings should return all 12 eval.* keys."""
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    # Verify all expected keys are present
    expected_keys = set(EVAL_SETTINGS_DEFAULTS.keys())
    assert expected_keys.issubset(set(data))
    # All returned keys must start with eval.
    assert all(k.startswith("eval.") for k in data)


def test_get_eval_settings_count_matches_defaults(client):
    """GET /api/eval/settings should return exactly as many keys as EVAL_SETTINGS_DEFAULTS."""
    resp = client.get("/api/eval/settings")
    assert len(resp.json()) == len(EVAL_SETTINGS_DEFAULTS)


def test_put_eval_settings_updates_values(client):
    """PUT /api/eval/settings should update and return new values."""
    resp = client.put("/api/eval/settings", json={"eval.per_cluster": 6})
    assert resp.status_code == 200
    # Returned dict should reflect the update
    data = resp.json()
    assert data["eval.per_cluster"] == 6
    # GET should also reflect it
    get_resp = client.get("/api/eval/settings")
    assert get_resp.json()["eval.per_cluster"] == 6


def test_put_eval_settings_partial_update(client):
    """PUT with only one key should not clobber others."""
    # Set judge_backend first
    client.put("/api/eval/settings", json={"eval.judge_backend": "openai"})
    # Update per_cluster only
    client.put("/api/eval/settings", json={"eval.per_cluster": 8})
    settings = client.get("/api/eval/settings").json()
    assert settings["eval.judge_backend"] == "openai"
    assert settings["eval.per_cluster"] == 8


def test_put_eval_settings_rejects_invalid_judge_backend(client):
    """PUT with invalid judge_backend should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.judge_backend": "anthropic"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("judge_backend" in str(err) for err in (detail if isinstance(detail, list) else [detail]))


def test_put_eval_settings_rejects_out_of_range_per_cluster(client):
    """PUT with per_cluster outside 1-20 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.per_cluster": 99})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_per_cluster_zero(client):
    """PUT with per_cluster = 0 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.per_cluster": 0})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_invalid_data_source_url(client):
    """PUT with a non-HTTP data_source_url should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.data_source_url": "ftp://wrong"})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_non_localhost_url(client):
    """PUT with a non-localhost data_source_url should return 422 (SSRF protection)."""
    resp = client.put("/api/eval/settings", json={"eval.data_source_url": "https://example.com"})
    assert resp.status_code == 422


def test_put_eval_settings_accepts_localhost_url(client):
    """PUT with a localhost data_source_url should succeed."""
    resp = client.put("/api/eval/settings", json={"eval.data_source_url": "http://127.0.0.1:7685"})
    assert resp.status_code == 200


def test_put_eval_settings_rejects_out_of_range_judge_temperature(client):
    """PUT with judge_temperature > 2.0 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.judge_temperature": 3.5})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_out_of_range_stability_window(client):
    """PUT with stability_window outside 1-20 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.stability_window": 25})
    assert resp.status_code == 422


def test_put_eval_settings_is_all_or_nothing_on_validation_failure(client):
    """If one field is invalid, no settings should be written (all-or-nothing)."""
    # Get current per_cluster value
    original = client.get("/api/eval/settings").json()["eval.per_cluster"]

    # Attempt to update per_cluster (valid) + judge_backend (invalid) together
    resp = client.put(
        "/api/eval/settings",
        json={
            "eval.per_cluster": 15,
            "eval.judge_backend": "invalid-backend",
        },
    )
    assert resp.status_code == 422

    # per_cluster must NOT have been updated
    after = client.get("/api/eval/settings").json()["eval.per_cluster"]
    assert after == original


def test_put_eval_settings_accepts_valid_batch(client):
    """PUT with multiple valid values should update all and return 200."""
    resp = client.put(
        "/api/eval/settings",
        json={
            "eval.judge_backend": "ollama",
            "eval.per_cluster": 5,
            "eval.f1_threshold": 0.8,
            "eval.stability_window": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.judge_backend"] == "ollama"
    assert data["eval.per_cluster"] == 5
    assert data["eval.f1_threshold"] == 0.8


# --- Datasource test ---


def test_datasource_test_returns_ok_when_reachable(client):
    """GET /api/eval/datasource/test should return ok=True when datasource responds 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "item_count": 763, "cluster_count": 12}

    with patch("httpx.get", return_value=mock_response):
        resp = client.get("/api/eval/datasource/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["item_count"] == 763
    assert data["cluster_count"] == 12
    assert isinstance(data["response_ms"], int)
    assert data["error"] is None


def test_datasource_test_returns_ok_false_on_connection_error(client):
    """GET /api/eval/datasource/test should return ok=False on connection refused."""
    with patch("httpx.get", side_effect=Exception("connection refused")):
        resp = client.get("/api/eval/datasource/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "connection refused" in data["error"]
    assert isinstance(data["response_ms"], int)


def test_datasource_test_returns_ok_false_on_http_error(client):
    """GET /api/eval/datasource/test should return ok=False on non-200 status."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.json.return_value = {}

    with patch("httpx.get", return_value=mock_response):
        resp = client.get("/api/eval/datasource/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "503" in data["error"]


def test_datasource_test_includes_response_ms(client):
    """response_ms should always be present and be a non-negative int."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "item_count": 10, "cluster_count": 2}

    with patch("httpx.get", return_value=mock_response):
        resp = client.get("/api/eval/datasource/test")

    data = resp.json()
    assert "response_ms" in data
    assert data["response_ms"] >= 0


# --- Eval Trends (basic sanity) ---


def test_eval_trends_returns_expected_shape_with_no_runs(client):
    """GET /api/eval/trends should return valid structure even with no completed runs."""
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert "variants" in data
    assert "item_sets_differ" in data
    assert isinstance(data["variants"], dict)
    assert data["item_sets_differ"] is False


def test_post_eval_datasource_prime_proxies_and_returns_result(client):
    """POST /api/eval/datasource/prime proxies to data source and returns its response."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True, "updated": 3, "item_count": 10, "cluster_count": 2}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response):
        resp = client.post("/api/eval/datasource/prime")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["updated"] == 3
    assert data["cluster_count"] == 2


def test_post_eval_datasource_prime_returns_502_when_offline(client):
    """POST /api/eval/datasource/prime returns 502 when data source is unreachable."""
    with patch("httpx.post", side_effect=Exception("Connection refused")):
        resp = client.post("/api/eval/datasource/prime")

    assert resp.status_code == 502


def test_auto_promote_defaults_to_false(client_and_db):
    """eval.auto_promote defaults to False."""
    client, _db = client_and_db
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.auto_promote"] is False


def test_auto_promote_min_improvement_default(client_and_db):
    """eval.auto_promote_min_improvement defaults to 0.05."""
    client, _db = client_and_db
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.auto_promote_min_improvement"] == pytest.approx(0.05)


def test_put_eval_settings_rejects_non_bool_auto_promote(client):
    """auto_promote must be a boolean — string 'yes' is rejected."""
    resp = client.put("/api/eval/settings", json={"eval.auto_promote": "yes"})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_out_of_range_auto_promote_min_improvement(client):
    """auto_promote_min_improvement must be 0.0-1.0 -- 1.5 and -0.1 are rejected."""
    for bad_val in [1.5, -0.1]:
        resp = client.put("/api/eval/settings", json={"eval.auto_promote_min_improvement": bad_val})
        assert resp.status_code == 422, f"Expected 422 for value {bad_val}, got {resp.status_code}"


def test_can_save_auto_promote_settings(client_and_db):
    """Can save both new auto-promote settings via PUT."""
    client, _db = client_and_db
    resp = client.put(
        "/api/eval/settings",
        json={
            "eval.auto_promote": True,
            "eval.auto_promote_min_improvement": 0.10,
        },
    )
    assert resp.status_code == 200
    # PUT returns updated settings dict — verify the new values are present
    put_data = resp.json()
    assert put_data["eval.auto_promote"] is True
    assert put_data["eval.auto_promote_min_improvement"] == pytest.approx(0.10)
    # Read back via GET to confirm persistence
    resp2 = client.get("/api/eval/settings")
    data = resp2.json()
    assert data["eval.auto_promote"] is True
    assert data["eval.auto_promote_min_improvement"] == pytest.approx(0.10)


def test_positive_threshold_setting(client_and_db):
    """Verify eval.positive_threshold is a valid setting with default 3."""
    client, _db = client_and_db
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("eval.positive_threshold") == 3
    # Set it
    resp = client.put("/api/eval/settings", json={"eval.positive_threshold": 4})
    assert resp.status_code == 200
    # Read it back
    resp = client.get("/api/eval/settings")
    data = resp.json()
    assert data.get("eval.positive_threshold") == 4


# --- Coverage gap tests: eval settings validation & datasource ---


def test_put_eval_settings_unknown_key_returns_422(client):
    """PUT with a non-allowlisted key returns 422. Covers lines 1804-1805."""
    resp = client.put("/api/eval/settings", json={"eval.nonexistent_key": "value"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("unknown" in str(e).lower() for e in detail)


def test_put_eval_settings_same_cluster_targets_validation(client):
    """same_cluster_targets must be 1-10. Covers lines 1813-1814."""
    resp = client.put("/api/eval/settings", json={"eval.same_cluster_targets": 15})
    assert resp.status_code == 422


def test_put_eval_settings_diff_cluster_targets_validation(client):
    """diff_cluster_targets must be 1-10. Covers lines 1813-1814."""
    resp = client.put("/api/eval/settings", json={"eval.diff_cluster_targets": 0})
    assert resp.status_code == 422


def test_put_eval_settings_f1_threshold_validation(client):
    """f1_threshold must be 0.0-1.0. Covers line 1820."""
    resp = client.put("/api/eval/settings", json={"eval.f1_threshold": 1.5})
    assert resp.status_code == 422


def test_put_eval_settings_error_budget_validation(client):
    """error_budget must be 0.0-1.0. Covers line 1820."""
    resp = client.put("/api/eval/settings", json={"eval.error_budget": -0.1})
    assert resp.status_code == 422


def test_put_eval_settings_positive_threshold_out_of_range(client):
    """positive_threshold must be integer 1-5. Covers line 1839."""
    resp = client.put("/api/eval/settings", json={"eval.positive_threshold": 10})
    assert resp.status_code == 422


def test_put_eval_settings_positive_threshold_not_int(client):
    """positive_threshold must be integer, not float. Covers line 1839."""
    resp = client.put("/api/eval/settings", json={"eval.positive_threshold": 2.5})
    assert resp.status_code == 422


def test_get_eval_settings_masks_token(client_and_db):
    """GET masks eval.data_source_token with ***. Covers line 1769."""
    client, db = client_and_db
    db.set_setting("eval.data_source_token", "secret-bearer-token")
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    masked = "***"
    assert resp.json()["eval.data_source_token"] == masked


def test_post_eval_datasource_prime_http_status_error(client_and_db):
    """POST prime returns 502 on HTTPStatusError. Covers lines 1747-1748."""
    import httpx as _httpx

    client, db = client_and_db
    db.set_setting("eval.data_source_url", "http://127.0.0.1:7685")

    mock_response = MagicMock()
    mock_response.status_code = 500
    exc = _httpx.HTTPStatusError(
        "Server Error",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.post", side_effect=exc):
        resp = client.post("/api/eval/datasource/prime")

    assert resp.status_code == 502
    assert "500" in resp.json()["detail"]


def test_create_eval_schedule_daily(client):
    """Create a daily eval schedule. Covers lines 1860-1904 (daily path)."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A", "B"],
            "per_cluster": 4,
            "run_mode": "batch",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_create_eval_schedule_weekly(client):
    """Create a weekly eval schedule. Covers line 1882."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 4,
            "run_mode": "batch",
            "recurrence": "weekly",
        },
    )
    assert resp.status_code == 200


def test_create_eval_schedule_invalid_recurrence(client):
    """Invalid recurrence returns 400. Covers line 1884."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 4,
            "run_mode": "batch",
            "recurrence": "monthly",
        },
    )
    assert resp.status_code == 400
    assert "recurrence" in resp.json()["detail"].lower()


def test_create_eval_schedule_invalid_variants(client):
    """Non-alphanumeric variants return 400. Covers lines 1868-1871."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A; DROP TABLE"],
            "per_cluster": 4,
            "run_mode": "batch",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 400


def test_create_eval_schedule_invalid_per_cluster(client):
    """Out-of-range per_cluster returns 400. Covers lines 1872-1873."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 99,
            "run_mode": "batch",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 400


def test_create_eval_schedule_invalid_run_mode(client):
    """Invalid run_mode returns 400. Covers lines 1874-1877."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 4,
            "run_mode": "invalid",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 400


def test_put_eval_settings_returns_all_validation_errors(client):
    """PUT with multiple invalid fields must return ALL errors in a single 422 response.

    Regression: provider errors (HTTP 400) and numeric-range errors (HTTP 422)
    were raised in separate conditional blocks.  Submitting both an invalid
    provider AND an out-of-range numeric field returned only the first error
    class encountered, requiring multiple round-trips to discover all problems.
    The fix merges both error classes into a single HTTP 422 response.
    """
    resp = client.put(
        "/api/eval/settings",
        json={
            "eval.per_cluster": 99,  # out-of-range → numeric validation error
            "eval.generator_provider": "gemini",  # unknown provider → provider error
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # detail must be a list containing errors for BOTH invalid fields
    assert isinstance(detail, list), f"Expected list, got {type(detail)}: {detail}"
    assert len(detail) >= 2, f"Expected ≥2 errors, got {len(detail)}: {detail}"
    error_text = " ".join(str(e) for e in detail)
    assert "per_cluster" in error_text, f"per_cluster error missing: {detail}"
    assert "gemini" in error_text or "provider" in error_text.lower(), f"provider error missing: {detail}"


def test_create_eval_schedule_duplicate_returns_409(client):
    """Creating duplicate schedule returns 409. Covers lines 1899-1903."""
    body = {
        "variants": ["A"],
        "per_cluster": 4,
        "run_mode": "batch",
        "recurrence": "daily",
    }
    resp1 = client.post("/api/eval/schedule", json=body)
    assert resp1.status_code == 200

    resp2 = client.post("/api/eval/schedule", json=body)
    assert resp2.status_code == 409


# --- Task 5: Provider settings and API key masking ---


def test_provider_settings_exist_after_init(client):
    """Provider settings should be seeded with defaults after initialization."""
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("eval.generator_provider") == "ollama"
    assert data.get("eval.judge_provider") == "ollama"
    assert data.get("eval.optimizer_provider") == "claude"
    assert data.get("eval.oracle_provider") == "claude"
    assert data.get("eval.oracle_enabled") == "false"
    assert data.get("eval.max_cost_per_run_usd") == "1.00"


def test_api_keys_masked_in_get(client_and_db):
    """API keys should be masked (first 6 chars + ***) in GET responses."""
    client, db = client_and_db
    db.set_setting("eval.claude_api_key", "sk-ant-api03-realkey123456")
    resp = client.get("/api/eval/settings")
    data = resp.json()
    assert data.get("eval.claude_api_key") != "sk-ant-api03-realkey123456"
    assert "***" in data.get("eval.claude_api_key", "")
    assert data.get("eval.claude_api_key", "").startswith("sk-ant")


def test_empty_api_key_not_masked(client):
    """Empty API keys should be returned as empty string, not masked."""
    resp = client.get("/api/eval/settings")
    data = resp.json()
    assert data.get("eval.claude_api_key") == ""
    assert data.get("eval.openai_api_key") == ""


def test_set_invalid_provider_rejected(client):
    """PUT with invalid provider value should return 422 (merged into validation errors)."""
    resp = client.put("/api/eval/settings", json={"eval.generator_provider": "gemini"})
    assert resp.status_code == 422
    detail = resp.json().get("detail", "")
    detail_text = " ".join(str(e) for e in detail) if isinstance(detail, list) else str(detail)
    assert "gemini" in detail_text.lower() or "provider" in detail_text.lower()


def test_set_valid_provider_accepted(client):
    """PUT with valid provider should succeed."""
    resp = client.put("/api/eval/settings", json={"eval.generator_provider": "claude"})
    assert resp.status_code == 200


def test_short_api_key_fully_masked(client_and_db):
    """API keys shorter than 6 chars should be fully masked."""
    client, db = client_and_db
    db.set_setting("eval.claude_api_key", "abc")
    resp = client.get("/api/eval/settings")
    data = resp.json()
    assert data.get("eval.claude_api_key") == "***"
    assert "abc" not in data.get("eval.claude_api_key", "")


# --- Task 11: Provider test endpoint ---


# --- Task 4: Ollama model existence validation ---


def test_put_eval_settings_rejects_missing_ollama_model(client):
    """PUT with eval.judge_model set to a nonexistent model should return 422."""
    with patch(
        "ollama_queue.api.eval_settings._installed_ollama_models",
        return_value={"qwen3.5:9b", "qwen3:14b"},
    ):
        resp = client.put("/api/eval/settings", json={"eval.judge_model": "nonexistent:7b"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    detail_text = " ".join(str(e) for e in detail) if isinstance(detail, list) else str(detail)
    assert "not installed" in detail_text.lower()


def test_put_eval_settings_accepts_installed_ollama_model(client):
    """PUT with an installed model should succeed (200)."""
    with patch(
        "ollama_queue.api.eval_settings._installed_ollama_models",
        return_value={"qwen3.5:9b", "qwen3:14b"},
    ):
        resp = client.put("/api/eval/settings", json={"eval.judge_model": "qwen3.5:9b"})
    assert resp.status_code == 200


def test_put_eval_settings_skips_model_check_for_empty_string(client):
    """Empty string model should be accepted (it means 'use default')."""
    with patch(
        "ollama_queue.api.eval_settings._installed_ollama_models",
        return_value={"qwen3.5:9b", "qwen3:14b"},
    ):
        resp = client.put("/api/eval/settings", json={"eval.judge_model": ""})
    assert resp.status_code == 200


# --- Task 11: Provider test endpoint ---


def test_provider_test_ollama_success(client):
    """POST /api/eval/providers/test with ollama should succeed when proxy responds."""
    from unittest.mock import patch

    with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
        mock.return_value = ("hello world", {"prompt_tokens": 5, "completion_tokens": 3}, None)
        resp = client.post(
            "/api/eval/providers/test",
            json={
                "provider": "ollama",
                "model": "qwen2.5:7b",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["response_length"] > 0


def test_provider_test_ollama_failure(client):
    """POST /api/eval/providers/test returns ok=False when proxy returns None."""
    from unittest.mock import patch

    with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
        mock.return_value = (None, {}, None)
        resp = client.post(
            "/api/eval/providers/test",
            json={
                "provider": "ollama",
                "model": "qwen2.5:7b",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False


def test_provider_test_invalid_provider(client):
    """POST /api/eval/providers/test with unknown provider should return 400."""
    resp = client.post(
        "/api/eval/providers/test",
        json={
            "provider": "invalid_provider",
            "model": "test-model",
        },
    )
    assert resp.status_code == 400


def test_provider_test_missing_model(client):
    """POST /api/eval/providers/test without model should return 422."""
    resp = client.post(
        "/api/eval/providers/test",
        json={
            "provider": "ollama",
        },
    )
    assert resp.status_code == 422


# --- Task 4: Backend URL validation ---


def test_valid_backend_url_accepted(client_and_db):
    """PUT with a registered backend URL should be accepted."""
    client, db = client_and_db
    db.add_backend("http://100.114.197.57:11434", weight=1.0)
    resp = client.put(
        "/api/eval/settings",
        json={"eval.generator_backend_url": "http://100.114.197.57:11434"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.generator_backend_url"] == "http://100.114.197.57:11434"


def test_auto_backend_accepted(client):
    """PUT with 'auto' backend URL should always be accepted (no registration required)."""
    resp = client.put(
        "/api/eval/settings",
        json={"eval.generator_backend_url": "auto"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.generator_backend_url"] == "auto"


def test_unknown_backend_rejected_422(client):
    """PUT with an unknown backend URL should return 422 with 'registered' in detail."""
    resp = client.put(
        "/api/eval/settings",
        json={"eval.judge_backend_url": "http://unknown-host:11434"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    detail_text = " ".join(str(e) for e in detail) if isinstance(detail, list) else str(detail)
    assert "not registered" in detail_text.lower() or "registered backends" in detail_text.lower()


def test_backend_url_validation_works_with_bare_key(client):
    """PUT with bare key (no eval. prefix) also validates backend URL."""
    resp = client.put(
        "/api/eval/settings",
        json={"generator_backend_url": "http://unknown-host:11434"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    detail_text = " ".join(str(e) for e in detail) if isinstance(detail, list) else str(detail)
    assert "not registered" in detail_text.lower()
