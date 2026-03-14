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

    def add_backend(self, url: str, weight: float = 1.0) -> None:
        """Insert or replace a backend row."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute(
                    "INSERT OR REPLACE INTO backends (url, weight, enabled, added_at) VALUES (?, ?, 1, ?)",
                    (url, weight, time.time()),
                )
                conn.commit()

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

    def list_backends(self) -> list[dict]:
        """Return all enabled backends ordered by added_at (earliest first)."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute("SELECT * FROM backends WHERE enabled = 1 ORDER BY added_at")
                return [dict(r) for r in cur.fetchall()]

    def update_backend_inference_mode(self, url: str, mode: str) -> bool:
        """Set inference_mode for a backend. Returns True if the row existed.

        Plain English: Controls whether this backend may use CPU RAM when the model
        doesn't fit in VRAM ('cpu_shared', the default) or must stay GPU-only ('gpu_only').
        """
        if mode not in ("gpu_only", "cpu_shared"):
            raise ValueError(f"inference_mode must be 'gpu_only' or 'cpu_shared', got {mode!r}")
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute("UPDATE backends SET inference_mode = ? WHERE url = ?", (mode, url))
                conn.commit()
                return cur.rowcount > 0

    def get_backend(self, url: str) -> dict | None:
        """Return a single backend row by URL, or None if not found."""
        with self._lock:
            conn = self._connect()
            with closing(conn.cursor()) as cur:
                cur.execute("SELECT * FROM backends WHERE url = ?", (url,))
                row = cur.fetchone()
                return dict(row) if row else None
