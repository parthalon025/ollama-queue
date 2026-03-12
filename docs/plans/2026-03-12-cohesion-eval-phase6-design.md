# Dashboard Cohesion + Eval Phase 6 Design

**Date:** 2026-03-12
**Status:** Approved — ready for implementation planning
**Scope:** ollama-queue SPA — all 8 pages + Eval tab Phase 6 redesign

---

## Overview

Two parallel, interleaved efforts that together make the dashboard tell a single coherent story:

1. **20 Information Cohesion Improvements** — the same data (model names, job status, F1 scores,
   variant identities) surfaces consistently across all 8 pages with cross-links, shared vocabulary,
   and a persistent system narrative strip.

2. **Eval Phase 6 Redesign** — the Eval tab becomes a proper "control room" for managing the
   optimization campaign. Eval *data* (results, winner, active run) propagates outward to Now,
   History, Performance, and Plan — it does not live only in the Eval tab.

### Core Principle

> Eval is not a separate system. It runs on Ollama, submits through the queue, produces jobs,
> and generates results. Every page that shows jobs, models, history, or performance should
> know about eval naturally — not through a detour to a separate tab.

### Design Layers

Every component in this doc is described at three levels:

- **Plain English** — what a 15-year-old who has never used this system would understand
- **Decision it drives** — the specific action the user makes after seeing this information
- **Technical terms** — the signal names, API endpoints, and component names for implementation

---

## Part 1 — 20 Information Cohesion Improvements

Organized by the four root causes of incoherence.

---

### A. Data Isolation — clicking an entity should take you to where that entity lives

---

#### 1. `<ModelChip>` — Universal Clickable Model Name

**Plain English:**
Right now if you see a model name like "deepseek-r1:8b" anywhere on the dashboard, it's just
text — you can't click it. This makes it a chip (a small pill-shaped label) that you can click
to jump straight to that model's detail page.

**Decision it drives:**
"I see this model is running — let me check its performance history / eval score / queue depth."

**Technical terms:**
- New component: `src/components/ModelChip.jsx`
- Props: `name: string, provider?: string, isLive?: bool, evalRole?: 'judge'|'generator'`
- On click: sets `currentTab.value = 'models'` + `modelFilter.value = name`
- Replaces all inline model name renders across: `Now.jsx`, `History.jsx`, `Plan/index.jsx`,
  `Eval` components, `RunHistoryTable.jsx`, `ResultsTable.jsx`

---

#### 2. Job Deep-Link from History

**Plain English:**
When you're looking at a failed job in the History tab (the "dead letter queue"), there's
currently no way to see what that job was doing when it ran. This adds a "→ View context"
button that jumps you to the Now tab and highlights that job.

**Decision it drives:**
"This job failed — I want to see what it was running, what model it used, and whether it's
been retried."

**Technical terms:**
- DLQ entries in `History.jsx` get a `→` action button
- On click: sets `currentTab.value = 'now'` + `highlightJobId.value = job.id`
- `Now.jsx` reads `highlightJobId` signal and pulses that job's row
- New signal: `highlightJobId` in `stores/index.js`

---

#### 3. Eval Winner Badge on Models Tab

**Plain English:**
The Models tab shows all the AI models you have. But it doesn't tell you which model is
currently the "best judge" according to your testing system (eval). This adds a gold star
badge to that model: "★ Best judge (F1 score: 0.87)".

**Decision it drives:**
"Should I keep using this model as my judge, or try a different one?"

**Technical terms:**
- `ModelsTab.jsx` reads `evalVariants.value` (already fetched) to find `is_production=true` variant
- From that variant, extract `judge_provider` + `judge_model` from eval settings
- Show `<EvalRoleBadge role="judge" f1={topRun.f1} runId={topRun.id} />` on matching model row
- Badge links: sets `currentTab.value = 'eval'` + `evalSubTab.value = 'runs'`

---

#### 4. Scheduled Eval Runs on Plan Gantt

**Plain English:**
The Plan tab shows a timeline of all the jobs scheduled to run in the next 24 hours. But
eval runs (which also use your GPU and take time) are invisible there. This makes eval runs
show up on the timeline like any other job, so you can see when they'll compete for GPU time.

**Decision it drives:**
"Is there enough GPU headroom for my eval run tonight, or will it conflict with these other jobs?"

