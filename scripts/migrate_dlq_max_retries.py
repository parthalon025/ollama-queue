#!/usr/bin/env python3
"""Migration: add max_retries column to dlq table."""

import pathlib
import sqlite3
import sys

db_path = pathlib.Path.home() / ".local/share/ollama-queue/queue.db"
if len(sys.argv) > 1:
    db_path = pathlib.Path(sys.argv[1])

conn = sqlite3.connect(db_path, timeout=30)
try:
    conn.execute("ALTER TABLE dlq ADD COLUMN max_retries INTEGER DEFAULT 0")
    conn.commit()
    print(f"Migration applied to {db_path}")
except sqlite3.OperationalError as e:
    if "duplicate column" in str(e):
        print("Already migrated.")
    elif "locked" in str(e).lower():
        print(f"DB locked — stop the daemon and retry: {e}")
        sys.exit(0)
    else:
        raise
finally:
    conn.close()
