---
title: "internal-exception-bypasses-failure-routing"
severity: blocker
languages: [python]
scope: [domain:ollama, language:python]
category: silent-failures
fix: "Place failure routing in a finally block keyed on job state, so it runs regardless of which exception handler fires."
---

## Observation
In ollama-queue daemon.py, the outer `except Exception:` block in `_run_job` called `complete_job` (marks job failed) but never called `dlq.handle_failure`. Jobs configured with `max_retries > 0` silently ended up stuck at `status='failed'` with no DLQ entry, no retry, and no recovery path.

## Insight
When a function has multiple exception handlers at different nesting levels, the outer handler handles exceptions from ALL inner code including the inner exception handler itself. Code paths in the inner handler (DLQ routing, retry) are silently skipped when the outer handler catches an exception first.

## Lesson
Every failure-routing call must be replicated at EVERY exception handler level. Or: use a `finally` block that checks job state and routes appropriately regardless of how the exception was raised. Positive alternative: place failure routing in a `finally` block keyed on job state, so it runs regardless of which exception handler fires.