**Technical terms:**
- `Plan/index.jsx` fetches scheduled eval runs from `GET /api/eval/runs?status=scheduled`
- Renders them as `<GanttBlock type="eval" label="Eval: variant-C vs A" />` on the timeline
- Color: use the eval accent color (distinct from regular job color)
- Existing `GanttChart.jsx` accepts a `blocks` prop — add eval blocks to that array

---

#### 5. Consumer Attribution on Eval Runs

**Plain English:**
When eval runs a test, it submits jobs to the queue and a "consumer" (a program listening
for jobs) handles them. Right now you can't see which consumer handled an eval job or how
long it took. This surfaces that info on each eval run's detail panel.

**Decision it drives:**
"Is eval being handled by the right consumer? Is one consumer slower than the others?"

**Technical terms:**
- `RunRow/index.jsx` expanded detail panel: show `consumer_id` from job result metadata
- API: `GET /api/eval/runs/{id}/progress` already returns job-level metadata — surface
  `consumer_id` and `duration_ms` per phase
- Link consumer name via `<ConsumerChip id={consumer_id} />` → navigates to Consumers tab

---

### B. Contextual Amnesia — the current state survives tab changes

---

#### 6. Persistent Active-Job Strip

**Plain English:**
Right now, if an AI model is running a job and you switch from the "Now" tab to any other tab,
that information disappears. This adds a thin bar at the top of every page that always shows
what's running: "deepseek-r1:8b · 2m 14s · 3 queued".

**Decision it drives:**
"Is the system still busy, or can I submit another job?"

**Technical terms:**
- New component: `src/components/ActiveJobStrip.jsx`
- Reads: `currentJob.value` (already exists in `stores/index.js`)
- Renders in `app.jsx` above the main content area, below the cohesion header
- Hidden when `currentJob.value === null`
- Hidden on Now tab (redundant with full CurrentJob display)
- Mobile: collapses to a single icon with running animation

---

#### 7. Persistent Active-Eval Strip

**Plain English:**
When an eval test is running in the background (testing different AI prompt versions against
each other), you currently have to stay on the Eval tab to see progress. This adds a strip
that shows progress everywhere: "Eval: testing variant-C · judging phase · F1 so far: 0.82".

**Decision it drives:**
"Is the eval still running? Is it making progress? Should I wait before submitting more jobs?"

**Technical terms:**
- New component: `src/components/ActiveEvalStrip.jsx`
- Reads: `evalActiveRun.value` (already exists in `stores/eval.js`)
- Shows phase (generate/judge/promote) from `run.phase`, progress bar from `run.progress_pct`
- Shows current best F1 from `run.best_f1_so_far` if available
- Renders in `app.jsx` alongside `ActiveJobStrip` — stacks if both active
- Click → sets `currentTab.value = 'eval'` + `evalSubTab.value = 'campaign'`

---

#### 8. Global System Summary Line

**Plain English:**
The sidebar already shows a small green/red dot for system health. This upgrades it to one
readable sentence: "3 waiting · deepseek running · variant-C winning". Like a status bar at
the bottom of a phone, but for your AI queue.

**Decision it drives:**
"Do I need to take any action right now, or is everything running as expected?"

**Technical terms:**
- Extend `Sidebar.jsx` health chip area: add a `<SystemSummaryLine />` component below it
- Reads: `queueDepth.value`, `currentJob.value?.model`, `evalWinner.value` (new signal)
- `evalWinner.value` = derived signal: `computed(() => evalVariants.value.find(v => v.is_recommended))`
- Format: `{n} queued · {model} running · {variant} winning`
- Each segment is a `<ModelChip>` / `<VariantChip>` for click-through

---

#### 9. Last Eval Winner Persistent Chip

**Plain English:**
After each eval test, the system picks a winning prompt variant. Right now that result is
only visible on the Eval tab. This adds a small "★ variant-C · 0.87" chip in the sidebar
that's always visible, so you always know which prompt version is currently winning.

**Decision it drives:**
"Has the winning variant changed since I last checked? Do I need to promote it?"

