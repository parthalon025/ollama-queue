---
title: "executor-not-recreated-on-config-change"
severity: important
languages: [python]
scope: [domain:ollama, language:python]
category: specification-drift
fix: "For any pool/executor created from a configurable setting, add a comment: 'Re-start required to change max_workers' OR implement a re-creation path. Never leave the gap undocumented."
---

## Observation
In ollama-queue daemon.py, `ThreadPoolExecutor` was created once at startup with `max_workers=_max_slots()`. The `max_concurrent_jobs` setting was exposed as a runtime-settable value via the API, but changing it had no effect because the executor retained its original `max_workers`.

## Insight
Resource pools (executors, connection pools, thread pools) created from configuration at startup do not automatically reflect runtime configuration changes. The gap between "what the code thinks the limit is" and "what the pool enforces" is silent.

## Lesson
Either: (a) detect the setting change and re-create the executor, or (b) document in the API response that a service restart is required to apply the change. Positive alternative: for any pool/executor created from a configurable setting, add a comment: "Re-start required to change max_workers" OR implement a re-creation path. Never leave the gap undocumented.
