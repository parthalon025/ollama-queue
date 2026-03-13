"""Unit tests for BackendsMixin — SQLite persistence of dynamically registered backends."""

import pytest

from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


class TestAddAndList:
    def test_add_and_list(self, db):
        db.add_backend("http://host1:11434", weight=1.0)
        backends = db.list_backends()
        assert len(backends) == 1
        assert backends[0]["url"] == "http://host1:11434"
        assert backends[0]["weight"] == 1.0
        assert backends[0]["enabled"] == 1

    def test_add_multiple_ordered_by_added_at(self, db):
        db.add_backend("http://host1:11434")
        db.add_backend("http://host2:11434")
        backends = db.list_backends()
        assert len(backends) == 2
        assert backends[0]["url"] == "http://host1:11434"
        assert backends[1]["url"] == "http://host2:11434"

    def test_add_persisted(self, db):
        db.add_backend("http://host1:11434", weight=3.0)
        row = db.get_backend("http://host1:11434")
        assert row is not None
        assert row["url"] == "http://host1:11434"
        assert row["weight"] == 3.0

    def test_add_replace_existing(self, db):
        db.add_backend("http://host1:11434", weight=1.0)
        db.add_backend("http://host1:11434", weight=5.0)
        backends = db.list_backends()
        assert len(backends) == 1
        assert backends[0]["weight"] == 5.0

    def test_list_empty(self, db):
        assert db.list_backends() == []


class TestRemove:
    def test_remove_returns_true(self, db):
        db.add_backend("http://host1:11434")
        removed = db.remove_backend("http://host1:11434")
        assert removed is True

    def test_remove_deletes_row(self, db):
        db.add_backend("http://host1:11434")
        db.remove_backend("http://host1:11434")
        assert db.list_backends() == []

    def test_remove_returns_false_not_found(self, db):
        removed = db.remove_backend("http://missing:11434")
        assert removed is False

    def test_remove_only_target(self, db):
        db.add_backend("http://host1:11434")
        db.add_backend("http://host2:11434")
        db.remove_backend("http://host1:11434")
        backends = db.list_backends()
        assert len(backends) == 1
        assert backends[0]["url"] == "http://host2:11434"


class TestUpdateWeight:
    def test_update_weight_success(self, db):
        db.add_backend("http://host1:11434", weight=1.0)
        updated = db.update_backend_weight("http://host1:11434", 7.5)
        assert updated is True

    def test_update_weight_persisted(self, db):
        db.add_backend("http://host1:11434", weight=1.0)
        db.update_backend_weight("http://host1:11434", 7.5)
        row = db.get_backend("http://host1:11434")
        assert row["weight"] == 7.5

    def test_update_weight_not_found(self, db):
        updated = db.update_backend_weight("http://missing:11434", 2.0)
        assert updated is False


class TestGetBackend:
    def test_get_existing(self, db):
        db.add_backend("http://host1:11434", weight=2.0)
        row = db.get_backend("http://host1:11434")
        assert row is not None
        assert row["url"] == "http://host1:11434"
        assert row["weight"] == 2.0

    def test_get_not_found(self, db):
        row = db.get_backend("http://missing:11434")
        assert row is None

    def test_get_after_remove(self, db):
        db.add_backend("http://host1:11434")
        db.remove_backend("http://host1:11434")
        assert db.get_backend("http://host1:11434") is None
