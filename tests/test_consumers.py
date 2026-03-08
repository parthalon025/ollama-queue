import pytest

from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


def test_consumers_table_created(db):
    conn = db._connect()
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='consumers'").fetchone()
    assert row is not None


def test_consumers_upsert_and_fetch(db):
    import time

    db.upsert_consumer(
        {
            "name": "aria.service",
            "type": "systemd",
            "platform": "linux",
            "source_label": "aria",
            "detected_at": int(time.time()),
        }
    )
    rows = db.list_consumers()
    assert len(rows) == 1
    assert rows[0]["name"] == "aria.service"


def test_consumers_upsert_deduplicates(db):
    import time

    now = int(time.time())
    db.upsert_consumer(
        {"name": "svc", "type": "systemd", "platform": "linux", "source_label": "svc", "detected_at": now}
    )
    db.upsert_consumer(
        {"name": "svc", "type": "systemd", "platform": "linux", "source_label": "svc", "detected_at": now}
    )
    assert len(db.list_consumers()) == 1


def test_consumer_update_status(db):
    import time

    db.upsert_consumer(
        {"name": "svc", "type": "systemd", "platform": "linux", "source_label": "svc", "detected_at": int(time.time())}
    )
    rows = db.list_consumers()
    db.update_consumer(rows[0]["id"], status="included")
    updated = db.get_consumer(rows[0]["id"])
    assert updated["status"] == "included"
