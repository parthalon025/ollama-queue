#!/usr/bin/env python3
"""Migration: add eval V2 Bayesian fusion columns to eval_results and eval_runs.

Idempotent — safe to run multiple times. Uses timeout=30 to handle concurrent
daemon access without triggering circuit breaker on OperationalError.
"""

import pathlib
import sqlite3
import sys

db_path = pathlib.Path.home() / ".local/share/ollama-queue/queue.db"
if len(sys.argv) > 1:
    db_path = pathlib.Path(sys.argv[1])

MIGRATIONS = [
    ("eval_results", "score_paired_winner", "TEXT"),
    ("eval_results", "score_mechanism_match", "INTEGER"),
    ("eval_results", "score_embedding_sim", "REAL"),
    ("eval_results", "score_posterior", "REAL"),
    ("eval_results", "mechanism_trigger", "TEXT"),
    ("eval_results", "mechanism_target", "TEXT"),
    ("eval_results", "mechanism_fix", "TEXT"),
    ("eval_runs", "judge_mode", "TEXT DEFAULT 'rubric'"),
]

conn = sqlite3.connect(str(db_path), timeout=30)
applied = 0
try:
    for table, column, defn in MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {defn}")
            conn.commit()
            print(f"  Added {table}.{column}")
            applied += 1
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  {table}.{column} already exists — skipping")
            elif "locked" in str(e).lower():
                print(f"DB locked — stop the daemon and retry: {e}")
                sys.exit(0)
            else:
                raise
    print(f"\nMigration complete: {applied} columns added to {db_path}")
finally:
    conn.close()
