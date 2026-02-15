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
