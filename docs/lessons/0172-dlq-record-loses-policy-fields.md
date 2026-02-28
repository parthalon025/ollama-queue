---
title: "dlq-record-loses-policy-fields"
severity: blocker
languages: [python]
scope: [domain:ollama, language:python, framework:sqlite]
category: integration-boundaries
fix: "Design DLQ tables as complete job snapshots — include every field from the jobs table that affects behavior (including max_retries and policy fields), not just the ones that describe the failure. At retry time, pass all copied policy fields to submit_job."
---

## Observation
In ollama-queue db.py `move_to_dlq`, the DLQ table copied operational state (`retry_count`, `timeout`) from the original job but omitted the policy field `max_retries`. When `retry_dlq_entry` re-submitted the job via `submit_job`, it used the default `max_retries=0`, silently dropping the configured retry budget.

## Insight
When designing a dead-letter queue record, the schema was built from "what state describes this failure" rather than "what does re-submission need to reconstruct the original job". Policy fields (max_retries, resource_profile) were omitted as non-operational.

## Lesson
The DLQ schema must be a complete snapshot of all fields needed for re-submission, not just operational fields. Positive alternative: design DLQ tables as "complete job snapshots" — include every field from the jobs table that affects behavior, not just the ones that describe the failure.
