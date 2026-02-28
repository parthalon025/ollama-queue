---
title: "toctou-read-then-lock-pattern"
severity: blocker
languages: [python]
scope: [domain:ollama, language:python, framework:sqlite]
category: integration-boundaries
fix: "Read, decide, write — all three inside the same lock scope. Code pattern: with self._lock: data = self._read_internal(id); if not data: return None; self._write_internal(data)"
---

## Observation
In ollama-queue db.py, both `move_to_dlq` and `update_recurring_next_run` read data outside the lock (`job = self.get_job(job_id)`), then entered `with self._lock:` to write using the read data. Between read and write, another thread could have modified the job, making the write based on stale data.

## Insight
It is tempting to read data before acquiring a lock (to keep lock scope small) and then use that data inside the lock. But if the decision or computation depends on the read data remaining unchanged, the read must also be inside the lock.

## Lesson
Read, decide, write — all three inside the same lock scope. If the lock must be short, re-validate the read inside the lock before writing. Positive alternative: code pattern to follow: `with self._lock: data = self._read_internal(id); if not data: return None; self._write_internal(data)` — all phases locked.
