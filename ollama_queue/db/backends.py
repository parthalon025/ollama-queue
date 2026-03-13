"""BackendsMixin — SQLite persistence for dynamically registered Ollama backends.

Plain English: Stores backends registered at runtime (POST /api/backends) so they
survive daemon restarts. Env-var backends (OLLAMA_BACKENDS) are always included via
backend_router.py; this table holds additions made through the API.

Decision it drives: Which Ollama backends are included in multi-GPU routing.
"""

import time
from contextlib import closing


class BackendsMixin:
    """CRUD operations for the backends table."""

    def add_backend(self, url: str, weight: float = 1.0, label: str | None = None) -> dict:
        """Insert or replace a backend. Returns the stored row."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute(
                    "INSERT OR REPLACE INTO backends (url, weight, enabled, added_at, label) VALUES (?, ?, 1, ?, ?)",
                    (url, weight, time.time(), label),
                )
                conn.commit()
                cur.execute("SELECT * FROM backends WHERE url = ?", (url,))
                row = cur.fetchone()
                return dict(row) if row else None

    def remove_backend(self, url: str) -> bool:
        """Delete a backend by URL. Returns True if a row was deleted."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute("DELETE FROM backends WHERE url = ?", (url,))
                conn.commit()
                return cur.rowcount > 0

    def update_backend_weight(self, url: str, weight: float) -> bool:
        """Update the routing weight for a backend. Returns True if the row existed."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute("UPDATE backends SET weight = ? WHERE url = ?", (weight, url))
                conn.commit()
                return cur.rowcount > 0

    def list_backends(self) -> list:
        """Return all enabled backends ordered by added_at (earliest first)."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute("SELECT * FROM backends WHERE enabled = 1 ORDER BY added_at")
                return [dict(r) for r in cur.fetchall()]

    def get_backend(self, url: str) -> dict | None:
        """Return a single backend row by URL, or None if not found."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute("SELECT * FROM backends WHERE url = ?", (url,))
                row = cur.fetchone()
                return dict(row) if row else None
