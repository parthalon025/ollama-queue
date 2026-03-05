# Eval Pipeline UI Design

**Date:** 2026-03-05
**Status:** Approved — ready for implementation
**Feature:** Eval tab in ollama-queue dashboard + eval engine migration from lessons-db
**Repos affected:** `ollama-queue` (primary), `lessons-db` (contract endpoints)

## Goal

Add a first-class eval pipeline UI to the ollama-queue dashboard. The eval pipeline tests prompt × model × settings combinations to find the best configuration for extracting transferable principles from structured item collections. The UI must:

1. Give full control over eval runs, variant configs, and prompt templates from the browser
2. Be replicable on any setup via a pluggable data source contract (not locked to lessons-db)
3. Show trends over time — is the system improving, stable, or regressing?
4. Use plain language throughout (L1/L2 always jargon-free, L3 with tooltips)
5. Apply 3-level progressive disclosure to every entity

---

## Architecture

### Overview

The eval pipeline uses a split-seam design (Option C): ollama-queue owns all inference, judging, and scoring logic, while lesson data lives in an external data source reached through a fixed 4-endpoint HTTP contract. This keeps the eval engine general-purpose and the data source swappable.

```
┌─────────────────────────────────────────────────────────────┐
│                    ollama-queue process                      │
│                                                             │
│  ┌──────────────┐    ┌─────────────────────────────────┐   │
│  │  Preact SPA  │    │          Eval Engine             │   │
│  │  (Eval tab)  │◄──►│  run_eval_generate()            │   │
│  └──────────────┘    │  run_eval_judge()               │   │
│         │            │  build_generation_prompt()      │   │
│         │            │  build_judge_prompt()           │   │
│         ▼            │  compute_metrics() / F1         │   │
│  ┌──────────────┐    │  render_report()                │   │
│  │  REST API    │◄──►│                                 │   │
│  │  /api/eval/* │    └──────────────┬──────────────────┘   │
│  └──────────────┘                   │                       │
│         │                           │ Ollama proxy          │
│         ▼                           ▼                       │
│  ┌──────────────┐    ┌─────────────────────────────────┐   │
│  │  SQLite DB   │    │       ollama-queue proxy         │   │
│  │  eval_*      │    │  (existing job queue + router)  │   │
│  │  tables      │    └─────────────────┬───────────────┘   │
│  └──────────────┘                      │                    │
└───────────────────────────────────────┼─────────────────────┘
                                        │
              ┌─────────────────────────▼────────────────────┐
              │           Data Source (lessons-db or any)     │
              │                                               │
              │  GET  /eval/health                            │
              │  GET  /eval/items                             │
              │  GET  /eval/clusters                          │
              │  POST /eval/results                           │
              │  POST /eval/production-variant                │
              └───────────────────────────────────────────────┘
```

### Responsibility Boundary

| Concern | Owner | Notes |
|---|---|---|
| Lesson/item data | Data source | Fetched at run start, not cached long-term |
| Cluster structure | Data source | Used for same/diff-cluster target selection |
| Prompt templates | ollama-queue DB | Editable in UI; system defaults seeded at startup |
| Variant configs | ollama-queue DB | Model, temp, ctx, template binding |
| Generation jobs | ollama-queue proxy | Each generation submitted as a standard queue job |
| Judge jobs | ollama-queue proxy | Judge inference goes through proxy for rate control |
| Scoring + F1 | ollama-queue eval engine | `compute_metrics()` runs server-side after judging |
| Eval run history | ollama-queue DB | `eval_runs` + `eval_results` tables |
| Scored results (write-back) | Data source via `POST /eval/results` | Data source can consume scores for its own purposes |

### Migration Boundary

**Moves from lessons-db to ollama-queue:**
- `run_eval_generate()` — eval engine
- `run_eval_judge()` — eval engine
- `build_generation_prompt()`, `build_judge_prompt()` — prompt construction
- `compute_metrics()`, `render_report()` — scoring and reporting
- `VARIANT_CONFIGS` — replaced by `eval_variants` DB table

**Stays in lessons-db (exposed as contract endpoints):**
- `select_source_lessons()` → `GET /eval/items`
- `select_transfer_targets()` → used internally to serve targets
- Result storage → `POST /eval/results`

### Eval Engine Stages

```
pending → generating → judging → complete
                │
                └→ failed | cancelled
```

