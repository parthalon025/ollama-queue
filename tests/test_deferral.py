import os
import tempfile
import time

import pytest

from ollama_queue.db import Database


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    d = Database(path)
    d.initialize()
    yield d
    os.unlink(path)


def test_defer_job(db):
    """Defer a pending job."""
    job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
    deferral_id = db.defer_job(job_id, reason="resource", context="needs 10GB, 4GB free")
    assert deferral_id is not None

    # Job status should be 'deferred'
    job = db.get_job(job_id)
    assert job["status"] == "deferred"

    # Deferral record exists
    d = db.get_deferral(deferral_id)
    assert d["job_id"] == job_id
    assert d["reason"] == "resource"
    assert d["deferred_at"] is not None
    assert d["resumed_at"] is None


def test_list_deferred(db):
    j1 = db.submit_job("cmd1", "m", 5, 60, "test")
    j2 = db.submit_job("cmd2", "m", 5, 60, "test")
    db.defer_job(j1, reason="burst")
    db.defer_job(j2, reason="thermal")

    deferred = db.list_deferred()
    assert len(deferred) == 2


def test_list_deferred_unscheduled_only(db):
    j1 = db.submit_job("cmd1", "m", 5, 60, "test")
    j2 = db.submit_job("cmd2", "m", 5, 60, "test")
    d1 = db.defer_job(j1, reason="burst")
    d2 = db.defer_job(j2, reason="thermal")

    # Schedule one
    db.update_deferral_schedule(d1, scheduled_for=time.time() + 3600, scoring_snapshot='{"score": 5}')

    unscheduled = db.list_deferred(unscheduled_only=True)
    assert len(unscheduled) == 1
    assert unscheduled[0]["id"] == d2


def test_resume_deferred_job(db):
    job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
    deferral_id = db.defer_job(job_id, reason="thermal")

    db.resume_deferred_job(deferral_id)

    # Job back to pending
    job = db.get_job(job_id)
    assert job["status"] == "pending"

    # Deferral marked resumed
    d = db.get_deferral(deferral_id)
    assert d["resumed_at"] is not None


def test_deferred_job_keeps_same_id(db):
    """Deferred jobs keep their original job ID — no new job created."""
    job_id = db.submit_job("echo hi", "qwen3.5:9b", 8, 60, "test")
    db.defer_job(job_id, reason="resource")

    # Still the same job
    job = db.get_job(job_id)
    assert job["priority"] == 8  # preserves original priority
