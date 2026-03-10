"""Shared fixtures for ollama-queue tests."""

import pytest

from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    """Create a fresh Database instance for each test."""
    db_path = tmp_path / "test.db"
    d = Database(str(db_path))
    d.initialize()
    return d


@pytest.fixture(autouse=True)
def reset_burst_detector_singleton():
    """Reset the module-level BurstDetector singleton before each test.

    The singleton in burst.py accumulates EWMA state across process lifetime.
    Without this fixture, submit calls in one test contaminate regime() results
    in later tests (state bleeds once sample count exceeds 10).
    """
    from ollama_queue.sensing.burst import _default_detector

    _default_detector._ewma = None
    _default_detector._baseline_samples.clear()
    _default_detector._last_ts = None
    yield