Each stage progresses via the progress polling endpoint. `<think>` blocks from judge responses are captured in `eval_results.judge_reasoning` and surfaced in the GenerationInspector.

---

## Data Model

Four new tables in the existing ollama-queue SQLite database.

### `eval_prompt_templates`

```sql
CREATE TABLE eval_prompt_templates (
    id           TEXT PRIMARY KEY,   -- 'fewshot' | 'zero-shot-causal' | 'chunked' | user UUID
    label        TEXT NOT NULL,
    instruction  TEXT NOT NULL,      -- editable main instruction text
    format_spec  TEXT,               -- output format hint
    examples     TEXT,               -- JSON array of {input, output}; fewshot only
    is_chunked   INTEGER DEFAULT 0,  -- 1 = include sibling items from same cluster
    is_system    INTEGER DEFAULT 1,  -- 1 = shipped default; clone-only in UI
    created_at   TEXT NOT NULL
);
```

System templates seeded: `fewshot`, `zero-shot-causal`, `chunked`.

### `eval_variants`

```sql
CREATE TABLE eval_variants (
    id                  TEXT PRIMARY KEY,       -- 'A'–'E' or user UUID
    label               TEXT NOT NULL,
    prompt_template_id  TEXT NOT NULL REFERENCES eval_prompt_templates(id),
    model               TEXT NOT NULL,
    temperature         REAL NOT NULL DEFAULT 0.6,
    num_ctx             INTEGER NOT NULL DEFAULT 8192,
    is_recommended      INTEGER DEFAULT 0,  -- ★ badge
    is_production       INTEGER DEFAULT 0,  -- at most one row = 1
    is_system           INTEGER DEFAULT 0,  -- seeded; clone-only
    is_active           INTEGER DEFAULT 1,
    created_at          TEXT NOT NULL
);
```

System variants seeded (A–E):

| ID | Template | Model | Temp | Ctx | Recommended |
|---|---|---|---|---|---|
| A | fewshot | deepseek-r1:8b | 0.7 | 4096 | — (baseline) |
| B | zero-shot-causal | deepseek-r1:8b | 0.6 | 8192 | — |
| C | chunked | deepseek-r1:8b | 0.6 | 8192 | — |
| D | zero-shot-causal | qwen3:14b | 0.6 | 8192 | ★ |
| E | chunked | qwen3:14b | 0.6 | 8192 | ★ |

### `eval_runs`

```sql
CREATE TABLE eval_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data_source_url TEXT NOT NULL,
    variants        TEXT NOT NULL,       -- JSON array of variant IDs
    per_cluster     INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN
                    ('pending','generating','judging','complete','failed','cancelled')),
    stage           TEXT,
    run_mode        TEXT NOT NULL DEFAULT 'batch' CHECK (run_mode IN
                    ('batch','opportunistic','fill-open-slots','scheduled')),
    item_count      INTEGER,
    item_ids        TEXT,               -- JSON array (reproducibility)
    seed            INTEGER,            -- RNG seed for diff-cluster selection
    judge_model     TEXT,
    judge_backend   TEXT CHECK (judge_backend IN ('ollama','openai')),
    error_budget    REAL DEFAULT 0.30,
    metrics         TEXT,               -- JSON: {variant -> {f1, recall, precision, actionability}}
    winner_variant  TEXT,
    report_md       TEXT,
    error           TEXT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    -- fill-open-slots mode
    max_runs        INTEGER,
    max_time_s      INTEGER,
    runs_completed  INTEGER DEFAULT 0
);
```

### `eval_results`

```sql
CREATE TABLE eval_results (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   INTEGER NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    variant                  TEXT NOT NULL,
    source_item_id           TEXT NOT NULL,
    principle                TEXT,
    judge_reasoning          TEXT,            -- captured <think> blocks
    target_item_id           TEXT NOT NULL,
    is_same_cluster          INTEGER NOT NULL,
    score_transfer           INTEGER,         -- 1–5
    score_precision          INTEGER,         -- 1–5
    score_action             INTEGER,         -- 1–5
    override_score_transfer  INTEGER,
    override_score_precision INTEGER,
    override_score_action    INTEGER,
    override_reason          TEXT,
    generation_time_s        REAL,
    queue_job_id             INTEGER,         -- FK to ollama-queue jobs
    error                    TEXT
);
```

Effective score uses `COALESCE(override_score_*, score_*)` at query time.

### `judge_attempts`

