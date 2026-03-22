"""Tests for GET /api/jobs/{id}/log — log tail endpoint."""

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def client_and_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_get_job_log_returns_last_n_lines(client_and_db):
    """GET /api/jobs/{id}/log returns last N lines of job stdout_tail."""
    client, db = client_and_db
    job_id = db.submit_job(command="echo test", model=None, priority=5, timeout=60, source="test")
    # Manually set stdout_tail with known content via direct DB update
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE jobs SET stdout_tail = ? WHERE id = ?",
            ("line1\nline2\nline3\nline4\nline5\nline6", job_id),
        )
        conn.commit()

    resp = client.get(f"/api/jobs/{job_id}/log?tail=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "lines" in data
    assert len(data["lines"]) <= 5
    assert "line6" in data["lines"]
    # tail=5 should give last 5 lines
    assert data["lines"] == ["line2", "line3", "line4", "line5", "line6"]


def test_get_job_log_returns_404_for_missing_job(client_and_db):
    """GET /api/jobs/{id}/log returns 404 when job not found."""
    client, _db = client_and_db
    resp = client.get("/api/jobs/999999/log")
    assert resp.status_code == 404


def test_get_job_log_returns_empty_for_no_output(client_and_db):
    """GET /api/jobs/{id}/log returns empty lines when job has no stdout_tail."""
    client, db = client_and_db
    job_id = db.submit_job(command="echo test", model=None, priority=5, timeout=60, source="test")
    resp = client.get(f"/api/jobs/{job_id}/log")
    assert resp.status_code == 200
    data = resp.json()
    assert "lines" in data
    assert data["lines"] == []


def test_get_job_log_default_tail_is_5(client_and_db):
    """GET /api/jobs/{id}/log without tail param defaults to 5 lines."""
    client, db = client_and_db
    job_id = db.submit_job(command="echo test", model=None, priority=5, timeout=60, source="test")
    output = "\n".join(f"line{i}" for i in range(1, 11))  # 10 lines
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET stdout_tail = ? WHERE id = ?", (output, job_id))
        conn.commit()

    resp = client.get(f"/api/jobs/{job_id}/log")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) == 5
    assert data["lines"][-1] == "line10"


def test_get_job_log_tail_max_capped_at_50(client_and_db):
    """GET /api/jobs/{id}/log caps tail at 50 lines even if more requested."""
    client, db = client_and_db
    job_id = db.submit_job(command="echo test", model=None, priority=5, timeout=60, source="test")
    output = "\n".join(f"line{i}" for i in range(1, 101))  # 100 lines
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET stdout_tail = ? WHERE id = ?", (output, job_id))
        conn.commit()

    resp = client.get(f"/api/jobs/{job_id}/log?tail=100")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) <= 50


def test_get_job_log_filters_empty_lines(client_and_db):
    """GET /api/jobs/{id}/log filters out blank lines from output."""
    client, db = client_and_db
    job_id = db.submit_job(command="echo test", model=None, priority=5, timeout=60, source="test")
    output = "line1\n\nline2\n   \nline3\n"
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET stdout_tail = ? WHERE id = ?", (output, job_id))
        conn.commit()

    resp = client.get(f"/api/jobs/{job_id}/log?tail=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lines"] == ["line1", "line2", "line3"]