**Technical terms:**
- New component: `src/components/EvalWinnerChip.jsx`
- Reads: `evalWinner.value` derived signal (see #8)
- Shows variant label + F1 score + `★` if `is_production`, `☆` if `is_recommended` only
- Click → `currentTab.value = 'eval'` + `evalSubTab.value = 'trends'`
- Hidden if no variants exist or no runs completed

---

#### 10. Live Model Indicators on Models Tab

**Plain English:**
The Models tab lists all your AI models, but it looks like a static list — it doesn't show
you which models are actually busy right now. This adds a pulsing dot to models that are
currently running a job, in the eval queue, or being used by a consumer.

**Decision it drives:**
"Is this model busy? Is it safe to change its settings, or will that interrupt something?"

**Technical terms:**
- `ModelsTab.jsx` reads `currentJob.value?.model` and `evalActiveRun.value?.variants` to
  derive the set of "live" model names
- Models matching a live model get a `<LiveIndicator pulse />` dot on their row
- Three states: `running` (solid pulse), `queued` (slow pulse), `in-eval` (blue pulse)

---

### C. Terminology & Metric Drift — the same concept has one name everywhere

---

#### 11. Unified Job Status Vocabulary

**Plain English:**
Different pages use different words for the same thing. The Now tab might say "active" while
the History tab says "running". This standardizes to exactly six words used everywhere, each
with its own color, so you always know what's happening at a glance.

**Decision it drives:**
N/A — this is foundational. Every status-related decision becomes more reliable.

**Technical terms:**
- Canonical statuses: `queued` (grey) · `running` (blue) · `complete` (green) · `failed` (red)
  · `deferred` (amber) · `cancelled` (muted)
- New component: `src/components/StatusPill.jsx` — single source for all status rendering
- Audit and replace all inline status strings across: `Now.jsx`, `History.jsx`, `Plan/index.jsx`,
  `RunHistoryTable.jsx`, `ResultsTable.jsx`, `Consumers.jsx`

---

#### 12. Priority Display System

**Plain English:**
"Priority" tells you how urgently a job needs to run. Right now it's shown as just a number
in some places and missing entirely in others. This makes it a color-coded label everywhere:
red for "critical", orange for "high", blue for "normal", grey for "low".

**Decision it drives:**
"Should I bump the priority of my job to get it ahead of the queue?"

**Technical terms:**
- New component: `src/components/PriorityPill.jsx`
- Props: `level: 'critical'|'high'|'normal'|'low'` (uses existing `utils/priority.js` mapping)
- Replace all inline priority renders across Now, History, Plan, Submit modal

---

#### 13. Duration Format Standard

**Plain English:**
Time durations are shown inconsistently: sometimes "90 seconds", sometimes "1.5 min",
sometimes just a raw number. This makes every duration show as "1m 30s" — the same format,
everywhere.

**Decision it drives:**
N/A — foundational. Consistent format prevents misreading durations.

**Technical terms:**
- `utils/time.js`: add/update `formatDuration(ms: number): string` → "Xm Ys" (or "Xs" if < 60s,
  or "Xh Ym" if > 60m)
- Audit all duration renders: replace raw `ms`, raw seconds, `toFixed(1) + 'min'` with
  `formatDuration()`
- Affects: `CurrentJob.jsx`, `RunHistoryTable.jsx`, `History.jsx`, `Performance.jsx`,
  `RunRow/index.jsx`

---

#### 14. `<F1Score>` — Consistent F1 Display with Tooltip

**Plain English:**
"F1 score" is a number between 0 and 1 that measures how well a prompt version is performing.
Right now it's displayed as a plain number in some places and formatted differently in others.
This makes it always look the same — 0.87 in a green pill — and shows a tooltip explaining
what it means when you hover: "F1 = how well this prompt finds the right lessons: 87 out of 100."

**Decision it drives:**
"Is this variant good enough to promote? Is it better than the previous one?"

**Technical terms:**
- New component: `src/components/F1Score.jsx`
- Props: `value: number, delta?: number, showTooltip?: bool`
- Tooltip text: "F1 measures how often this variant correctly identifies relevant lessons.
  1.0 = perfect, 0.0 = useless. Weighted harmonic mean of precision and recall."
- Color: green ≥ 0.80, amber 0.60–0.79, red < 0.60
- Replace all F1 number renders across: `VariantRow.jsx`, `RunHistoryTable.jsx`,
  `TrendSummaryBar.jsx`, `EvalWinnerChip.jsx`, `ModelsTab.jsx`

---

#### 15. `<VariantChip>` — Consistent Variant Identity

**Plain English:**
Prompt variants (versions A, B, C, etc.) are shown differently in different places: sometimes
just a letter, sometimes a name, sometimes with a score, sometimes without. This makes them
always appear as a consistent pill: "C · 0.87 · ollama" with a gold star if it's the winner.

**Decision it drives:**
"Is this the winning variant? Which provider does it use? Should I clone it?"

**Technical terms:**
- New component: `src/components/VariantChip.jsx`
- Props: `id: string, label: string, f1?: number, provider?: string, isProduction?: bool,
  isRecommended?: bool`
- Click → `currentTab.value = 'eval'` + `evalSubTab.value = 'variants'` + `focusVariantId.value = id`
- Replace inline variant renders across: `EvalWinnerChip.jsx`, `RunHistoryTable.jsx`,
  `TrendSummaryBar.jsx`, `ModelsTab.jsx`, `ActiveEvalStrip.jsx`, `Plan Gantt eval blocks`

---

### D. Narrative Gap — the pages together tell one coherent story

---

#### 16. Cohesion Header Strip

**Plain English:**
Right now each page feels like a completely separate app — you don't know what the whole
system is doing just by looking. This adds a thin strip at the top of every page showing one
sentence about what's happening right now: "3 waiting · deepseek running 2m · eval: variant-C
leading". Like a news ticker for your AI queue.

**Decision it drives:**
"Do I need to take action anywhere, or can I focus on what I'm doing?"

**Technical terms:**
- New component: `src/components/CohesionHeader.jsx`
- Reads: `queueDepth`, `currentJob`, `evalActiveRun`, `evalWinner`, `dlqCount` signals
- Sticky, 32px height, rendered in `app.jsx` above the per-page content
- Desktop/tablet only (mobile already has BottomNav summary)
- Segments link to their respective tabs using existing `currentTab` signal

---

#### 17. Performance Page Eval Annotations

**Plain English:**
The Performance tab shows charts of how fast each AI model processes jobs. But it doesn't
show anything about the *quality* of the outputs — which eval found to be the best. This adds
annotations on the charts: a gold star on the model that's the current eval winner.

**Decision it drives:**
"Is the model I'm relying on for quality also performing well on speed? Is there a
quality-vs-speed tradeoff I should address?"

**Technical terms:**
- `Performance.jsx` reads `evalVariants.value` to find `is_production=true` variant's
  `judge_model` and `generator_model`
- Overlay `<EvalAnnotation label="★ Current judge" f1={score} />` on that model's perf curve
- uPlot plugin: draw a vertical marker at the F1 threshold line
- Data source: existing `/api/eval/variants` response (already fetched in `stores/eval.js`)

---

#### 18. History Timeline Includes Eval Events

**Plain English:**
The History tab shows a heatmap of when jobs ran. But it doesn't show when eval tests happened
or when a new prompt version was promoted. Adding these events lets you see the narrative:
"eval ran on Tuesday, F1 went up, variant-C was promoted on Wednesday."

**Decision it drives:**
"Did the system improve after that eval run? When was the last time we tested our prompts?"

**Technical terms:**
- `History.jsx` fetches `GET /api/eval/runs?limit=50` alongside existing DLQ data
- Injects eval run events into the `ActivityHeatmap` as a distinct event type (color: eval accent)
- Event types: `eval_started`, `eval_completed`, `eval_promoted` — differentiated by icon in
  tooltip
- Existing `ActivityHeatmap.jsx` accepts `events` prop — extend to support typed events

---

#### 19. Plan Tab Nav Badge for Upcoming Eval

**Plain English:**
The Plan tab shows what's scheduled to run. If an eval test is scheduled in the next few hours,
you'd want to know that before planning other jobs. This adds a small "EVAL" badge on the Plan
tab in the navigation so you can see it from any page.

**Decision it drives:**
"Should I defer my big batch job to avoid colliding with the scheduled eval?"

**Technical terms:**
- `Sidebar.jsx` and `BottomNav.jsx` read `scheduledEvalCount.value` (new derived signal)
- `scheduledEvalCount` = count of eval runs from `/api/eval/runs?status=scheduled` within 4h
- Renders as a small `EVAL` badge on the Plan nav item (same style as DLQ count badge on History)
- Signal refreshes on the same 30s poll cycle as other schedule data

---

#### 20. Variant Data Lineage Tooltip

**Plain English:**
When you see that a prompt variant is "in production" (the official best one), there's no
explanation of how it got there. This adds an info button (ⓘ) that shows the history:
"Promoted from test run #42 · beat variant-A by +0.12 · tested 847 lessons · 2026-03-10."
Like a "where did this come from?" label on a product.

**Decision it drives:**
"Is this variant's promotion still valid? Was it tested recently enough? Should I re-test?"

**Technical terms:**
- `VariantRow.jsx` and `VariantChip.jsx` show a `ⓘ` icon when `is_production=true` or
  `is_recommended=true`
- Tooltip reads from variant's `promoted_from_run_id` (new field in Phase 2 schema migration)
  or from the latest run that produced `is_production=true` status
- API: `GET /api/eval/variants/{id}/lineage` (new endpoint) returns:
  `{run_id, run_date, f1_delta, comparison_variant_id, lessons_tested}`
- Short-term fallback: derive from existing run history until lineage endpoint exists

---

## Part 2 — Eval Phase 6: The Control Room

The Eval tab becomes the management surface for the optimization campaign. It no longer tries
to be the only place where eval information lives — that information now propagates to other
pages (see Part 1). The tab's job is to let you configure, trigger, compare, and guide the
optimization process.

### Mental Model Shift

| Old mental model | New mental model |
|---|---|
| "Eval tab = where eval lives" | "Eval tab = where eval is managed" |
| "Runs = the point of eval" | "Variants = the point of eval; runs are evidence" |
| "Settings = config" | "Config = how providers connect; Campaign = how well it's working" |
| "Trends = F1 line chart" | "Timeline = the journey from raw prompts to fine-tuned variants" |

---

### Tab 1: **Campaign** (replaces Runs)

**Plain English:**
This is your "command center" for the testing campaign. It shows you who's winning (which
prompt version is currently getting the best results), what the testing system is doing right
now, and — most importantly — what it thinks you should try next. Like a coach showing you
the scoreboard and suggesting your next play.

