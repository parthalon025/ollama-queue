# UX & Design Philosophy Improvements — Design

**Date:** 2026-03-11
**Status:** Approved (brainstorming complete)
**Scope:** `ollama_queue/dashboard/spa/src/` — all components, pages, stores
**Goal:** 40 targeted improvements across two axes: ease of use/understanding (20 items) and design
philosophy adherence — ARIA design language + superhot-ui aesthetic (20 items).

## BLUF

Four phased PRs. Phase 1 lays the UX foundation (empty states, plain-English tooltips, CTAs,
keyboard navigation). Phase 2 adds interaction depth (undo cancel, live log tail, retry, settings
restart banner, stall guidance). Phase 3 extends the superhot visual language throughout the app
(freshness on queue rows, PAUSED/OFFLINE mantra, glitch on disconnection, shatter on DLQ/History).
Phase 4 closes the visualization-science gap (sparklines audit, progressive disclosure, heatmap
hover, animation tier enforcement). Phases 3 and 4 can run in parallel worktrees after Phase 1 merges.

---

## Implementation Approach

**Approach B: Phased by concern** — 4 standalone PRs.

| Phase | Theme | Items | Branch |
|-------|-------|-------|--------|
| 1 | UX Foundation | #1–#7, #16, #20 | `feature/ux-foundation` |
| 2 | Interaction Depth | #8–#15, #17–#19 | `feature/ux-interactions` |
| 3 | Superhot Philosophy | #21–#33 | `feature/superhot-philosophy` |
| 4 | Visualization Science | #34–#40 | `feature/viz-science` |

Phases 3 and 4 can be implemented in parallel worktrees once Phase 1 merges. Phase 2 may
require a new API endpoint (`/api/jobs/{id}/log`) — gates independently from Phase 3/4.

---

## Phase 1: UX Foundation

### Item 1 — Plain-English KPI Tooltips

**What:** Add `title` tooltip and an optional `?` icon to every `HeroCard` and resource gauge label
that explains the metric in plain English.

**Why:** ARIA design rule: *"Explain like I'm 5. If a user needs domain expertise to interpret a
chart, the chart has failed."*

**Implementation:**
- `HeroCard` receives an optional `tooltip` prop (string)
- Rendered as `<span title={tooltip}>` on the metric label and as a `?` icon with `aria-label`
- ResourceGauges: each gauge label gets a `title` attribute explaining the metric

**Tooltips to write:**

| Metric | Tooltip Text |
|--------|-------------|
| Jobs/24h | "Total jobs completed in the last 24 hours. Rising = queue is healthy. Falling = daemon may be stalled or paused." |
| Avg Wait | "Average time a job spends waiting in the queue before the daemon starts it. Spikes mean the daemon was busy or paused during that window." |
| Pause Time | "Total minutes the daemon spent paused in the last 24 hours. High values mean frequent health-triggered or manual pauses." |
| Success Rate | "Percentage of completed jobs that succeeded vs. failed. Below 90% warrants investigation of failed jobs in History." |
| RAM % | "System RAM in use. Above the pause threshold, the daemon stops accepting new jobs to protect stability." |
| VRAM % | "GPU memory in use by Ollama. Near 100% causes model loading failures. This is the most common resource bottleneck." |
| Load Avg | "1-minute system load average. Values above CPU count indicate the system is overloaded." |
| Swap % | "Swap (disk memory) in use. Any non-zero swap on a machine with adequate RAM signals memory pressure." |

### Item 2 — First-Run Guided Overlay

**What:** On first load (localStorage key `oq_onboarded`), display a 5-step modal overlay that
explains each tab's purpose. User can dismiss at any step with "Got it."

**Implementation:**
- New component `OnboardingOverlay.jsx` — renders a centered modal with backdrop
- Step content: one paragraph + one "Decision this tab drives" line per tab
- Progress dots at bottom (5 steps, current highlighted)
- "Next" / "Got it" buttons — "Got it" at any step sets `localStorage.oq_onboarded = '1'` and closes
- No dependency on app state — renders above everything via a portal

