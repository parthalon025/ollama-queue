"""Tests for the job_metrics table and derived queries."""

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


class TestJobMetrics:
    def test_store_job_metrics(self, db):
        """Store and retrieve metrics for a completed job."""
        job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
        metrics = {
            "model": "qwen3.5:9b",
            "command": "echo hi",
            "resource_profile": "ollama",
            "load_duration_ns": 1_500_000_000,
            "prompt_eval_count": 20,
            "prompt_eval_duration_ns": 800_000_000,
            "eval_count": 300,
            "eval_duration_ns": 5_000_000_000,
            "total_duration_ns": 7_000_000_000,
            "model_size_gb": 5.2,
        }
        db.store_job_metrics(job_id, metrics)
        result = db.get_job_metrics(job_id)
        assert result is not None
        assert result["job_id"] == job_id
        assert result["model"] == "qwen3.5:9b"
        assert result["eval_count"] == 300
        assert result["eval_duration_ns"] == 5_000_000_000
        assert result["model_size_gb"] == pytest.approx(5.2)

    def test_get_tok_per_min(self, db):
        """Derive tok/min from stored eval_count and eval_duration_ns.

        300 tokens / 5s = 60 tok/s = 3600 tok/min.
        """
        job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
        db.store_job_metrics(
            job_id,
            {
                "model": "qwen3.5:9b",
                "eval_count": 300,
                "eval_duration_ns": 5_000_000_000,
            },
        )
        rates = db.get_tok_per_min("qwen3.5:9b")
        assert len(rates) == 1
        assert rates[0] == pytest.approx(3600.0)

    def test_get_job_durations(self, db):
        """Historical wall-clock durations from jobs table (completed_at - started_at)."""
        job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
        db.start_job(job_id)
        # Manually set started_at and completed_at for deterministic test
        now = time.time()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "UPDATE jobs SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
                (now - 120.0, now, job_id),
            )
            conn.commit()
        durations = db.get_job_durations("qwen3.5:9b")
        assert len(durations) == 1
        assert durations[0] == pytest.approx(120.0, abs=0.1)

    def test_get_load_durations(self, db):
        """Warmup times from load_duration_ns. 1.8 billion ns = 1.8s."""
        job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
        db.store_job_metrics(
            job_id,
            {
                "model": "qwen3.5:9b",
                "load_duration_ns": 1_800_000_000,
            },
        )
        loads = db.get_load_durations("qwen3.5:9b")
        assert len(loads) == 1
        assert loads[0] == pytest.approx(1.8)

    def test_get_model_stats(self, db):
        """Aggregate stats per model: run_count, avg_tok_per_min, avg_warmup_s, model_size_gb."""
        for _i in range(3):
            job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
            db.store_job_metrics(
                job_id,
                {
                    "model": "qwen3.5:9b",
                    "eval_count": 300,
                    "eval_duration_ns": 5_000_000_000,
                    "load_duration_ns": 2_000_000_000,
                    "model_size_gb": 5.2,
                },
            )
        stats = db.get_model_stats()
        assert "qwen3.5:9b" in stats
        s = stats["qwen3.5:9b"]
        assert s["run_count"] == 3
        assert s["avg_tok_per_min"] == pytest.approx(3600.0)
        assert s["avg_warmup_s"] == pytest.approx(2.0)
        assert s["model_size_gb"] == pytest.approx(5.2)

    def test_metrics_missing_fields_stored_as_null(self, db):
        """Non-Ollama jobs store partial metrics (missing fields are NULL)."""
        job_id = db.submit_job("echo hi", "qwen3.5:9b", 5, 60, "test")
        db.store_job_metrics(
            job_id,
            {
                "model": "qwen3.5:9b",
                "command": "echo hi",
            },
        )
        result = db.get_job_metrics(job_id)
        assert result is not None
        assert result["model"] == "qwen3.5:9b"
        assert result["eval_count"] is None
        assert result["eval_duration_ns"] is None
        assert result["load_duration_ns"] is None
        assert result["model_size_gb"] is None