**Decision it drives:**
"Is this campaign converging toward a clear winner? Should I run another test, or is it time
to promote the winner and stop?"

**Technical terms:**

**F1 Leader chip (always visible)**
- Reads `evalVariants.value` to find `is_recommended=true` variant
- Shows: `★ variant-C · F1 0.87 · +0.12 vs baseline · stable (σ=0.02)`
- Uses `<VariantChip>` + `<F1Score>` shared components

**Active run progress (conditional)**
- Renders when `evalActiveRun.value !== null`
- Same swimlane phases: `generate → judge → analyze → promote`
- Progress bar from `run.progress_pct`; cancel button calls `POST /api/eval/runs/{id}/cancel`
- Phase labels use plain language: "Generating outputs", "Scoring with judge", "Analyzing results",
  "Deciding winner"

**Next Steps card (post-run)**
- Renders from `evalActiveRun.value.suggestions_json` (Phase 1 already populates this field)
- Each suggestion is a one-click action card:
  - "Clone variant-C and lower temperature to 0.4" → opens Variants tab with pre-filled clone form
  - "Run oracle calibration" → triggers oracle run modal
  - "Expand eval set — Goodhart risk detected" → opens data source panel with rotation prompt
- Max 3 suggestions shown; collapsible

**Oracle report panel (conditional)**
- Renders when `evalActiveRun.value.oracle_json !== null`
- Shows: Kappa score, agreement %, disagreement count, OPRO-derived prompt suggestions
- Plain English header: "How reliable was the judge?" — Kappa tooltip: "Agreement between
  judge and reference answers. 1.0 = perfect, 0.0 = random."

