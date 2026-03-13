"""Tests for ScheduleMixin — recurring job CRUD and cron validation."""

import pytest

from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


class TestCronValidation:
    def test_valid_cron_accepted(self, db):
        """A valid cron expression is accepted without error."""
        rj_id = db.add_recurring_job("valid", "echo hi", cron_expression="0 7 * * *")
        rj = db.get_recurring_job(rj_id)
        assert rj["cron_expression"] == "0 7 * * *"

    def test_invalid_cron_raises_valueerror(self, db):
        """An invalid cron expression raises ValueError at submission time (#25)."""
        with pytest.raises(ValueError, match="Invalid cron expression"):
            db.add_recurring_job("bad", "echo hi", cron_expression="not-a-cron")

    def test_invalid_cron_bad_field_raises_valueerror(self, db):
        """A cron expression with out-of-range fields raises ValueError."""
        with pytest.raises(ValueError, match="Invalid cron expression"):
            db.add_recurring_job("bad2", "echo hi", cron_expression="99 99 * * *")

    def test_interval_job_skips_cron_validation(self, db):
        """An interval job with no cron expression does not trigger validation."""
        rj_id = db.add_recurring_job("interval", "echo hi", interval_seconds=3600)
        rj = db.get_recurring_job(rj_id)
        assert rj["interval_seconds"] == 3600
        assert rj["cron_expression"] is None


class TestCronValidationAPI:
    def test_api_returns_400_on_invalid_cron(self, db):
        """POST /api/schedule returns 400 for invalid cron expression (#25)."""
        from fastapi.testclient import TestClient

        from ollama_queue.app import create_app

        app = create_app(db)
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={
                "name": "bad-cron",
                "command": "echo hi",
                "cron_expression": "not-a-cron",
            },
        )
        assert resp.status_code == 400
        assert "Invalid cron expression" in resp.json()["detail"]

    def test_api_accepts_valid_cron(self, db):
        """POST /api/schedule succeeds with a valid cron expression."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from ollama_queue.app import create_app

        app = create_app(db)
        client = TestClient(app)
        # Mock Scheduler.rebalance to avoid side effects
        with patch("ollama_queue.scheduling.scheduler.Scheduler.rebalance", return_value=[]):
            resp = client.post(
                "/api/schedule",
                json={
                    "name": "good-cron",
                    "command": "echo hi",
                    "cron_expression": "0 7 * * *",
                },
            )
        assert resp.status_code == 200
