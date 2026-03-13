"""Tests for DB schema migration atomicity."""


def test_add_column_if_missing_does_not_commit_internally():
    """_add_column_if_missing must not commit — caller owns the transaction."""
    import inspect

    from ollama_queue.db import Database

    src = inspect.getsource(Database._add_column_if_missing)
    assert (
        "conn.commit()" not in src
    ), "_add_column_if_missing must not commit internally — commit is the caller's responsibility"