**Run trigger panel**
- Collapsed by default when an active run exists
- Variant multi-select (checkbox grid, not dropdown)
- Scheduling mode selector: `batch` (run now) / `opportunistic` (run when GPU is idle)
- Start button disabled with tooltip if data source not connected

**Run history table**
- Last 10 runs, one row per run
- Columns: date, variants tested, winner, F1, status badge (uses `<StatusPill>`)
- Click row → expand inline analysis panel (same as current `GenerationInspector`)

---

### Tab 2: **Variants** (replaces Configurations)

**Plain English:**
This is your library of prompt versions. Think of it like having multiple drafts of an essay —
each "variant" is a different way of asking the AI to find the right lessons. This page lets
you see all the drafts at a glance, compare two of them side-by-side, or automatically create
10 variations of one draft to test all at once.

**Decision it drives:**
"Which variant should I promote to production? Which one should I use as the base for the
next round of testing? Is there one that's stable enough to trust?"

**Technical terms:**

**Card grid (replaces table)**
- Each card (min-width 280px, CSS Grid auto-fill):
  - Header: variant label + `[ollama]`/`[claude]`/`[openai]` provider badge
  - Score area: `<F1Score value={v.f1}>` + stability badge (σ < 0.03 = stable, amber 0.03–0.07, red > 0.07)
  - Status badges: `★ Production` (gold) / `☆ Recommended` (silver) — at most one of each exists
  - Param pills: top 3 non-default params as small pills (`temp 0.7`, `top_k 40`, `mirostat 2`)
  - System prompt preview: first 60 chars, `…` if truncated
  - Footer actions: `Clone` · `Edit` · `Delete` · checkbox for Compare mode
