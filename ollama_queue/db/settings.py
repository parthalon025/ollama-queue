"""Settings CRUD for ollama-queue.

Plain English: Read and write daemon configuration values. Every tuning knob
(poll interval, thresholds, retention periods) lives in the settings table and
is accessed through these three methods.
"""

import json
import time


class SettingsMixin:
    """Settings get/set operations."""

    def get_setting(self, key):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            result = json.loads(row["value"])
            # Defensively convert string "true"/"false" to Python bool so
            # callers using truthiness checks (if db.get_setting(...):) behave
            # correctly even when a string was stored instead of a JSON boolean.
            if isinstance(result, str) and result.lower() in ("true", "false"):
                return result.lower() == "true"
            return result

    def set_setting(self, key, value):
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            conn.commit()

    def get_all_settings(self):
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: json.loads(row["value"]) for row in rows}
