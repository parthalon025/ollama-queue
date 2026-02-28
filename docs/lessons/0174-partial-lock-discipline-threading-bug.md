---
title: "partial-lock-discipline-threading-bug"
severity: blocker
languages: [python]
scope: [domain:ollama, language:python, framework:sqlite]
category: integration-boundaries
fix: "Document the locking invariant at the class level: 'All writes acquire self._lock. Methods named _*_locked assume caller holds lock.' Every method that writes to a shared connection object must hold the lock — no exceptions."
---

## Observation
In ollama-queue db.py, approximately 70% of write methods correctly wrapped their bodies in `with self._lock:`, but `set_setting`, `log_health`, and `prune_old_data` did not. These three are called from FastAPI's concurrent thread pool. WAL mode serializes at the SQLite file level but does not make the Python `sqlite3.Connection` object thread-safe. The unlocked methods were data races.

## Insight
Lock discipline was applied to "important" write methods but not to "utility" write methods, creating an uneven pattern that looked safe but wasn't. New contributors seeing most methods without locks would reasonably not add one.

## Lesson
Every method that writes to a shared connection object must hold the lock — no exceptions. The absence of a lock on a write method must be explicitly documented (e.g., "caller holds lock"). Positive alternative: document the locking invariant at the class level: "All writes acquire self._lock. Methods named _*_locked assume caller holds lock." Enforce with a code review checklist item.