- Cards sorted by F1 desc; unscored variants at bottom

**Compare mode**
- Checking 2+ card checkboxes opens a diff matrix panel above the grid
- Matrix rows = config fields; matrix columns = selected variants
- Cells that differ are highlighted in amber
- Fields: `provider`, `model`, `temperature`, `top_k`, `mirostat`, `system_prompt` (diff view),
  `params` (JSON diff), `training_config`
- "Run eval on selected variants" button in compare mode header

**Sweep generator**
- Toolbar button: "Sweep"
- Form: pick base variant (dropdown) + dimension (temperature/top_k/mirostat/num_ctx) +
  range (min/step/max or comma-separated list)
- Preview: shows variant names that will be created (e.g., "variant-D-temp-0.3" through
  "variant-D-temp-0.9")
- Submits `POST /api/eval/variants/sweep` (Phase 1 endpoint — already exists)

**Create/edit form**
- Provider dropdown (ollama/claude/openai) — changing provider updates model dropdown via
  `GET /api/eval/providers/models?provider=X`
- System prompt textarea (plain text, monospace, resize)
- Params JSON editor with inline validation — rejects unknown keys with fuzzy suggestion
  ("did you mean 'top_k'?"), rejects flat-column params (temperature, num_ctx belong in
  dedicated fields)
- Training config field (JSON, for Unsloth integration in Phase 5)
- Test button: `POST /api/eval/providers/test` — validates model connectivity

---

### Tab 3: **Timeline** (replaces Trends)

**Plain English:**
This page shows the history of your testing campaign as a timeline — like a story with a
beginning, middle, and hopefully a happy ending. On the left is "we started with a basic
prompt", and on the right is "we've now fine-tuned it to be much more accurate." Each dot
on the line is a test run; each star is when a new winner was promoted.

**Decision it drives:**
"Is the system still improving, or has it plateaued? Are we at Level 0 (basic prompting) or
have we progressed to Level 1 (tuned params) or Level 2 (fine-tuned model)? Should I start
a new campaign?"

**Technical terms:**

**Optimization timeline (new visualization)**
- uPlot chart, X-axis = time, Y-axis = F1 score
- One line per variant (colored, labeled)
- Event markers overlaid:
  - Dot = run completed (tooltip: run ID, variants tested, winner)
  - Star = variant promoted (tooltip: variant, F1, delta)
  - Circle = oracle calibrated (tooltip: Kappa score)
- "Escalation levels" background bands (from enhancement design):
  - Level 0 band: grey — prompt engineering phase
  - Level 1 band: blue — params tuning phase
  - Level 2 band: purple — fine-tuning phase
  - Current level highlighted; threshold crossed when promoted variant used training_config
- Data source: `GET /api/eval/trends` (existing) + run event log

**Stability panel**
- Below timeline: per-variant F1 stdev table (`<VariantStabilityTable>` — existing component)
- Stable (σ < 0.03) = green badge; unstable = amber/red
- Plain English label: "How consistent is this variant? A stable one gets similar scores every
  time it's tested."

**Signal quality panel**
- `<SignalQualityPanel>` — existing component
- Bayesian quality indicators — confidence in current winner
- Plain English label: "How sure are we that variant-C is actually better? High confidence
  means more test runs have confirmed it."