```sql
CREATE TABLE judge_attempts (
    id           TEXT PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES eval_runs(id),
    judge_model  TEXT NOT NULL,
    judge_backend TEXT NOT NULL,
    judge_temp   REAL,
    metrics      TEXT,           -- JSON aggregate metrics for this attempt
    created_at   TEXT NOT NULL
);
```

### Eval Settings (existing `settings` table)

| Key | Default | Notes |
|---|---|---|
| `eval.data_source_url` | `http://127.0.0.1:7685` | Base URL |
| `eval.data_source_token` | _(empty)_ | Bearer auth |
| `eval.per_cluster` | `4` | Items per cluster |
| `eval.same_cluster_targets` | `2` | Positive targets |
| `eval.diff_cluster_targets` | `2` | Negative targets |
| `eval.judge_model` | `deepseek-r1:8b-0528-qwen3-q4_K_M` | Default judge |
| `eval.judge_backend` | `ollama` | `ollama` or `openai` |
| `eval.judge_temperature` | `0.1` | Low temp for determinism |
| `eval.f1_threshold` | `0.75` | Promotion eligibility |
| `eval.stability_window` | `3` | Runs averaged for stability |
| `eval.error_budget` | `0.30` | Circuit breaker threshold |
| `eval.setup_complete` | `false` | Controls setup checklist |

---

## API Endpoints

### ollama-queue — `/api/eval/*`

#### Variants

```
GET    /api/eval/variants                   list all
POST   /api/eval/variants                   create user variant
PUT    /api/eval/variants/{id}              update (user only)
DELETE /api/eval/variants/{id}              delete (user only)
POST   /api/eval/variants/{id}/clone        clone any → user variant
POST   /api/eval/variants/generate          bulk create from model list
GET    /api/eval/variants/generate/preview  proposed names + count (no create)
GET    /api/eval/variants/{id}/history      F1 sparkline across runs
GET    /api/eval/variants/export            JSON export of all user variants
POST   /api/eval/variants/import            bulk import from JSON
```

#### Prompt Templates

```
GET    /api/eval/templates              list all
PUT    /api/eval/templates/{id}         update (user only)
POST   /api/eval/templates/{id}/clone   clone any → user template
```

#### Runs

```
GET    /api/eval/runs                    list (summary)
POST   /api/eval/runs                    trigger run (returns 201 + run_id immediately)
GET    /api/eval/runs/{id}               full detail + metrics + report_md
GET    /api/eval/runs/{id}/progress      polling (5s): stage, pct, per_variant, eta_s, failure_rate
GET    /api/eval/runs/{id}/results       paginated scored pairs
DELETE /api/eval/runs/{id}               cancel
POST   /api/eval/runs/{id}/resume        resume cancelled run
POST   /api/eval/runs/{id}/retry-failed  resubmit errored jobs only
POST   /api/eval/runs/{id}/judge         create new judge attempt (non-destructive)
GET    /api/eval/runs/{id}/judge-attempts list all judge attempts
POST   /api/eval/runs/{id}/promote       set winner as production variant
GET    /api/eval/runs/{id}/export        JSON export with full reproducibility metadata
```

**POST /api/eval/runs body:**
```json
{
  "variants": ["D", "E"],
  "per_cluster": 4,
  "judge_model": "deepseek-r1:8b",
  "judge_backend": "ollama",
  "judge_temperature": 0.1,
  "run_mode": "batch",
  "max_runs": null,
  "max_time_s": null,
  "seed": 1741392847,
  "dry_run": false,
  "pause_after_generate": false
}
```

**GET /api/eval/runs/{id}/progress response:**
```json
{
  "run_id": 7,
  "status": "generating",
  "stage": "generate",
  "completed": 23,
  "total": 78,
  "pct": 29.5,
  "per_variant": {
    "E": {"completed": 12, "total": 16, "failed": 0},
    "D": {"completed": 11, "total": 16, "failed": 1}
  },
  "eta_s": 340,
  "failure_rate": 0.04,
  "queue_depth": 55
}
```

#### Trends + Settings

```
GET /api/eval/trends              per-variant F1 history, stability, signal quality
GET /api/eval/datasource/test     {ok, item_count, cluster_count, response_ms}
GET /api/eval/settings            all eval.* settings
PUT /api/eval/settings            bulk update (validated, all-or-nothing)
POST /api/eval/schedule           create recurring eval session
```

