import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.api import create_app
from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def client(db):
    return TestClient(create_app(db))


def _seed_consumer(db, **overrides):
    defaults = {
        "name": "aria.service",
        "type": "systemd",
        "platform": "linux",
        "source_label": "aria",
        "detected_at": int(time.time()),
    }
    defaults.update(overrides)
    return db.upsert_consumer(defaults)


# ── List and scan ────────────────────────────────────────────────────────────


def test_list_consumers_empty(client):
    resp = client.get("/api/consumers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_consumers_returns_rows(client, db):
    _seed_consumer(db)
    resp = client.get("/api/consumers")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "aria.service"


def test_scan_triggers_and_returns_consumers(client):
    with patch(
        "ollama_queue.api.run_scan",
        return_value=[
            {
                "name": "aria.service",
                "type": "systemd",
                "platform": "linux",
                "source_label": "aria",
                "is_managed_job": False,
                "streaming_confirmed": False,
                "streaming_suspect": False,
            }
        ],
    ):
        resp = client.post("/api/consumers/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "aria.service"


# ── Include guards ───────────────────────────────────────────────────────────


def test_include_managed_job_returns_409(client, db):
    cid = _seed_consumer(db, is_managed_job=1)
    resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 409
    assert "deadlock" in resp.json()["detail"].lower()


def test_include_streaming_confirmed_without_override_returns_422(client, db):
    cid = _seed_consumer(db, streaming_confirmed=1)
    resp = client.post(
        f"/api/consumers/{cid}/include",
        json={"restart_policy": "deferred", "force_streaming_override": False},
    )
    assert resp.status_code == 422
    assert "stream" in resp.json()["detail"].lower()


def test_include_streaming_confirmed_with_override_proceeds(client, db, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, streaming_confirmed=1, patch_path=str(env), type="env_file")
    with patch(
        "ollama_queue.api.patch_consumer",
        return_value={"patch_applied": True, "status": "patched", "patch_type": "env_file"},
    ):
        resp = client.post(
            f"/api/consumers/{cid}/include",
            json={"restart_policy": "deferred", "force_streaming_override": True},
        )
    assert resp.status_code == 200


def test_include_system_path_without_confirm_returns_422(client, db):
    cid = _seed_consumer(db, patch_path="/etc/systemd/system/aria.service")
    resp = client.post(
        f"/api/consumers/{cid}/include",
        json={"restart_policy": "deferred", "system_confirm": False},
    )
    assert resp.status_code == 422
    assert "system" in resp.json()["detail"].lower()


def test_include_deferred_sets_pending_restart(client, db, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, patch_path=str(env), type="env_file")
    with patch(
        "ollama_queue.api.patch_consumer",
        return_value={"patch_applied": True, "status": "pending_restart", "patch_type": "env_file"},
    ):
        resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "pending_restart"


# ── ignore / revert / health ─────────────────────────────────────────────────


def test_ignore_sets_status(client, db):
    cid = _seed_consumer(db)
    resp = client.post(f"/api/consumers/{cid}/ignore")
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "ignored"


def test_revert_calls_revert_and_resets_status(client, db):
    cid = _seed_consumer(db, status="patched", patch_applied=1)
    with patch("ollama_queue.api.revert_consumer"):
        resp = client.post(f"/api/consumers/{cid}/revert")
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "discovered"


def test_health_endpoint_returns_status(client, db):
    cid = _seed_consumer(db, status="patched")
    with patch(
        "ollama_queue.api.check_health",
        return_value={
            "old_port_clear": True,
            "new_port_active": True,
            "request_seen": False,
            "status": "confirmed",
        },
    ):
        resp = client.get(f"/api/consumers/{cid}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


# --- Coverage gap tests: consumer/intercept error paths ---


def test_include_consumer_not_found(client):
    """Include on non-existent consumer returns 404. Covers line 2612."""
    resp = client.post(
        "/api/consumers/99999/include",
        json={"restart_policy": "deferred"},
    )
    assert resp.status_code == 404


def test_include_windows_consumer_returns_422(client, db):
    """Include on Windows consumer returns 422. Covers line 2621."""
    cid = _seed_consumer(db, platform="windows")
    resp = client.post(
        f"/api/consumers/{cid}/include",
        json={"restart_policy": "deferred"},
    )
    assert resp.status_code == 422
    assert "windows" in resp.json()["detail"].lower()


def test_include_patch_exception_returns_500(client, db):
    """Patch failure returns 500. Covers lines 2641-2642."""
    cid = _seed_consumer(db, patch_path="/tmp/test.env", type="env_file")  # noqa: S108
    with patch(
        "ollama_queue.api.patch_consumer",
        side_effect=RuntimeError("patch write error"),
    ):
        resp = client.post(
            f"/api/consumers/{cid}/include",
            json={"restart_policy": "deferred"},
        )
    assert resp.status_code == 500
    assert "patch write error" in resp.json()["detail"]


def test_include_patched_triggers_health_check(client, db, tmp_path):
    """When patch returns status=patched, background health check runs. Covers lines 2658-2659."""
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, patch_path=str(env), type="env_file")

    health_called = []

    def mock_check_health(consumer, db_ref):
        health_called.append(True)
        return {"status": "confirmed"}

    with (
        patch(
            "ollama_queue.api.patch_consumer",
            return_value={"patch_applied": True, "status": "patched", "patch_type": "env_file"},
        ),
        patch("ollama_queue.api.check_health", side_effect=mock_check_health),
    ):
        resp = client.post(
            f"/api/consumers/{cid}/include",
            json={"restart_policy": "immediate"},
        )
    assert resp.status_code == 200
    # Allow background thread to run
    import time

    time.sleep(0.2)
    assert len(health_called) == 1


def test_include_health_check_exception_is_logged(client, db, tmp_path):
    """Health check exception in background thread is caught. Covers line 2659."""
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, patch_path=str(env), type="env_file")

    with (
        patch(
            "ollama_queue.api.patch_consumer",
            return_value={"patch_applied": True, "status": "patched", "patch_type": "env_file"},
        ),
        patch("ollama_queue.api.check_health", side_effect=RuntimeError("health check boom")),
    ):
        resp = client.post(
            f"/api/consumers/{cid}/include",
            json={"restart_policy": "immediate"},
        )
    assert resp.status_code == 200
    # Background exception is caught — no crash
    import time

    time.sleep(0.2)


def test_ignore_consumer_not_found(client):
    """Ignore on non-existent consumer returns 404. Covers line 2669."""
    resp = client.post("/api/consumers/99999/ignore")
    assert resp.status_code == 404


def test_revert_consumer_not_found(client):
    """Revert on non-existent consumer returns 404. Covers line 2677."""
    resp = client.post("/api/consumers/99999/revert")
    assert resp.status_code == 404


def test_revert_consumer_exception_returns_500(client, db):
    """Revert failure returns 500. Covers lines 2680-2682."""
    cid = _seed_consumer(db, status="patched", patch_applied=1)
    with patch(
        "ollama_queue.api.revert_consumer",
        side_effect=RuntimeError("file revert failed"),
    ):
        resp = client.post(f"/api/consumers/{cid}/revert")
    assert resp.status_code == 500
    assert "file revert failed" in resp.json()["detail"]


def test_health_consumer_not_found(client):
    """Health on non-existent consumer returns 404. Covers line 2690."""
    resp = client.get("/api/consumers/99999/health")
    assert resp.status_code == 404


def test_intercept_enable_non_linux_returns_422(client):
    """Intercept enable on non-Linux returns 422. Covers lines 2697-2700."""
    # The endpoint does `import platform as _plat` locally, so mock the module itself
    import platform as _plat_mod

    with patch.object(_plat_mod, "system", return_value="Darwin"):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 422
    assert "linux" in resp.json()["detail"].lower()


def test_intercept_enable_no_consumers_returns_422(client):
    """Intercept enable with no included consumers returns 422. Covers lines 2702-2706."""
    import platform as _plat_mod

    with patch.object(_plat_mod, "system", return_value="Linux"):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 422
    assert "consumer" in resp.json()["detail"].lower()


def test_intercept_enable_success(client, db):
    """Intercept enable with included consumer succeeds. Covers lines 2707-2713."""
    import platform as _plat_mod

    _seed_consumer(db, status="patched")
    with (
        patch.object(_plat_mod, "system", return_value="Linux"),
        patch(
            "ollama_queue.api.enable_intercept",
            return_value={"enabled": True},
        ),
        patch("os.getuid", return_value=1000),
    ):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_intercept_enable_iptables_fail_returns_422(client, db):
    """Intercept enable with iptables failure returns 422. Covers lines 2709-2710."""
    import platform as _plat_mod

    _seed_consumer(db, status="included")
    with (
        patch.object(_plat_mod, "system", return_value="Linux"),
        patch(
            "ollama_queue.api.enable_intercept",
            return_value={"enabled": False, "error": "iptables not found"},
        ),
        patch("os.getuid", return_value=1000),
    ):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 422
    assert "iptables" in resp.json()["detail"].lower()


def test_intercept_disable_success(client, db):
    """Intercept disable succeeds. Covers lines 2717-2722."""
    db.set_setting("intercept_mode_uid", "1000")
    with patch(
        "ollama_queue.api.disable_intercept",
        return_value={"enabled": False},
    ):
        resp = client.post("/api/consumers/intercept/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_intercept_disable_error_returns_500(client, db):
    """Intercept disable with error returns 500. Covers lines 2719-2720."""
    db.set_setting("intercept_mode_uid", "1000")
    with patch(
        "ollama_queue.api.disable_intercept",
        return_value={"enabled": True, "error": "iptables -D failed"},
    ):
        resp = client.post("/api/consumers/intercept/disable")
    assert resp.status_code == 500


def test_intercept_status_endpoint(client, db):
    """Intercept status returns current state. Covers lines 2726-2727."""
    db.set_setting("intercept_mode_uid", "1000")
    with patch(
        "ollama_queue.api.get_intercept_status",
        return_value={"enabled": False, "rules": []},
    ):
        resp = client.get("/api/consumers/intercept/status")
    assert resp.status_code == 200
    assert "enabled" in resp.json()
