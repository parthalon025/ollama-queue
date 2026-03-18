"""ForgeMixin — CRUD for forge_runs, forge_results, forge_embeddings tables."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class ForgeMixin:
    """DB operations for Forge v2 evaluation engine."""

    def create_forge_run(
        self,
        *,
        data_source_url: str,
        variant_id: str,
        judge_model: str,
        oracle_model: str,
        pairs_per_quartile: int = 20,
        label: str | None = None,
        seed: int | None = None,
    ) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """INSERT INTO forge_runs
                   (data_source_url, variant_id, judge_model, oracle_model,
                    pairs_per_quartile, label, seed, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (data_source_url, variant_id, judge_model, oracle_model, pairs_per_quartile, label, seed, time.time()),
            )
            conn.commit()
            return cur.lastrowid

    def get_forge_run(self, run_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM forge_runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_forge_runs(self, *, limit: int = 50) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT * FROM forge_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def update_forge_run(self, run_id: int, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = [*list(kwargs.values()), run_id]
        with self._lock:
            conn = self._connect()
            conn.execute(f"UPDATE forge_runs SET {sets} WHERE id = ?", vals)
            conn.commit()

    def insert_forge_result(
        self,
        *,
        run_id: int,
        source_item_id: str,
        target_item_id: str,
        embedding_similarity: float,
        quartile: str,
        judge_score: int | None = None,
        judge_reasoning: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR IGNORE INTO forge_results
                   (run_id, source_item_id, target_item_id,
                    embedding_similarity, quartile, judge_score,
                    judge_reasoning, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    source_item_id,
                    target_item_id,
                    embedding_similarity,
                    quartile,
                    judge_score,
                    judge_reasoning,
                    time.time(),
                ),
            )
            conn.commit()

    def get_forge_results(self, run_id: int) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM forge_results WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_forge_result_oracle(
        self,
        result_id: int,
        *,
        oracle_score: int,
        oracle_reasoning: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE forge_results
                   SET oracle_score = ?, oracle_reasoning = ?
                   WHERE id = ?""",
                (oracle_score, oracle_reasoning, result_id),
            )
            conn.commit()

    def store_forge_embedding(
        self,
        item_id: str,
        content_hash: str,
        vector: list[float],
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR REPLACE INTO forge_embeddings
                   (item_id, content_hash, vector_json, created_at)
                   VALUES (?, ?, ?, ?)""",
                (item_id, content_hash, json.dumps(vector), time.time()),
            )
            conn.commit()

    def get_forge_embedding(
        self,
        item_id: str,
        content_hash: str,
    ) -> list[float] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT vector_json FROM forge_embeddings
                   WHERE item_id = ? AND content_hash = ?""",
                (item_id, content_hash),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["vector_json"])

    def upsert_forge_archive_cell(
        self,
        *,
        x_bin: int,
        y_bin: int,
        x_value: float,
        y_value: float,
        variant_id: str,
        fitness: float,
        prompt_text: str | None = None,
        metadata_json: str | None = None,
        run_id: int | None = None,
    ) -> None:
        """Insert or replace archive cell — only replaces if fitness is higher."""
        now = time.time()
        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT fitness FROM forge_archive WHERE x_bin = ? AND y_bin = ?",
                (x_bin, y_bin),
            ).fetchone()
            if existing and existing["fitness"] >= fitness:
                return  # existing is better
            conn.execute(
                """INSERT INTO forge_archive
                   (x_bin, y_bin, x_value, y_value, variant_id, fitness,
                    prompt_text, metadata_json, run_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(x_bin, y_bin) DO UPDATE SET
                     x_value=excluded.x_value, y_value=excluded.y_value,
                     variant_id=excluded.variant_id, fitness=excluded.fitness,
                     prompt_text=excluded.prompt_text, metadata_json=excluded.metadata_json,
                     run_id=excluded.run_id, updated_at=excluded.updated_at""",
                (x_bin, y_bin, x_value, y_value, variant_id, fitness, prompt_text, metadata_json, run_id, now, now),
            )
            conn.commit()

    def get_forge_archive_cell(self, x_bin: int, y_bin: int) -> dict | None:
        """Get one archive cell by grid coordinates."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM forge_archive WHERE x_bin = ? AND y_bin = ?",
                (x_bin, y_bin),
            ).fetchone()
            return dict(row) if row else None

    def get_forge_archive_grid(self) -> list[dict]:
        """Get all archive cells ordered by x_bin, y_bin."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT * FROM forge_archive ORDER BY x_bin, y_bin").fetchall()
            return [dict(r) for r in rows]

    def clear_forge_archive(self) -> None:
        """Delete all archive cells."""
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM forge_archive")
            conn.commit()

    def save_forge_thompson_state(self, state: dict) -> None:
        """Persist Thompson state as a single row (replaces previous)."""
        now = time.time()
        state_json = json.dumps(state)
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM forge_thompson_state")
            conn.execute(
                "INSERT INTO forge_thompson_state (state_json, updated_at) VALUES (?, ?)",
                (state_json, now),
            )
            conn.commit()

    def load_forge_thompson_state(self) -> dict | None:
        """Load Thompson state. Returns None if no state saved."""
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT state_json FROM forge_thompson_state ORDER BY id DESC LIMIT 1").fetchone()
            return json.loads(row["state_json"]) if row else None
