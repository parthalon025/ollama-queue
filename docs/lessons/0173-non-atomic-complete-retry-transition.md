---
title: "non-atomic-complete-retry-transition"
severity: important
languages: [python]
scope: [domain:ollama, language:python, framework:sqlite]
category: integration-boundaries
fix: "When a status transition has two steps, design the first write to always be a reversible intermediate state (not a terminal status), or combine into a single atomic operation."
---

## Observation
In ollama-queue daemon.py, `complete_job` (sets `status='failed'`) was called before `dlq.handle_failure` (which may set `status='pending'` for retry). Between the two calls, external readers (API, CLI history) saw the job as permanently failed even though it would be retried.

## Insight
Two-step status transitions — where step 1 writes a "terminal" status that step 2 overrides — are externally visible during the gap. This is a TOCTOU at the application level, not the DB level.

## Lesson
Either wrap both operations in a single DB transaction, add a new method that does both atomically, or defer the first write until after the routing decision is made. Positive alternative: when a status transition has two steps, design the first write to always be a reversible intermediate state (not a terminal status), or combine into a single atomic operation.
