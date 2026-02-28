---
title: "count-assertions-vs-membership-assertions"
severity: important
languages: [python]
scope: [domain:ollama, language:python]
category: testing
fix: "Default to membership/content assertions. Only assert length when the exact count is the behavior under test (e.g., pagination returns exactly 10 results)."
---

## Observation
In ollama-queue test_db.py and test_scheduler.py, assertions like `assert len(history) == 2` and `assert len(events) == 1` were used. These break silently when test fixtures change (more events logged, different DB state), producing false failures unrelated to the actual behavior being tested.

## Insight
Asserting list length is easy to write but conflates "the right thing happened" with "exactly N things happened". The latter is fragile in tests where side-effects (logging, events, history) accumulate.

## Lesson
Assert the presence of the expected value rather than the total count: `assert any(e["event_type"] == "promoted" for e in events)` instead of `assert len(events) == 1`. Positive alternative: default to membership/content assertions. Only assert length when the exact count is the behavior under test (e.g., "pagination returns exactly 10 results").