---

### Tab 4: **Config** (replaces Settings)

**Plain English:**
This is the setup page for the whole eval system. You tell it which AI service to use for
generating test outputs (the "generator"), which one to use as a judge to score them, and
where to get the lesson data it tests against. You only need to come here when setting up
for the first time or switching providers.

**Decision it drives:**
"Is the eval system correctly connected to all the services it needs? Is it configured to
stay within budget?"

**Technical terms:**

**Setup checklist (gating)**
- `<SetupChecklist>` — existing component, moved here from Settings view
- Gate 1: data source connected (lessons-db ping succeeds)
- Gate 2: at least one eval run completed
- Only show Campaign/Variants/Timeline tabs as fully interactive once Gate 1 passes

**Provider section (new — Phase 1 backend exists, no UI yet)**
- Four role tabs: Generator / Judge / Optimizer / Oracle
- Per role: provider dropdown (`ollama`/`claude`/`openai`) + model dropdown (dynamic via
  `GET /api/eval/providers/models?provider=X`) + API key input (masked, stored in settings
  not .env, shown as `sk-...xxxx`) + "Test connection" button (`POST /api/eval/providers/test`)
- Budget control: max cost per run (USD, disabled for Ollama), max API calls per phase
- Plain English label: "Generator = which AI creates the test outputs. Judge = which AI
  scores them. Optimizer = which AI suggests better prompts. Oracle = the reference AI used
  to check the judge's accuracy."

**Data source panel**
- `<DataSourcePanel>` — existing component
- lessons-db URL + token + test button + prime button
- Plain English label: "This is where the lessons come from — the database of things the AI
  is supposed to learn from your experience."

**Auto-promote gates**
- `<JudgeDefaultsForm>` — existing component, relabeled for clarity
- F1 threshold, stability window, min improvement delta, error budget
- Plain English labels on each field: "Minimum score to promote" (F1 threshold),
  "How many tests before we trust the result" (stability window), etc.

**General settings**
- `<GeneralSettings>` — existing component
- Analysis model, lessons per group, polling interval
- Plain English label: "Analysis model = which AI reads the results and writes a plain-English
  summary. You can leave this blank to use the same model as the judge."

---

## Part 3 — Shared Components Inventory

All components introduced here. Each is used in multiple places to enforce the single-source
discipline described in pain points C and D.

| Component | File | Used by |
|---|---|---|
| `<ModelChip>` | `components/ModelChip.jsx` | Now, History, Plan, Eval (all tabs), Performance |
| `<VariantChip>` | `components/VariantChip.jsx` | EvalWinnerChip, RunHistoryTable, TrendSummaryBar, ModelsTab, ActiveEvalStrip, Plan Gantt |
| `<F1Score>` | `components/F1Score.jsx` | VariantRow, VariantChip, RunHistoryTable, TrendSummaryBar, EvalWinnerChip, ModelsTab |
| `<StatusPill>` | `components/StatusPill.jsx` | Now, History, Plan, RunHistoryTable, ResultsTable, Consumers |
| `<PriorityPill>` | `components/PriorityPill.jsx` | Now, History, Plan, SubmitJobModal |
| `<LiveIndicator>` | `components/LiveIndicator.jsx` | ModelsTab, ConsumersTab, ModelChip |
| `<EvalRoleBadge>` | `components/EvalRoleBadge.jsx` | ModelsTab |
| `<ConsumerChip>` | `components/ConsumerChip.jsx` | RunRow expanded detail |
| `<ActiveJobStrip>` | `components/ActiveJobStrip.jsx` | app.jsx (global) |
| `<ActiveEvalStrip>` | `components/ActiveEvalStrip.jsx` | app.jsx (global) |
| `<CohesionHeader>` | `components/CohesionHeader.jsx` | app.jsx (global) |
| `<EvalWinnerChip>` | `components/EvalWinnerChip.jsx` | Sidebar.jsx |
| `<SystemSummaryLine>` | `components/SystemSummaryLine.jsx` | Sidebar.jsx |
| `formatDuration()` | `utils/time.js` | All duration renders (global audit) |

---

## Part 4 — Data Propagation Map

How eval data flows to pages outside the Eval tab.

