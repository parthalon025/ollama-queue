"""Eval table CRUD for ollama-queue.

Plain English: Placeholder for eval-specific database operations. Currently,
eval CRUD lives in eval/engine.py which accesses the DB connection directly.
This mixin exists so the Database MRO includes it and future eval DB methods
have a clear home.
"""

import logging
import time

_log = logging.getLogger(__name__)


class EvalMixin:
    """Eval pipeline database operations."""

    def get_eval_cache(self, principle_hash: str, target_hash: str, judge_model: str, judge_mode: str):
        """Return (scores_json, reasoning) for a cached judge result, or (None, None) on miss.

        Keyed on (principle_hash, target_hash, judge_model, judge_mode) — the full
        deterministic identity of a judge call so rubric and binary results are stored
        separately and different models never collide.
        """
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT scores_json, reasoning FROM eval_cache "
                "WHERE principle_hash = ? AND target_hash = ? AND judge_model = ? AND judge_mode = ?",
                (principle_hash, target_hash, judge_model, judge_mode),
            ).fetchone()
        if row is None:
            return None, None
        return row["scores_json"], row["reasoning"]

    def store_eval_cache(
        self,
        principle_hash: str,
        target_hash: str,
        judge_model: str,
        judge_mode: str,
        scores_json: str,
        reasoning: str,
    ) -> None:
        """Upsert a judge result into eval_cache.

        Uses INSERT OR REPLACE so re-runs with the same inputs overwrite stale
        cached results (e.g. after a model update) rather than raising a
        UNIQUE constraint error.
        """
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO eval_cache "
                "(principle_hash, target_hash, judge_model, judge_mode, scores_json, reasoning, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    principle_hash,
                    target_hash,
                    judge_model,
                    judge_mode,
                    scores_json,
                    reasoning,
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ),
            )
            conn.commit()
