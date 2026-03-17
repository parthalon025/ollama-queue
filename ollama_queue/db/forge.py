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