**Step content:**

| Step | Tab | Summary |
|------|-----|---------|
| 1 | Now | "Your real-time command center. Shows what's running right now, what's waiting, and whether the system has the resources to keep working." |
| 2 | Plan | "Your 24-hour schedule. Recurring jobs, estimated run windows, and conflict detection. Use this to understand when your jobs will run." |
| 3 | History | "Everything that already ran. Failed jobs, duration trends, and GPU activity. Use this to investigate problems and measure performance." |
| 4 | Models | "What Ollama has installed and what's available to download. Use this when a job fails because a model is missing." |
| 5 | Settings | "Health thresholds, retry behavior, stall detection, and daemon controls. Change these when the system is pausing too aggressively or not aggressively enough." |

### Item 3 — Contextual Empty States with CTAs

**What:** Replace generic "idle" / empty states with actionable empty-state components.

**Affected states:**

| Location | Current | New |
|----------|---------|-----|
| CurrentJob idle | "Ready — waiting for jobs to run" badge | "Ready. Nothing in queue. [+ Submit a job]" with button |
| QueueList empty | Nothing | "Queue is empty — jobs you submit will appear here." |
| HistoryList empty | Nothing | "No history yet. Run your first job to see results here." |
| DLQ empty | Badge shows 0 | No badge shown (silence is health) |
| Models catalog empty | Nothing | "No models found. Check your Ollama connection in Settings." |

**Implementation:** `EmptyState.jsx` — props: `icon`, `headline`, `body`, `action` (optional button config).

### Item 4 — System Health Summary in Sidebar

**What:** A single-line health indicator at the top of the Sidebar and BottomNav that aggregates
daemon state + DLQ count + resource pressure into one signal.

**States:**

| Signal | Condition | Color |
|--------|-----------|-------|
| `● Healthy` | daemon running, DLQ=0, no resource warning | `--status-healthy` |
| `● 1 Warning` | resource at warning tier OR DLQ=1-3 | `--status-warning` |
| `● N Issues` | daemon paused OR DLQ>3 OR resource at pause tier | `--status-error` |

**Implementation:**
- New `SystemHealthChip.jsx` — pure computed signal from `daemonState`, `dlqCount`, `latestHealth`, `settings`
- Replaces the existing daemon status chip at top of Sidebar
- On mobile: added above BottomNav as a thin strip

### Item 5 — "Submit Job" as Primary Sidebar Action

**What:** Add a `[+ Submit]` button permanently visible in the Sidebar (below nav items) and as a
floating action button on mobile (above BottomNav).