| Eval data | Signal / endpoint | Propagates to | Component |
|---|---|---|---|
| Active eval run | `evalActiveRun` (existing) | Now (CurrentJob area), all pages (ActiveEvalStrip) | `ActiveEvalStrip` |
| Run completion / promotion events | `GET /api/eval/runs?limit=50` | History (ActivityHeatmap) | eval event markers |
| Scheduled eval runs | `GET /api/eval/runs?status=scheduled` | Plan (Gantt) | `GanttBlock type=eval` |
| Winner variant + F1 | `evalWinner` (derived signal) | Sidebar chip, Models tab, CohesionHeader | `EvalWinnerChip`, `EvalRoleBadge` |
| Judge/generator model name | `evalVariants[is_production].judge_model` | Performance (curve annotation), Models tab | `EvalAnnotation`, `EvalRoleBadge` |
| Scheduled eval count (4h window) | `scheduledEvalCount` (derived signal) | Plan tab nav badge | Sidebar + BottomNav |

---

## Part 5 — New Signals Required

New signals to add to `stores/eval.js` and `stores/index.js`.

| Signal | Type | Source | Used by |
|---|---|---|---|
| `evalWinner` | `computed` | `evalVariants.find(v => v.is_recommended)` | EvalWinnerChip, CohesionHeader, SystemSummaryLine, EvalRoleBadge |
| `scheduledEvalCount` | `signal` | `GET /api/eval/runs?status=scheduled&within_hours=4` | Plan tab badge |
| `highlightJobId` | `signal` | Set by History deep-link click | Now.jsx pulse effect |
| `modelFilter` | `signal` | Set by ModelChip click | ModelsTab filter |
| `focusVariantId` | `signal` | Set by VariantChip click | Variants tab card focus |

---

## Part 6 — New API Endpoints Required

Backend additions needed to support cohesion improvements and Phase 6.

| Method | Path | Purpose | Priority |
|---|---|---|---|
| `GET` | `/api/eval/runs?status=scheduled&within_hours=4` | Plan badge + Gantt blocks | High |
| `GET` | `/api/eval/variants/{id}/lineage` | Variant data lineage tooltip (#20) | Medium |
| `GET` | `/api/eval/providers/models?provider=X` | Provider model dropdown (Phase 1 — exists) | Exists |
| `POST` | `/api/eval/providers/test` | Provider connectivity test (Phase 1 — exists) | Exists |

---

## Part 7 — Implementation Phasing

These improvements are grouped into three buildable batches. Each batch is independently
deployable and adds visible value.

### Batch 1 — Shared Components + Vocabulary (foundation)
Items: #11, #12, #13, #14, #15 (shared components + format standards)
Why first: every other improvement depends on these. `<F1Score>`, `<ModelChip>`, `<VariantChip>`,
`<StatusPill>`, `<PriorityPill>` must exist before they can be placed in other pages.

### Batch 2 — Global State Strips + Sidebar (contextual amnesia)
Items: #6, #7, #8, #9, #10, #16 (global strips, sidebar chips, cohesion header)
Why second: these use the shared components from Batch 1 and establish the persistent
system narrative that makes all subsequent cross-page links meaningful.

### Batch 3 — Cross-Page Data Propagation (data isolation + narrative gap)
Items: #1, #2, #3, #4, #5, #17, #18, #19, #20 (deep-links, badges, history events, Gantt, lineage)
Why third: these rely on the shared components (Batch 1) and the signals architecture (Batch 2).

### Batch 4 — Eval Tab Phase 6 Redesign
Items: Campaign tab, Variants card grid + compare + sweep, Timeline visualization, Config with
provider section
Why last: the eval tab redesign is self-contained and the shared components from Batches 1–3
make the new eval components consistent with the rest of the dashboard automatically.

---

## Constraints & Non-Goals

- **No new polling endpoints** unless explicitly listed. All new signals must derive from
  existing poll data where possible (see Part 5).
- **No layout changes to non-Eval pages** beyond adding the global strips and injecting
  shared components. Page structure stays the same.
- **No backend schema changes in this design.** Phase 2 schema migrations are a separate
  concern. `lineage` endpoint (#20) has a short-term fallback.
- **Mobile:** Global strips collapse to icon-only. CohesionHeader hidden on mobile.
  Card grid in Variants collapses to single column.
- **Eval tab structure stays at 4 sub-tabs.** Renamed: Campaign / Variants / Timeline / Config.