Validation rules on `PUT /api/eval/settings`:
- `judge_backend` ∈ `[ollama, openai]`
- `per_cluster` integer 1–20
- `judge_temperature` float 0.0–2.0
- `data_source_url` valid HTTP/HTTPS URL

### Data Source Contract (5 endpoints)

All requests include `Authorization: Bearer {eval.data_source_token}`.

```
GET  /eval/health
     → {ok: true, item_count: 763, cluster_count: 12}

GET  /eval/items
     ?cluster_id=X  ?limit=N
     → [{id, title, one_liner, description, cluster_id, category}]

GET  /eval/clusters
     → [{id, label, item_count}]  (clusters with >= 3 items only)

POST /eval/results
     {run_id, source: "ollama-queue", results: [{
       source_item_id, target_item_id, variant, principle,
       is_same_cluster, score_transfer, score_precision, score_action
     }]}
     → {accepted: N}  (idempotent — upserts on duplicate key)

POST /eval/production-variant
     {variant_id, model, prompt_template_id, temperature, num_ctx}
     → {accepted: true}
     (lessons-db updates meta extract-principles config)
```

Queue job tagging: every generation/judge job includes `_source: "eval-run-{run_id}"` for batch cancellation.

---

## UI Components

### Overview

New "Eval" tab added to sidebar and bottom nav. Four sub-views inside: Runs, Configurations, Trends, Settings. All entities use 3-level progressive disclosure. Plain language throughout (L1/L2 jargon-free; L3 with `?` tooltips). Translations centralized in `components/eval/translations.js`.

### File Layout

```
pages/Eval.jsx
views/EvalRuns.jsx
views/EvalVariants.jsx
views/EvalTrends.jsx
views/EvalSettings.jsx
components/eval/
  EvalSubNav.jsx
  RunTriggerPanel.jsx
  VariantMultiSelect.jsx
  RunConfigForm.jsx
  SchedulingModeSelector.jsx
  CostEstimator.jsx
  ActiveRunProgress.jsx
  StageIndicator.jsx
  PerVariantProgressBars.jsx
  RunHistoryTable.jsx
  RunRow.jsx
  ResultsTable.jsx
  ResultRow.jsx
  GenerationInspector.jsx
  ItemDifficultyPanel.jsx
  RunDiffPanel.jsx
  VariantTable.jsx
  VariantRow.jsx
  VariantToolbar.jsx
  BulkGenerateDropdown.jsx
  TemplateSection.jsx
  TemplateRow.jsx
  TemplateEditor.jsx
  TrendSummaryBar.jsx
  F1LineChart.jsx
  VariantStabilityTable.jsx
  SignalQualityPanel.jsx
  SetupChecklist.jsx
  DataSourcePanel.jsx
  JudgeDefaultsForm.jsx
  GeneralSettings.jsx
  translations.js
```

### Store Additions (`store.js`)

```js
export const evalSubTab    = signal('runs');
export const evalRuns      = signal([]);
export const evalVariants  = signal([]);
export const evalTemplates = signal([]);
export const evalTrends    = signal(null);
export const evalActiveRun = signal(null);   // persisted to sessionStorage
export const evalSettings  = signal({});

export function startEvalPoll(runId) { /* GET /api/eval/runs/{id}/progress every 5s */ }
export function stopEvalPoll()       { /* clearInterval */ }
```

`evalActiveRun` is written to `sessionStorage` on every update so polling resumes after page refresh. Polling stops when `status ∈ {complete, failed, cancelled}`.

### Plain Language Translations

```js
// components/eval/translations.js
export const EVAL_TRANSLATIONS = {
  f1:                { label: 'Quality score',              tooltip: 'Combined accuracy + completeness. Higher is better.' },
  recall:            { label: 'Catches right patterns',     tooltip: 'How often the principle matches the correct target.' },
  precision:         { label: 'Avoids false matches',       tooltip: 'How often a match is actually correct.' },
  actionability:     { label: 'Useful for preventing bugs', tooltip: 'Whether the principle gives specific, actionable guidance.' },
  temperature:       { label: 'Creativity',                 tooltip: '0=focused, 1=varied.' },
  num_ctx:           { label: 'Memory window',              tooltip: 'How much text the model reads at once, in tokens.' },
  judge_model:       { label: 'Scorer AI',                  tooltip: 'Model used to evaluate generated principles.' },
  'zero-shot-causal':{ label: 'Figure it out',              tooltip: 'Model reasons from cause to effect without examples.' },
  fewshot:           { label: 'Learn from examples first',  tooltip: 'Model sees examples before generating.' },
  chunked:           { label: 'Show examples in groups',    tooltip: 'Examples grouped by type.' },
  generating:        { label: 'Writing principles…',        tooltip: null },
  judging:           { label: 'Scoring results…',           tooltip: null },
};
```