**Implementation:**
- Sidebar: add a `<button class="t-btn">+ Submit</button>` at the bottom of the nav list
- Mobile: FAB with `position: fixed; bottom: 80px; right: 16px; z-index: 50`
- Clicking either opens the existing `SubmitJobModal`
- Both are hidden when `daemon.state === 'error'` (can't submit if daemon is down)

### Item 6 — Priority Selector with Named Levels

**What:** Replace the priority number input in `SubmitJobModal` and `AddRecurringJobModal` with a
segmented control: `Critical · High · Normal · Low · Background`.

**Mapping:**

| Label | Value Range | Color |
|-------|------------|-------|
| Critical | 1 | `--status-error` |
| High | 3 | `--status-warning` |
| Normal | 5 | `--accent` |
| Low | 7 | `--text-tertiary` |
| Background | 9 | `--text-tertiary` at 60% opacity |

**Implementation:**
- New `PrioritySelector.jsx` — renders 5 labeled pills, selected one gets border highlight
- Internal value is still numeric (submitted to API as `priority: number`)
- Existing API unchanged

### Item 7 — Estimated Wait Time on Queue Rows

**What:** Each queued job row in `QueueList` shows its ETA as `~3 min` using data already
available in `queueEtas` from the Plan tab store signal.

**Implementation:**
- Read `queueEtas` signal in `QueueList` (already in global store)
- Match by job ID to get `eta_seconds`
- Render as `<span class="data-mono" style="color: var(--text-tertiary);">~{formatDuration(eta)}</span>` on the right side of each row
- If no ETA available, render nothing (don't show "—")

### Item 16 — Keyboard Shortcuts

**What:** Number keys `1`–`5` navigate to Now/Plan/History/Models/Settings.

**Implementation:**
- Single `useEffect` in `app.jsx` attaches `keydown` listener
- Guard: skip if focus is inside an `<input>`, `<textarea>`, or `[contenteditable]`
- `currentTab.value = ['now','plan','history','models','settings'][key - 1]`
- Keyboard shortcut legend in Settings tab: small grey `Keyboard shortcuts: 1–5 to switch tabs`

### Item 20 — Consumer Tab Explanation Header

**What:** Add a 1-sentence explanation header to the Consumers tab.

**Implementation:**
- `Consumers.jsx` — add a `<p>` above the consumer table: *"Consumers are services or scripts that
  submit jobs. Each consumer gets its own priority lane and is tracked separately for rate limiting
  and history."*
- Styled as `style="color: var(--text-secondary); font-size: var(--type-body); margin-bottom: 1rem;"`

---

## Phase 2: Interaction Depth

### Item 8 — Retry Button on Failed History Entries

**What:** One-click re-queue on expandable history rows for failed/killed jobs.

**API:** `POST /api/jobs` with same `source`, `model`, `prompt`, `priority` from the history row.

**Implementation:**
- `HistoryList.jsx` expanded row — add `[↺ Retry]` button for rows where `status === 'failed' || status === 'killed'`
- On click: call store `retryJob(jobId)` which fetches job params from `/api/jobs/{id}` then POSTs to `/api/jobs`
- Success: show 2s toast "Requeued — job added to queue"
- Failure: show error toast

### Item 9 — Quick-Cancel with 5s Undo Window

**What:** Cancel from `QueueList` shows a 5s countdown toast instead of instant removal.
Shatter fires only after undo expires.

**Implementation:**
- `QueueList.jsx` — on cancel click, set `pendingCancel = { jobId, timeout }` in local state
- Show toast: `"Cancelled. Undo?"` with countdown bar
- Undo button calls `clearTimeout` + removes from `pendingCancel`
- On timeout: fire actual `DELETE /api/jobs/{id}` + trigger shatter on the row ref

### Item 10 — Live Log Tail in CurrentJob

**What:** Collapsible section in `CurrentJob` showing last 5 lines of running job stdout.

**API:** New endpoint `GET /api/jobs/{id}/log?tail=5` — reads from Ollama response stream buffer.

**Implementation:**
- Python: `GET /api/jobs/{id}/log` — returns `{ lines: string[] }` from job's stdout buffer in DB or in-memory ring buffer
- `CurrentJob.jsx` — add `<CollapsibleSection label="Output">` below progress bar
- Polls every 5s (same interval as main status poll) when job is running
- Shows monospace lines or "No output yet" if empty

### Item 11 — Settings Restart-Required Banner

**What:** Persistent amber banner after changing daemon-affecting settings until daemon restarts.

**Implementation:**
- `SettingsForm.jsx` — track which fields require restart: `['concurrency', 'stall_threshold_seconds', 'burst_detection_enabled']`
- On save of any restart-required field, set `restartRequired` signal to true
- Banner: `⚠ Daemon restart required for these changes to take effect. [Restart daemon]`
- `[Restart daemon]` calls existing daemon restart API endpoint
- Banner clears when daemon state transitions through `restarting → running`

### Item 12 — Stall Resolution Guidance

**What:** The `⚠ frozen` badge in `CurrentJob` gets an expandable tooltip with concrete steps.

**Tooltip content:**
```
Job appears frozen — not producing output.

Options:
1. Wait 2 more minutes — some models are slow to start
2. Cancel and retry — click × in the queue
3. Check Ollama: run "ollama ps" in terminal to verify model is loaded
4. Restart daemon from Settings if Ollama itself is stuck
```

**Implementation:**
- Replace the frozen `<span>` with a `<details>` element styled as an inline tooltip
- CSS: `summary { cursor: pointer; }` — click to expand guidance

### Item 13 — DLQ Quick-Actions in Now Alert Strip

**What:** The DLQ alert strip in `Now.jsx` gets `[View failed] [Dismiss all]` inline buttons.

**Implementation:**
- Alert strip currently links to History tab
- Add `[View failed]` → navigates to History tab + opens DLQ section
- Add `[Dismiss all]` → calls `DELETE /api/dlq/all` (batch acknowledge)
- Both styled as compact `.t-btn` at 0.75rem

### Item 14 — Performance Tab Explanation Headers

**What:** Each chart in `Performance.jsx` gets a 1-sentence header explaining what it shows and
what action it drives.

**Headers to add:**

| Chart | Header |
|-------|--------|
| Duration trend | "How long jobs take to run over time. Spikes indicate model loading delays or system pressure." |
| Wait time trend | "How long jobs wait before starting. Rising trends mean the queue is backing up faster than the daemon can drain it." |
| Throughput | "Jobs completed per hour. Use this to predict capacity for batch workloads." |

### Item 15 — Queue Position Indicator per Source

**What:** When a source has multiple jobs queued, each row shows `#2 of 3` position badge.

**Implementation:**
- `QueueList.jsx` — group queue items by source, compute per-source position index
- Render `<span>#2</span>` in `--text-tertiary` monospace on rows where source count > 1

### Item 17 — Hover Detail on Gantt Bars

**What:** Hovering a Gantt bar in `Plan.jsx` shows a tooltip with job metadata.

**Tooltip fields:** source, model, priority label, estimated start, estimated end, ETA confidence.

**Implementation:**
- `GanttChart.jsx` — add `onMouseEnter`/`onMouseLeave` handlers on each bar `<div>`
- Tooltip: absolutely positioned `<div class="t-frame">` with the fields listed above
- Position: follow cursor with 16px offset, clamp to viewport edges

### Item 18 — Copy Output Button on History Rows

**What:** Expanded history rows get a `[⎘ Copy output]` button that writes job output to clipboard.

**Implementation:**
- `HistoryList.jsx` expanded row — add button calling `navigator.clipboard.writeText(job.output)`
- On success: button label changes to `✓ Copied` for 2s then reverts
- Only shown when `job.output` is non-empty

### Item 19 — Mobile Composite Notification Badge

**What:** Bottom nav shows a composite `N issues` badge combining DLQ + stall + health warnings.

**Implementation:**
- `BottomNav.jsx` — compute `issueCount = dlqCount + (isStalled ? 1 : 0) + healthWarningCount`
- Replace per-tab DLQ badge with a single red badge on the History icon (most relevant tab)
- If `issueCount === 0`: no badge shown

---

## Phase 3: Superhot Philosophy

### Item 21 — Freshness on All Queue Rows

**What:** Apply `<ShFrozen>` wrapper to each `QueueList` row based on `enqueued_at` timestamp.
Time-in-queue is data; it should look like data.

**States:** fresh (<5 min) · cooling (5-30 min) · frozen (30-60 min) · stale (>60 min)

**Implementation:**
- `QueueList.jsx` row wrapper: `<ShFrozen timestamp={job.enqueued_at}>...</ShFrozen>`
- Import from `superhot-ui/preact`
- Jobs waiting >30 min look visually degraded — urgency communicated without a badge

### Item 22 — ShFrozen on DLQ Entries

**What:** DLQ rows in `History.jsx` apply `<ShFrozen>` based on `failed_at` timestamp.

**Thresholds for DLQ:**

| State | Age |
|-------|-----|
| fresh | < 1h |
| cooling | 1–6h |
| frozen | 6–24h |
| stale | > 24h |

**Implementation:** `HistoryList.jsx` DLQ section — wrap each row in `<ShFrozen timestamp={entry.failed_at} thresholds={{...}} />`.

### Item 23 — Time-Aware Gantt: Past = Frozen, Future = Alive

**What:** In `GanttChart.jsx`, bars for jobs whose `end_time < now` desaturate to grey (frozen
aesthetic). Future bars remain vibrant.

**Implementation:**
- `GanttChart.jsx` — compute `isPast = job.end_time < Date.now() / 1000`
- Past bars: `filter: saturate(0.2) opacity(0.6)` via inline style
- Current-window bar (started but not ended): add `data-sh-effect="threat-pulse"` if overrun

### Item 24 — "PAUSED" Mantra on Daemon-Paused State

**What:** When daemon is paused, `CurrentJob` card shows the `PAUSED` mantra watermark.

**Implementation:**
- `CurrentJob.jsx` — add a second `useEffect` parallel to the `RUNNING` mantra effect
- `applyMantra(cardRef.current, 'PAUSED')` when `isPaused === true`
- `removeMantra` when transitioning back to running or idle
- Both mantra effects cannot be active simultaneously — `isRunning` and `isPaused` are mutually exclusive

### Item 25 — "OFFLINE" Mantra on Disconnection

**What:** When `connectionStatus === 'disconnected'`, apply `OFFLINE` mantra to the Now page root.

**Implementation:**
- `Now.jsx` — add `nowRef = useRef()` on root div
- `useEffect` watching `connectionStatus`: apply/remove `OFFLINE` mantra on the root element
- Mantra appears as a large ghosted watermark behind all Now content — data is unavailable, UI tells you

### Item 26 — Glitch on Daemon Status Chip During Disconnection

**What:** The daemon status chip (`SystemHealthChip` in Sidebar) gets `data-sh-effect="glitch"` when
`connectionStatus === 'disconnected'`.

**Implementation:**
- `SystemHealthChip.jsx` or `Sidebar.jsx` — conditional `data-sh-effect` attribute on chip element
- Glitch fires once on disconnection transition; re-fires every 10s while still disconnected

### Item 27 — Glitch Burst on `killed` State Transition

**What:** `StatusBadge` already glitches on `failed`. Extend to `killed` with higher intensity.

**Implementation:**
- `StatusBadge.jsx` — add `killed` to the glitch-trigger state list
- `data-sh-intensity="high"` on killed (same as failed), differentiated from error by color

### Item 28 — Shatter on DLQ Row Dismiss

**What:** Dismissing/acknowledging a DLQ entry triggers shatter on the row element.

**Implementation:**
- `HistoryList.jsx` DLQ section — add `rowRef` to each row
- On dismiss click: `shatterElement(rowRef.current, { onComplete: () => removeDlqEntry(id) })`
- API call happens in `onComplete` callback — shatter is the visual confirmation

### Item 29 — Shatter Cascade on "Clear All Completed" in History

**What:** A "Clear all completed" batch action in History triggers sequential shatter across rows.

**Implementation:**
- `History.jsx` — add `[Clear completed]` button (only when completed jobs list is non-empty)
- On click: stagger `shatterElement` calls across row refs with 80ms delay between each
- After last shatter completes: call `DELETE /api/jobs/completed/all`

### Item 30 — Three-State ThreatPulse on ResourceGauges

**What:** ResourceGauges pulses at two thresholds: warning (amber, 70% default) and critical
(red, pause threshold).

**Implementation:**
- `ResourceGauges.jsx` — compute warning tier: `isWarning = value >= warningThreshold && value < pauseThreshold`
- `isWarning`: `data-sh-effect="threat-pulse"` with `data-sh-color="warning"` (amber pulse)
- `isCritical`: existing red pulse behavior (already implemented)
- Both effects time out after 3s per existing pattern

### Item 31 — ThreatPulse on KPI Degradation

**What:** If Success Rate drops below 80% (configurable), the Success Rate HeroCard pulses.

**Implementation:**
- `Now.jsx` — compute `isSucessRateLow = successRate < 0.8` from store signal
- Apply `data-sh-effect="threat-pulse"` to the Success Rate HeroCard ref when condition is met
- Re-fires every 30s while condition persists (not on every poll — prevents pulse fatigue)

### Item 32 — PageBanner Audit — Uniform Coverage

**What:** Verify every tab has a `.page-banner-sh` PageBanner component. Fill gaps.

**Audit:**

| Tab | PageBanner | Label |
|-----|------------|-------|
| Now | ✅ (added in PR #103) | "NOW" |
| Plan | Verify | "PLAN" |
| History | Verify | "HISTORY" |
| Models | Verify | "MODELS" |
| Settings | Verify | "SETTINGS" |
| Consumers | Likely missing | "CONSUMERS" |
| Eval | Likely missing | "EVAL" |
| Performance | Likely missing | "PERFORMANCE" |

**Implementation:** Add `<PageBanner label="CONSUMERS" />` etc. where missing.

### Item 33 — CRT Scanlines on Modal Dialogs

**What:** `SubmitJobModal` and `AddRecurringJobModal` apply `.sh-crt` class to their root element
so CRT scanlines appear inside modals — modals feel like part of the terminal, not HTML popups.

**Implementation:**
- Both modal components — add `class="sh-crt"` to the modal root `<div>`
- Verify `superhot-ui`'s `.sh-crt` CSS doesn't conflict with modal `z-index` or backdrop

---

## Phase 4: Visualization Science

### Item 34 — Non-Color Priority Discriminator (Treisman)

**What:** Queue rows and Plan table rows use left-border thickness as a second encoding channel
for priority — colorblind-safe.

**Border widths:**

| Priority | Thickness |
|----------|-----------|
| Critical (1-2) | 4px |
| High (3-4) | 3px |
| Normal (5-6) | 2px |
| Low (7-8) | 1px |
| Background (9-10) | 1px, 40% opacity |

**Implementation:**
- `QueueList.jsx` — `priorityBorderWidth(priority)` helper, applied as inline `border-left-width`
- `Plan/index.jsx` table rows — same helper

### Item 35 — Progressive Disclosure on QueueList Rows (Shneiderman)

**What:** Queue rows default to compact view (source + model + priority). Click/hover expands to
show full params, enqueue time, estimated duration, retry count.

**Implementation:**
- `QueueList.jsx` — local `expandedId` state
- Click on row body toggles expand (click on × still cancels)
- Expanded section: monospace key-value pairs for params, enqueue time, retries
- Collapse on second click or on outside click

### Item 36 — Hover Tooltip on Heatmap Cells (Shneiderman)

**What:** Each of the 168 cells in `ActivityHeatmap` shows a tooltip on hover: date/hour label +
exact GPU usage %.

**Implementation:**
- `ActivityHeatmap.jsx` — add `onMouseEnter`/`onMouseLeave` on each grid cell `<div>`
- Tooltip: small `<div>` positioned with fixed offset from cursor
- Format: `"Wed 14:00 — 73% GPU"` or `"No data"` for empty cells
- Performance: tooltip rendered in a single portal `<div>` at root, content updated on enter

### Item 37 — Sparklines on All HeroCards (Tufte)

**What:** Tufte: *"A number without trend is half a story."* Audit all 4 KPI HeroCards for missing
sparklines. Add where store data is available.

**Audit:**

| KPI | Sparkline | Data Source |
|-----|-----------|-------------|
| Jobs/24h | Likely present | `durationData` job counts bucketed by hour |
| Avg Wait | Verify | `durationData` wait_seconds trend |
| Pause Time | Likely missing | `durationData` paused_seconds bucketed by hour |
| Success Rate | Likely missing | `durationData` success count / total per hour |

**Implementation:** Where missing — compute bucket from `durationData` signal, pass to `HeroCard.sparkData`.

### Item 38 — `data-chroma` Semantic Audit

**What:** All `.t-frame` cards must have `data-chroma` attributes matching the semantic mapping
from the design guide.

**Required mapping (from design guide Appendix D):**

| Card / Section | data-chroma |
|----------------|-------------|
| CurrentJob | gustave (operational) |
| QueueList | gustave (operational) |
| ResourceGauges | lune (analytical) |
| HeroCard — Jobs/24h | lune |
| HeroCard — Avg Wait | lune |
| HeroCard — Pause Time | maelle (failure signal) |
| HeroCard — Success Rate | maelle |
| DLQ section | maelle |
| Settings — Health Thresholds | lune |
| Settings — Defaults | gustave |
| Settings — Retention | sciel |
| Settings — Retry | maelle |
| Settings — Stall Detection | maelle |
| Settings — Concurrency | lune |
| Settings — Daemon Controls | gustave |

**Implementation:** Audit each component JSX for `data-chroma` presence; add where missing.

### Item 39 — Animation Tier Audit (T1/T2/T3)

**What:** Every animation in the app must be tagged to its tier and respect `prefers-reduced-motion`.

**Tier definitions:**

| Tier | Type | Disabled on |
|------|------|------------|
| T1 | Ambient (scan beams, breathing) | Mobile AND prefers-reduced-motion |
| T2 | Data refresh (state transitions, progress bar) | prefers-reduced-motion only |
| T3 | Status alert (shatter, glitch, threat-pulse) | Never disabled (safety signal) |

**Implementation:**
- Audit `index.css` and all component inline animations
- Wrap T1 in `@media (prefers-reduced-motion: no-preference) and (min-width: 768px)`
- Wrap T2 in `@media (prefers-reduced-motion: no-preference)`
- T3 has no media query guard (alerts must still fire)

### Item 40 — `@starting-style` Tab Entrance Animations

**What:** When switching tabs, content animates in using CSS `@starting-style`. *Time moves when
you move.* The world was frozen while you were elsewhere — new tab content comes alive.

**Animation:** Simple opacity fade (0 → 1) + slight upward translate (8px → 0) over 200ms.

**Implementation:**
- Each page component root gets `class="tab-enter"` on mount
- `index.css`:
  ```css
  @starting-style {
    .tab-enter { opacity: 0; transform: translateY(8px); }
  }
  .tab-enter {
    opacity: 1;
    transform: translateY(0);
    transition: opacity 0.2s ease, transform 0.2s ease;
  }
  ```
- Works in Chrome 117+/Safari 17.5+; falls back gracefully (no animation, no layout shift)
- Respect prefers-reduced-motion: skip transition when set

---

## Testing Strategy

| Phase | Test approach |
|-------|--------------|
| 1 | Playwright smoke tests for empty states, overlay dismissal, keyboard navigation |
| 2 | Unit tests for retry/undo logic; API mock tests for log endpoint |
| 3 | Playwright visual checks that `.sh-mantra` and `.sh-crt` classes are applied in correct states |
| 4 | Playwright hover tests for heatmap tooltip, Gantt tooltip; `@starting-style` in browser only (no jsdom) |

## Reference

- ARIA design language: `~/Documents/projects/ha-aria/docs/design-language.md`
- superhot-ui: `~/Documents/projects/superhot-ui/README.md`
- LLM design guide: `ollama_queue/docs/llm-guide-design-system.md`
- Cleveland & McGill (1984) perceptual accuracy hierarchy — position > length > angle > color
- Treisman & Gelade (1980) — preattentive processing, max 3-4 color channels
- Shneiderman (1996) — overview first, zoom/filter, details on demand
- Tufte (2006) — sparklines: a number without trend is half a story
- Miller (1956) — 7±2 working memory chunks
