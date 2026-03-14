"""Tests for ScheduleMixin structural invariants."""


def test_add_recurring_job_assert_before_commit():
    """assert cur.lastrowid must be inside the lock, before conn.commit()."""
    import inspect

    from ollama_queue.db.schedule import ScheduleMixin

    src = inspect.getsource(ScheduleMixin.add_recurring_job)
    lines = src.splitlines()
    commit_idx = next(i for i, line in enumerate(lines) if "conn.commit()" in line)
    assert_idx = next((i for i, line in enumerate(lines) if "assert cur.lastrowid" in line), None)
    assert assert_idx is not None, "assert cur.lastrowid must exist in add_recurring_job"
    assert assert_idx < commit_idx, "assert cur.lastrowid must appear before conn.commit() — currently it's after"
