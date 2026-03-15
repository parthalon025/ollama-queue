"""Tests for gen_backend_url and judge_backend_url columns on eval_runs."""

from __future__ import annotations

from ollama_queue.db import Database
from ollama_queue.eval.engine import create_eval_run, get_eval_run


class TestBackendUrlColumnsExist:
    """Verify the new columns are present after initialize()."""

    def test_gen_backend_url_column_exists(self, db):
        """gen_backend_url column should exist in eval_runs after initialize."""
        with db._lock:
            conn = db._connect()
            row = conn.execute("SELECT gen_backend_url FROM eval_runs LIMIT 0").fetchone()
        # No exception means the column exists

    def test_judge_backend_url_column_exists(self, db):
        """judge_backend_url column should exist in eval_runs after initialize."""
        with db._lock:
            conn = db._connect()
            row = conn.execute("SELECT judge_backend_url FROM eval_runs LIMIT 0").fetchone()
        # No exception means the column exists

    def test_columns_default_to_null(self, db):
        """New columns should default to NULL when not provided."""
        run_id = create_eval_run(db, variant_id="A")
        run = get_eval_run(db, run_id)
        assert run["gen_backend_url"] is None
        assert run["judge_backend_url"] is None


class TestBackendUrlColumnsAcceptValues:
    """Verify the columns accept URL strings and other text."""

    def test_accept_url_values(self, db):
        """Columns should accept full URL values."""
        run_id = create_eval_run(
            db,
            variant_id="A",
            gen_backend_url="http://100.0.0.1:11434",
            judge_backend_url="http://127.0.0.1:11434",
        )
        run = get_eval_run(db, run_id)
        assert run["gen_backend_url"] == "http://100.0.0.1:11434"
        assert run["judge_backend_url"] == "http://127.0.0.1:11434"

    def test_accept_auto_string(self, db):
        """Columns should accept 'auto' as a value."""
        run_id = create_eval_run(
            db,
            variant_id="A",
            gen_backend_url="auto",
            judge_backend_url="auto",
        )
        run = get_eval_run(db, run_id)
        assert run["gen_backend_url"] == "auto"
        assert run["judge_backend_url"] == "auto"

    def test_accept_mixed_null_and_value(self, db):
        """One column can be NULL while the other has a value."""
        run_id = create_eval_run(
            db,
            variant_id="A",
            gen_backend_url="http://192.168.1.100:11434",
        )
        run = get_eval_run(db, run_id)
        assert run["gen_backend_url"] == "http://192.168.1.100:11434"
        assert run["judge_backend_url"] is None


class TestBackendUrlMigration:
    """Verify migration adds columns to pre-existing tables."""

    def test_migration_adds_columns(self, tmp_path):
        """Calling initialize() twice should not fail — migration is idempotent."""
        db_path = tmp_path / "migrate_test.db"
        db = Database(str(db_path))
        db.initialize()
        # Second initialize should not raise
        db.initialize()
        # Columns should still be accessible
        run_id = create_eval_run(
            db,
            variant_id="A",
            gen_backend_url="http://example.com:11434",
            judge_backend_url="http://other.com:11434",
        )
        run = get_eval_run(db, run_id)
        assert run["gen_backend_url"] == "http://example.com:11434"
        assert run["judge_backend_url"] == "http://other.com:11434"


class TestCreateEvalRunStoresBackendUrls:
    """Verify create_eval_run persists gen_backend_url and judge_backend_url."""

    def test_stores_both_urls(self, db):
        """create_eval_run with both URLs should persist them to the DB."""
        run_id = create_eval_run(
            db,
            variant_id="A",
            gen_backend_url="http://10.0.0.1:11434",
            judge_backend_url="http://10.0.0.2:11434",
        )
        run = get_eval_run(db, run_id)
        assert run["gen_backend_url"] == "http://10.0.0.1:11434"
        assert run["judge_backend_url"] == "http://10.0.0.2:11434"

    def test_stores_none_by_default(self, db):
        """create_eval_run without URLs should leave them NULL."""
        run_id = create_eval_run(db, variant_id="A")
        run = get_eval_run(db, run_id)
        assert run["gen_backend_url"] is None
        assert run["judge_backend_url"] is None

    def test_existing_judge_backend_untouched(self, db):
        """The existing judge_backend (provider type) column must still work."""
        run_id = create_eval_run(db, variant_id="A")
        # judge_backend is the provider type column, not the URL column
        with db._lock:
            conn = db._connect()
            conn.execute(
                "UPDATE eval_runs SET judge_backend = 'ollama' WHERE id = ?",
                (run_id,),
            )
            conn.commit()
        run = get_eval_run(db, run_id)
        assert run["judge_backend"] == "ollama"
        # The new URL columns should still be NULL
        assert run["gen_backend_url"] is None
        assert run["judge_backend_url"] is None