L1/L2 use `label`. L3 may show API field name alongside label: `Quality score (f1)`. Tooltips render on `?` click (not hover) for mobile.

### 3-Level Progressive Disclosure

**Run entity:**
```
L1  ● done  Winner: Config E  Quality: 79%  Mar 5 · 78 items   [▼]

L2  All stages complete
    Config  Quality  Catches right  Avoids false  Useful
    E ★     79%      88%            71%           4.1/5
    D       71%      82%            62%           3.9/5
    A       44%      100%           28%           3.2/5    ← bad precision = bad judge
    Scorer: deepseek-r1:8b · 312 calls · 0 failures
    [Score again ▼]  [Use this configuration]  [Compare ▼]  [Export]
    [▼ Show all scored pairs (312)]

L3  ResultsTable → ResultRow:
    L1: Config E · #47→#83 · Score: 0.84 · ✓ pass  [▼]
    L2: principle text · target · score grid · [Override ▼]
    L3: GenerationInspector
        Prompt sent / Raw response / Principle extracted /
        Scorer reasoning (<think> captured) / Scores + override /
        Queue job: #1847  [view →]
```

**Variant entity:**
```
L1  ★ E  Show examples in groups · qwen3:14b  ★ Recommended  Quality: 79%  [▼]

L2  Model: qwen3:14b  Creativity: 0.6  Memory: 8192 tokens
    How: Show examples in groups  Template: chunked  [preview ▼]
    Quality over time: ▁▃▅▇█  (sparkline, 5 runs)
    [Edit]  [Copy to customize]

L3  Quality by cluster (bar chart)
    Full run history table for this variant
```

**Trend row (Stability table):**
```
L1  E  Quality: 79%  +11% from last run  ●●○ getting better  [▼]

L2  Run by run: 44% → 51% → 68% → 79%
    Lessons tested: 47 → 56 → 63 → 78
    Consistency (last 3): good  Scorer reliability: 84%

L3  Per-cluster performance over time
    Item difficulty table (this variant only)
```

**Data source:**
```
L1  ● lessons-db  763 items · 12 clusters · tested 2m ago  [▼]

L2  URL · masked auth token · [Test now]  [Edit]

L3  Browse items (cluster tree, read-only, full item text on click)
```

### Scheduling Mode Selector

```
How should this run use the queue?

○ Full speed        Submit all jobs now. Fastest option.
                    Estimated: ~45 min

● One at a time     One job at a time. Only when queue is idle.
                    Your other work is never delayed.
                    Estimated: 2–6 hrs

○ Fill open slots   Use all available slots. Keep running until:
                      ○ Time limit   [ 2 ] hrs
                      ○ Run count    [ 5 ] complete runs
                      ○ Both         whichever comes first
                    Builds trend data automatically.

○ Scheduled         Start at: [date] [time]
                    Then run as: ○ Full speed  ○ Fill open slots
                    Repeat: ○ Weekly  ○ Daily  ○ Off
```

Sub-inputs rendered via signal-driven show/hide (not CSS display:none) to avoid stale hidden values in form submission.

### Setup Checklist (shown when `eval.setup_complete = false`)

```
Getting started with eval

☑  1. Connect a data source
      ✓ lessons-db connected · 763 lessons · 12 groups

☐  2. Verify AI models are available
      qwen3:14b ✓  deepseek-r1:8b ✓  qwen3:8b ✗ not loaded
      [Pull missing models →]

☐  3. Create configurations to test
      [⚡ Generate configurations for my models]

☐  4. Run your first evaluation
      [Start first run →]
```

Steps are sequential — step N+1 disabled until N is complete. Disappears permanently when all 4 complete.

---

## Scheduling

### Mode Behaviors

**Batch:** All N jobs submitted at priority 2 simultaneously. ~45 min typical. Best for dedicated sessions.

**Opportunistic:** Submit one job at priority 5. Wait for completion. Check queue depth via `/api/status`. If depth = 0, submit next. Otherwise wait 30s and re-check. Never pre-fills queue. True background operation.

