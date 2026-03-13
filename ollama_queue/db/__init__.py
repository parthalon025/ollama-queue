"""SQLite database layer for ollama-queue.

Plain English: The queue's filing cabinet. Every job, setting, health reading,
and schedule lives in a single SQLite file (~/.local/share/ollama-queue/queue.db).
All other modules read and write through this one — nothing talks to disk directly
except here.

Decision it drives: What data persists across restarts, and how long is it kept?

Uses mixin pattern to split implementation across domain files while preserving
a single Database class API.
"""

import logging
import sqlite3
import threading
import time as _time

from ollama_queue.db.backends import BackendsMixin
from ollama_queue.db.dlq import DLQMixin
from ollama_queue.db.eval import EvalMixin
from ollama_queue.db.health import HealthMixin
from ollama_queue.db.jobs import JobsMixin
from ollama_queue.db.schedule import ScheduleMixin
from ollama_queue.db.schema import DEFAULTS, EVAL_SETTINGS_DEFAULTS, SchemaMixin  # noqa: F401
from ollama_queue.db.settings import SettingsMixin

_log = logging.getLogger(__name__)


class Database(
    SchemaMixin,
    JobsMixin,
    ScheduleMixin,
    SettingsMixin,
    HealthMixin,
    DLQMixin,
    EvalMixin,
    BackendsMixin,
):
    """Synchronous SQLite database for the ollama-queue daemon.

    All mixins use self._conn (sqlite3.Connection) and self._lock (threading.RLock).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                # Performance hardening
                self._conn.execute("PRAGMA synchronous = NORMAL")
                self._conn.execute("PRAGMA temp_store = MEMORY")
                self._conn.execute("PRAGMA mmap_size = 536870912")  # 512MB
                self._conn.execute("PRAGMA cache_size = -64000")  # 64MB page cache
                self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
                # busy_timeout protects against cross-process contention (e.g., sqlite3 CLI,
                # migration scripts); same-process thread safety is handled by self._lock.
                self._conn.execute("PRAGMA busy_timeout = 5000")
        return self._conn

    def _add_column_if_missing(self, conn: sqlite3.Connection, table: str, col: str, defn: str) -> None:
        """ALTER TABLE ... ADD COLUMN, ignoring duplicate-column errors. Caller owns the commit."""
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                _log.debug("%s.%s already exists — skipping migration", table, col)
            else:
                raise

    def _retry_on_busy(self, fn, max_retries=2, backoff=0.1):
        """Retry a DB write on SQLITE_BUSY (WAL checkpoint contention).

        After 1000 WAL pages SQLite forces a checkpoint.  If 10+ FastAPI reader
        threads hold transactions during the checkpoint, the daemon's write blocks
        for busy_timeout=5000ms and then fails with SQLITE_BUSY.  This retries
        with exponential backoff so transient checkpoint contention self-heals.

        Must be called INSIDE self._lock — retries the DB operation, not the lock
        acquisition.
        """
        for attempt in range(max_retries + 1):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries:
                    _log.warning(
                        "SQLITE_BUSY on attempt %d/%d — retrying after %.1fs",
                        attempt + 1,
                        max_retries,
                        backoff * (2**attempt),
                    )
                    _time.sleep(backoff * (2**attempt))
                else:
                    raise

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
