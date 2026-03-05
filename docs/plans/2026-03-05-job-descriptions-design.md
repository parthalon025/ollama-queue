# Job Descriptions Design

**Date:** 2026-03-05
**Status:** Implemented (PR #22)
**Feature:** AI-generated layman descriptions for recurring jobs

## Problem

The Plan tab showed 30 recurring jobs with cryptic names like `aria-snapshot`, `embeddings-ha-aria`, `lessons-db-eval-generate`. Without reading the underlying command, there was no way to know what each job actually did or why it ran regularly. This made it hard to confidently manage the schedule.

## Design

### Storage

Add a `description TEXT` column to `recurring_jobs` via the existing `_add_column_if_missing` migration pattern. Nullable — jobs without a description just show a placeholder.

### Generation

Local Ollama (`qwen3:8b`, temperature 0.2) generates 2 plain-English sentences per job. The prompt includes a system context block explaining domain-specific tags (`aria`, `embeddings`, `lessons`, `telegram`, `notion`) and command patterns — without it, domain-specific jobs like `aria-snapshot` get generic descriptions ("creates a backup") instead of accurate ones ("saves ARIA's learned behavioral patterns").

Tested against 6 diverse job types before implementation. Context-aware prompt accuracy was confirmed correct on all 6.

### Auto-generation timing

On `POST /api/schedule` (new job creation), if no description is provided, generation fires in a background thread immediately. The endpoint returns immediately; the description appears 5-10s later on next poll.

On `POST /api/schedule/{id}/generate-description` (manual trigger), the call is **synchronous** — the endpoint blocks until Ollama responds and returns the description directly. This allows the UI to update the textarea immediately without polling.

### UI

Placed at the **top** of the expanded detail panel in Plan.jsx — above Command. This follows progressive disclosure: "what is this?" before "how does it work?" The description is a `<textarea>` so it's editable inline (manual corrections). A ↻ button triggers synchronous regeneration and updates the field on response. Saved via the existing Save button path.

## Approach Considered

**Option A (chosen): Local Ollama (qwen3:8b), synchronous for manual trigger**
- Accurate with domain context. ~5-10s wait for ↻. No external calls.

**Option B: DB-stored, no auto-generation**
- Simpler, but requires manual entry for all 30 jobs. Not scalable.

**Option C: Async polling**
- Returns immediately, UI polls for description. More moving parts for marginal UX gain.

## Files Changed

| File | Change |
|------|--------|
| `ollama_queue/db.py` | `description TEXT` migration, `add_recurring_job`, `update_recurring_job` |
| `ollama_queue/api.py` | `_JOB_DESCRIPTION_CONTEXT`, `_call_generate_description`, `RecurringJobCreate/Update`, `POST /api/schedule/{id}/generate-description`, auto-trigger on creation |
| `ollama_queue/dashboard/spa/src/store.js` | `generateJobDescription()` |
| `ollama_queue/dashboard/spa/src/pages/Plan.jsx` | `generatingDescId` state, `handleGenerateDescription`, description textarea in `renderDetailPanel`, save logic |

## Bulk generation

To generate descriptions for all existing jobs at once:

```bash
for id in $(curl -s http://localhost:7683/api/schedule | python3 -c \
  "import json,sys; [print(j['id']) for j in json.load(sys.stdin) if not j.get('description')]"); do
  curl -s -X POST http://localhost:7683/api/schedule/$id/generate-description > /dev/null
  echo "done $id"
done
```