**Fill open slots:** `available_slots = max_concurrent - current_depth`. Submit up to available capacity, top up as jobs complete. Each complete pass = one `eval_run` record. Run until time limit OR run count OR both. Key mode for passive trend accumulation — set 5 runs Sunday night, wake to populated trend chart.

**Scheduled:** Uses existing ollama-queue recurring job system. Creates an `eval_session` job type. Appears in the Plan tab. Supports weekly/daily/off recurrence.

Queue job tagging: `_source: "eval-run-{run_id}"` on every job. Cancellation calls batch-cancel by source tag.

---

## Reproducibility

### Item IDs per Run

`eval_runs.item_ids` stores the JSON array of item IDs used. Answers: "did F1 improve because the variant got better, or because the item set grew?"

TrendChart shows warning banner when item sets differ across displayed runs: *"Item sets differ — comparisons may reflect pool changes, not variant changes."*

### Random Seed

`eval_runs.seed` makes diff-cluster target selection deterministic. Same seed + same items = identical results. Enables controlled A/B: hold seed and items constant, change only the variant. F1 delta is then attributable solely to the variant.

**"Repeat with same items" button** on completed run cards — pre-fills RunTriggerPanel with original item_ids and seed. Clearing either field removes the "Repeat" badge and shows "New run".

### Export metadata includes:
```json
{
  "run_id": "run_022",
  "variant_id": "v_baseline",
  "seed": 1741392847,
  "item_ids": ["item_001", "item_047"],
  "judge_model": "deepseek-r1:8b",
  "created_at": "2026-03-05T23:14:07Z",
  "metrics": {"f1": 0.41, "precision": 0.38, "recall": 0.44}
}
```

---

## Error Handling

### Circuit Breaker

If `failed_count / submitted_count > eval.error_budget` AND `submitted_count >= 10`, the run pauses:

```
⚠ Run paused — too many failures
12 of 31 jobs failed (39%) — above 30% threshold
[Check queue →]  [Resume anyway]  [Retry failed jobs]  [Cancel]
```

### Retry Failed Items

`POST /api/eval/runs/{id}/retry-failed` — resubmits errored generation jobs only. Merges into run without affecting passing pairs. Idempotent.

### Judge Failure Handling

Unparseable judge response → default scores `{transfer:1, precision:1, actionability:1}` + `error` field. Excluded from F1 metrics with warning banner: *"3 pairs excluded from metrics due to scorer failures."*

### Run Cancellation + Resume

Cancel: sets status `cancelled`, batch-cancels pending queue jobs by source tag. In-flight jobs complete and results are preserved.

Resume: `POST /api/eval/runs/{id}/resume` — resubmits uncompleted pairs, merges into same run record.

### Non-Destructive Judge Re-Runs

`POST /api/eval/runs/{id}/judge` creates a `judge_attempt` record — never mutates original scores. UI shows all attempts side-by-side for judge calibration comparison. Active attempt (used for TrendChart) is user-selectable per run.

---

## Onboarding

### Setup Checklist

4-step sequential checklist in EvalSettings. Disappears permanently after completion.

**Step 1** — Connect data source: auto-completes on first load if `eval.data_source_url` is reachable.

**Step 2** — Verify models: cross-references `/api/models` against all variant model fields. Flags missing models. "Pull missing" submits an `ollama pull` job.

**Step 3** — Create configurations: links to BulkGenerateDropdown. Advances when `COUNT(eval_variants) > 0`.

**Step 4** — First run: navigates to Runs view with RunTriggerPanel expanded, pre-filled for a minimal 10-item run. Advances when `COUNT(eval_runs) > 0`.

### Variant Config Portability

```
GET  /api/eval/variants/export  → JSON file (variants + templates)
POST /api/eval/variants/import  → bulk create (non-destructive, skips existing names)
```

Enables copying a tuned variant set to a new machine without manual re-entry.

---

## Success Criteria

- Pipeline identifies a variant with F1 > 0.75 (recall + precision balanced)
- Winning variant demonstrably outperforms baseline (A) on all three metrics
- On a fresh setup: connect data source → bulk generate variants → run eval in < 10 min of setup
- Opportunistic mode never delays other queue work
- All L1/L2 text passes a "no jargon" review (all terms in `EVAL_TRANSLATIONS`)
