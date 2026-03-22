"""Tests for DB schema migration atomicity."""


def test_add_column_if_missing_does_not_commit_internally():
    """_add_column_if_missing must not commit — caller owns the transaction."""
    import inspect

    from ollama_queue.db import Database

    src = inspect.getsource(Database._add_column_if_missing)
    assert "conn.commit()" not in src, (
        "_add_column_if_missing must not commit internally — commit is the caller's responsibility"
    )


def test_run_migrations_does_not_commit_internally():
    """_run_migrations must not commit — initialize() owns the transaction."""
    import inspect

    from ollama_queue.db.schema import SchemaMixin

    src = inspect.getsource(SchemaMixin._run_migrations)
    assert "conn.commit()" not in src, (
        "_run_migrations must not commit internally — initialize() is the sole commit owner"
    )
