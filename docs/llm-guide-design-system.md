# ollama-queue Design System Guide

> **For LLMs:** Read this alongside the base ui-template design doc and the expedition33 theme guide before modifying any dashboard file. This document maps every queue concept to a specific design system component, mood, and metaphor.

**Dashboard:** `ollama_queue/dashboard/spa/src/`
**CSS entry:** `src/index.css` (imports `tailwindcss`, `superhot-ui`, `uPlot`)
**Tech:** Preact 10, @preact/signals, Tailwind v4, esbuild, uPlot
**Theme:** SUPERHOT terminal aesthetic — VT323 pixel font (Google Fonts), `.page-banner-sh` CRT headers, `superhot-ui` JS effects
**Polling:** 5s status, 60s non-realtime (health, durations, heatmap, history)

---

## 1. Project Context

### Pipeline Position

```
ui-template (base)        -> CSS token system, component manifest, animation library
  +-- superhot-ui (theme) -> SUPERHOT terminal aesthetic, CRT effects, JS animation hooks
        +-- ollama-queue (app) -> domain-specific components, queue-aware compositions
```

**ui-template** provides the three-layer token architecture (primitives → semantic → component), CSS custom properties, and the animation library.

**superhot-ui** provides the SUPERHOT terminal aesthetic: near-black terminal surfaces, red accent (`--accent`), VT323/monospace typography, and four JS effect hooks:
- `applyFreshness(el, ts, thresholds)` — color-shifts element based on age (fresh → cooling → frozen → stale)
- `shatterElement(el, opts)` — fragment animation on removal (cancel actions)
- `glitchText(el, opts)` — digital glitch burst on error state transitions
- `applyMantra(el) / removeMantra(el)` — repeating "SUPER HOT" scanline overlay on active state
- `data-sh-effect="threat-pulse"` attribute — sustained red glow pulse for critical states (DLQ rows, resource threshold breach)

Installed via `file:` dependency in `package.json`. Import JS effects from `'superhot-ui'` (not subpaths).

**ollama-queue** is the application layer. It consumes `superhot-ui` via `@import "../node_modules/superhot-ui/css/superhot.css"` in `index.css` and adds:
- App-specific layout tokens (`--sidebar-width`, `--font-mono` override with VT323 first)
- `PageBanner` component: `.page-banner-sh` CRT terminal headers with glow animation and scan beam
- Light/dark mode toggle: `localStorage`-backed `data-theme` attribute on `<html>`; `[data-theme="light"]` token overrides in `index.css`
- Domain components (ResourceGauges, GanttChart, ActivityHeatmap)
- Queue-specific CSS patterns (`.now-grid`, `.history-top-grid`, cursor system, tiered animations)

### Eight Tab Pages

| Tab | Route ID | View Component | Purpose |
|-----|----------|---------------|---------|
| Now | `now` | `pages/Now.jsx` | Real-time command center: running job, queue, resource gauges, KPI cards, alert strip |
| Plan | `plan` | `pages/Plan/index.jsx` | 24h Gantt timeline, recurring job table with inline editing, rebalance controls |
| History | `history` | `pages/History.jsx` | DLQ entries, duration trend charts, activity heatmap, completed job list |
| Models | `models` | `pages/ModelsTab.jsx` | Installed model table (sortable), model catalog search, download/pull progress |
| Performance | `performance` | `pages/Performance.jsx` | Model benchmarks, performance curve chart, load heatmap, system health |
| Settings | `settings` | `pages/Settings.jsx` | Health thresholds, defaults, retention, retry, stall detection, concurrency, daemon controls |
| Eval | `eval` | `pages/Eval.jsx` | A/B prompt evaluation runs, variants, trends, and eval settings |
| Consumers | `consumers` | `pages/Consumers.jsx` | Detected Ollama callers, config patching, iptables intercept mode |

### Layout System

**Desktop (>=1024px):** Fixed 200px sidebar (`layout-sidebar`) + main content area (`layout-main`) offset by sidebar width. Sidebar contains: daemon status chip at top + 8 nav buttons (DLQ badge on History) + theme toggle button at bottom (`.theme-toggle` class).

**Theme toggle:** `☀ Light` / `◗ Dark` button at the bottom of the sidebar. Reads/writes `localStorage('queue-theme')`, sets `document.documentElement.setAttribute('data-theme', ...)`. `[data-theme="light"]` overrides background/text/border tokens in `index.css` with warm cream paper aesthetic.

**Tablet (768-1023px):** Collapsed 64px icon-only sidebar (`.sidebar-label` hidden).

**Mobile (<768px):** Sidebar hidden. Bottom tab bar (`mobile-bottom-nav`) shown. Main content has no left margin, extra bottom padding.

**Now page grid:** `now-grid` is `grid-template-columns: minmax(0, 2fr) minmax(0, 3fr)` on desktop, single column on mobile. Left = operations (CurrentJob + QueueList), right = alerts + ResourceGauges + 2x2 KPI cards.

**History page grid:** `history-top-grid` is equal two-column for duration trends + activity heatmap, single column on mobile.

### Real-Time Polling Architecture

5-second poll interval (configurable via `settings.poll_interval_seconds`). `fetchStatus()` polls `/api/status` every 5s. Every 12th poll (~60s) also fetches health, durations, heatmap, history. Exponential backoff on failure (up to 30s). Connection status tracked as `'ok'` or `'disconnected'` (after 3 consecutive failures).

### Current UI Pattern Vocabulary

| Pattern | CSS Class | Where Used |
|---------|----------|------------|
| ASCII-framed card | `.t-frame` with `data-label`/`data-footer` | Universal card: CurrentJob, QueueList, ResourceGauges, HeroCard, DLQ, Heatmap, HistoryList, Settings, model cards |
| Terminal status badge | `.t-status` + `.t-status-healthy/warning/error/waiting` | StatusBadge (daemon/job states) |
| Monospace data | `.data-mono` (maps to `var(--font-mono)`) | All numeric values, source names, model names, timestamps |
| Priority colors | `priorityColor()` / `PRIORITY_COLORS` / `CATEGORY_COLORS` | QueueList border-left, Plan table row borders |
| Hero metric card | `HeroCard` (`.t-frame` + `cursor-active/working`) | 2x2 KPI grid: Jobs/24h, Avg Wait, Pause Time, Success Rate |
| Resource gauge bars | `ResourceGauges` (inline div bars) | Now page (standalone + inside CurrentJob) |
| Cursor state indicators | `.cursor-active` (1s blink), `.cursor-working` (0.5s), `.cursor-idle` (2s) | HeroCard loading/active state |
| CRT page banner | `PageBanner` component → `.page-banner-sh` | Top of every page — title in VT323 with phosphor glow, scan beam sweep, CRT scanlines |
| SUPERHOT freshness | `applyFreshness()` via `FreshRow` wrapper | QueueList rows — color shift based on job age |
| SUPERHOT shatter | `shatterElement()` in `cancelJob()` | Job cancel — fragment animation on FreshRow container |
| SUPERHOT glitch | `glitchText()` in `StatusBadge` | Error state entry transition (failed/killed) |
| SUPERHOT mantra | `applyMantra()` / `removeMantra()` in `CurrentJob` | Active running job — "SUPER HOT" scanline overlay |
| ThreatPulse | `data-sh-effect="threat-pulse"` | DLQ rows (always on mount), ResourceGauges (at threshold), CurrentJob stall |
| Model badge | `ModelBadge` (profile + typeTag pills) | Plan model column, Models installed table, catalog cards |
| Sparkline | `TimeChart` (uPlot thin line) | HeroCard sparklines, History duration trends |
| Heatmap | `ActivityHeatmap` (7x24 CSS grid) | History GPU activity |
| Gantt chart | `GanttChart` (absolute-positioned bars + density strip) | Plan 24h timeline |
| Animation tiers | `t1-*` (ambient), `t2-*` (data refresh), `t3-*` (status alert) | Responsive degradation: phone=T1 off, reduced-motion=T1+T2 off |

### Priority System

| Priority | Category | Current Color | Token |
|----------|----------|---------------|-------|
| 1-2 | critical | `#ef4444` / `var(--status-error)` | `--status-error` |
| 3-4 | high | `#f97316` / `var(--status-warning)` | `--status-warning` |
| 5-6 | normal | `#3b82f6` / `var(--accent)` | `--accent` |
| 7-8 | low | `#6b7280` / `var(--text-tertiary)` | `--text-tertiary` |
| 9-10 | background | `#374151` / `var(--text-tertiary)` | `--text-tertiary` at reduced opacity |

**Note:** `QueueList.jsx` uses hardcoded hex; `Plan.jsx` uses CSS custom properties. The Plan approach is correct. Migration should standardize on `var()` tokens.

### Daemon States

| State | Sidebar Icon | StatusBadge Class | Sidebar Color |
|-------|-------------|-------------------|---------------|
| `running` | `>` | `t-status-healthy` | `var(--status-healthy)` |
| `idle` | `o` | `t-status-healthy` | `var(--text-tertiary)` |
| `paused_health` | `||` | `t-status-warning` | `var(--status-warning)` |
| `paused_manual` | `||` | `t-status-warning` | `var(--status-warning)` |
| `paused_interactive` | `||` | `t-status-warning` | `var(--status-warning)` |

---

## 1.5 Strategy Stack

ollama-queue is an infrastructure operations tool. The user's core question: **"Are my AI jobs running?"** Every design decision serves confidence in the pipeline.

### UX Strategies (Mission Plan)

| Strategy | Priority | Application |
|----------|----------|-------------|
| **Outcome-Driven** | Primary | The outcome is "job submitted and completed reliably." Design backward: submit → confirm queued → monitor → see result. If it doesn't improve that path, cut it. |
| **Friction Reduction** | Primary | Submit a job and forget about it. Minimal required fields. Smart defaults (priority=0, source="proxy", timeout=120). The daemon handles everything — the UI confirms it's working. |
| **Trust & Predictability** | High | Daemon health visible at all times (sidebar chip). Resource gauges show system state. DLQ doesn't hide failures — it surfaces them with retry options. Predictable 5s polling cycle. |
| **Context-Aware** | Medium | Mobile quick-check (is the daemon running? what's in queue?) vs desktop deep-ops (Gantt scheduling, model catalog, settings tuning). Bottom tab bar on mobile, full sidebar on desktop. |
| **Anticipatory** | Medium | Smart scheduling suggests optimal times based on load map. Recurring jobs auto-promote. Stall detection catches stuck jobs before the user notices. |

### UI Strategies (Weapon System)

| Strategy | Priority | Application |
|----------|----------|-------------|
| **Clarity-First** | Primary | Now page: running job + queue = left column; health + KPIs = right column. One glance tells you everything. MonolithDisplay for queue depth. |
| **Action-Oriented** | Primary | Strong submit flow. Clear daemon controls (pause/resume). DLQ retry is one click. Every screen answers "what can I do right now?" |
| **Feedback-Rich** | High | 5s polling with visual refresh. Job state transitions animate. CanvasSaved on completion. CrossingOut → DLQ on failure. Resource gauges pulse at thresholds. |
| **Gamified** | Low | Battle metaphor (job=unit, queue=turn order, completion=victory, failure=unit falls). Only where it genuinely aids mental model — the queue IS a turn-based system. |

### Behavioral Target

**What behavior are we engineering?** Fire-and-forget confidence. The user submits work, trusts the queue to serialize and schedule it, checks back occasionally to confirm health. The worst outcome is a silently stuck pipeline or lost jobs.

---

## 2. Queue Concept -> Design System Component Mapping

| Queue Concept | Design System Component | Chroma | Data Attributes |
|---------------|------------------------|--------|-----------------|
| Running job | BattlePanel (player side) + StatBar progress | gustave (active gold) | `data-side="player"`, `data-bar="hp"` |
| Queued job | TurnSlot in TurnQueue | verso (waiting navy) | `data-active=false` |
| Failed job (DLQ) | HUDFrame + InkSplatter animation | maelle (error crimson) | `data-mood="dread"` |
| Completed job | CanvasSaved animation flash | gustave (gold) | `data-paint-state="dried"` |
| Priority: critical (1-2) | GlyphBadge with glow | maelle (crimson) | `data-status="error"` |
| Priority: high (3-4) | GlyphBadge | enemy (moss/warning) | `data-status="warning"` |
| Priority: normal (5-6) | GlyphBadge | gustave (gold) | `data-status="healthy"` |
| Priority: low (7-8) | GlyphBadge muted | verso (navy) | `data-status="waiting"` |
| Resource gauge: RAM | StatBar (HP variant) with threshold marker | lune (azure) | `data-bar="hp"` |
| Resource gauge: VRAM | StatBar (AP variant) with threshold marker | lune (azure) | `data-bar="ap"` |
| Pause threshold marker | ReactionRing (QTE-style) | enemy (danger line) | `data-state="active"` |
| Daemon: running | GlyphBadge healthy + CanvasTexture | gustave (gold) | `data-mood="dawn"` |
| Daemon: paused | GlyphBadge warning + AtmosphereShift | sciel (silver) | `data-mood="nostalgic"` |
| Daemon: offline | GlyphBadge error + VoidErase | maelle (crimson) | `data-mood="dread"` |
| Model catalog entry | JournalCard per model | lune (analytical) | `data-expedition=model_size` |
| Recurring job | FlowerSystem garland decoration | sciel (silver) | `data-tattoo` per schedule |
| DLQ retry action | CrossingOut -> ChromaSpread transition | maelle -> gustave | -- |
| Queue depth display | MonolithDisplay | gustave (gold numeral) | -- |
| 24h Gantt timeline | CommandBar (turn-based layout) | per-priority chroma | `data-active` per slot |
| Duration sparkline | StatBar thin variant | lune (analytical) | -- |
| Health heatmap | Grid of GlyphBadges | color per health level | -- |
| Model pull progress | StatBar + PaintLoading | lune (analytical) | `data-paint-state` progression |
| Stall detection | CrossingOut + DamageFloat | maelle (crimson) | `data-state="active"` |
| Connection lost | VoidErase ambient | maelle (crimson) | `data-mood="dread"` |

### Priority Color Reference

```
Priority 1-2  (critical):   var(--status-error)     muted crimson
Priority 3-4  (high):       var(--status-warning)    warm amber
Priority 5-6  (normal):     var(--accent)            gold
Priority 7-8  (low):        var(--text-tertiary)     deep muted
Priority 9-10 (background): var(--text-tertiary)     deep muted at reduced opacity
```

### Source Color Mapping (Gantt Chart)

```
aria / aria-*:         var(--accent)        gold    -> target chroma: lune (analytical)
telegram / telegram-*: #f97316              orange  -> target chroma: maelle (active interaction)
notion / notion-*:     #a78bfa              purple  -> target chroma: lune (analytical/data processing)  # matches project-hub canonical assignment
unknown/other:         var(--text-tertiary)  muted  -> target chroma: verso (outsider)
```

---

## 3. Tab -> Location/Mood Mapping

| Tab | Location | Mood | Rationale |
|-----|----------|------|-----------|
| **Now** | lumiere (command center) | dawn (healthy) or dread (issues) | The Now tab is home base. When the daemon is running and queue is flowing, dawn conveys operational confidence. When DLQ entries exist or failures are detected (`showAlerts`), shift to dread. The alert strip already uses `var(--status-error)` -- mood shift makes the entire page reinforce urgency. Mood should be dynamic: check `dlqCount > 0 || recentFailures > 0`. |
| **Plan** | continent (expedition map) | wonder | The Plan tab is forward-looking. The Gantt timeline shows the next 24h of scheduled missions. The Continent is unknown territory: each job is a waypoint. Wonder mood fits because planning is optimistic -- mapping the future. Density strip and conflict badges add cartographic wayfinding. |
| **History** | wasteland (corrupted zones) | dread | History is where DLQ entries live, where failed jobs surface their `outcome_reason`, where stall signals are exposed. The wasteland maps directly to the dead-letter queue. Even duration trends and activity heatmap serve forensic purposes. |
| **Models** | continent (armory) | nostalgic | Browsing installed models and the download catalog is contemplative, inventory-like. Nostalgic mood provides warm ambient light for reading model descriptions, comparing sizes. The catalog search is exploration without urgency. |
| **Settings** | lumiere (camp) | nostalgic | Settings is where you tune the system from safety. Health thresholds, retry defaults, stall detection -- all adjustments made calmly. Lumiere + nostalgic reinforces this is the control room, not the battlefield. |

### Mood Implementation Pattern

```jsx
// Tab container applies mood via data attribute
<div data-mood={dlqCount.value > 0 ? 'dread' : 'dawn'} data-location="lumiere">
  <main class="layout-main animate-page-enter">
    {/* Now tab content */}
  </main>
</div>
```

The mood attribute cascades CSS custom property overrides through the expedition33-ui theme system. Set `data-location` and `data-mood` on the `<main>` element wrapper when the active tab changes.

---

## 4. Current -> Target Component Migration

| Current Pattern | File(s) | Target Pattern | Migration Steps |
|-----------------|---------|---------------|-----------------|
| `.t-frame` cards | All pages | HUDFrame (operations), JournalCard (catalog) | Replace class, add corner ornaments for primary frames. HUDFrame supports `data-label`/`data-footer`. |
| `.t-frame[data-label="Current"]` | CurrentJob.jsx | BattlePanel (player side) | Running job = active combatant. BattlePanel provides HP bar (progress), status, resource readouts. Set `data-side="player"`. |
| `.t-frame[data-label="Queue"]` | QueueList.jsx | TurnQueue + TurnSlot per job | Queue = turn order. Each pending job = TurnSlot. Running job = active TurnSlot with glow ring. Priority border = per-slot chroma. |
| `.t-frame[data-label="Failed Jobs"]` | History.jsx | HUDFrame + wasteland section | Wrap DLQ in HUDFrame with `data-mood="dread"`. Add InkSplatter on entry. Retry triggers ChromaSpread (maelle -> gustave). |
| `HeroCard` KPIs (2x2) | Now.jsx | MonolithDisplay (primary) + ExpeditionCounter (secondary) | Jobs/24h, Success Rate = MonolithDisplay (existential numbers). Avg Wait, Pause Time = ExpeditionCounter. |
| `ResourceGauges` | Now.jsx, CurrentJob.jsx | StatBar (HP for RAM, AP for VRAM) | Replace inline div bars with StatBar. Dashed threshold -> ReactionRing marker. |
| Priority color text/borders | QueueList.jsx, Plan.jsx | GlyphBadge with chroma per priority | Replace star rating and colored spans with GlyphBadge. Critical = maelle + glow, high = enemy, normal = gustave, low = verso. |
| `StatusBadge` | CurrentJob.jsx, Sidebar.jsx | GlyphBadge with breathing dot | Replace `.t-status` classes with GlyphBadge providing breathing animation and data-attribute integration. |
| `GanttChart` bars | Plan.jsx | CommandBar + TurnSlot timeline | 24h Gantt = battle timeline. Bars become TurnSlots. Source colors -> per-source ChromaProvider. Conflict badges -> CrossingOut overlays. |
| `ActivityHeatmap` grid | History.jsx | Grid of GlyphBadges | Each 7x24 cell becomes GlyphBadge sized to fill. Opacity encoding maps to color intensity. |
| Alert strip (inline div) | Now.jsx | Banner (notification) | Formalize with Banner component. Calm Technology escalation: StatusDot -> Banner -> Modal. |
| Disconnected banner | Now.jsx | Banner + VoidErase | Replace inline orange div with error-severity Banner. Add VoidErase ambient. Recovery triggers AtmosphereShift. |
| `.animate-page-enter` | Every page | PaintReveal + StaggerEntrance | Replace CSS-only fade+slide with PaintReveal for container, StaggerEntrance for child sections. |
| Basic loading | LoadingState.jsx | EsquieLoader (page), PaintLoading (inline) | Narrative loading: EsquieLoader for full-page, PaintLoading for inline operations. |
| Error display | ErrorState.jsx | InkSplatter + dread mood | Animated error entry with recovery suggestion per Calm Technology. |
| Model pull progress | ModelsTab.jsx | StatBar + PaintLoading | Replace inline div bar with StatBar. PaintLoading during download, CanvasSaved on completion, VoidErase on cancel. |
| Tag filter chips | QueueList, HistoryList | FilterPills (GlyphBadge-derived) | Standardize on GlyphBadge styling with `var(--accent)` active, `var(--bg-inset)` inactive. |
| Inline editable fields | Plan.jsx | Input + CanvasSaved/CrossingOut feedback | Add CanvasSaved golden glow on successful save. CrossingOut on validation failure. |
| Hardcoded `#f97316` | CurrentJob, QueueList, HistoryList, GanttChart, SettingsForm | `var(--status-warning)` | Global replace in inline styles. |
| Hardcoded priority hex in `PRIORITY_COLORS` | QueueList.jsx | `var(--status-*)` tokens | Match `Plan.jsx` `CATEGORY_COLORS` pattern which already uses `var()`. |
| `cursor-*` system | index.css, HeroCard, CollapsibleSection | Keep as-is | Original and effective. Maps to terminal-within-painting metaphor. No migration needed. |
| Three-tier animation system | index.css | Keep and extend | Well-designed with responsive degradation. Extend with game-metaphor animations (PaintReveal for t2, InkSplatter for t3). |

---

## 5. Queue -> Battle Metaphor

| Queue Operation | Battle Parallel | Design Treatment |
|-----------------|----------------|------------------|
| Job queued | Unit enters turn queue | TurnSlot appears in TurnQueue with verso chroma. Entry uses `fade-in-up` stagger. Queue depth = army size. |
| Job starts running | Unit's turn begins | Active TurnSlot gets gustave chroma + glow ring. BattlePanel shows combatant identity (source), weapon (model), health (StatBar HP progress). |
| Job progresses | Combat in progress | StatBar HP fills. `cursor-working` (fast blink). ResourceGauges show combat resource consumption. |
| Job overruns estimate | Extended engagement | StatBar shifts orange. "+Xm over" badge = damage indicator. Orange left border -> critical-pulse. |
| Job completes | Victory | CanvasSaved (golden glow). TurnSlot exits. HistoryRow shows checkmark in healthy green. `data-paint-state="dried"`. |
| Job fails -> retry | Wounded, healing | DamageFloat shows failure reason. CrossingOut marks failure. Re-enters queue with retry badge (battle scar). Exponential backoff = recovery time. |
| Job fails -> DLQ | Unit falls in battle | CrossingOut + DamageFloat. Entry enters dead-letter queue (wasteland). InkSplatter animation. Maelle chroma. |
| DLQ retry | Recovery/resurrection | ChromaSpread from maelle to gustave. Job re-enters active queue. Comfort animation = healing. |
| DLQ dismiss | Burial/memorial | VoidErase (Gommage erasure). Permanent removal. High drama for high stakes. |
| DLQ clear all | Battlefield cleared | VoidErase cascades with stagger. AtmosphereShift from dread to nostalgic. Confirm dialog required. |
| Pause daemon | Battle pause | AtmosphereShift to muted/silver. Sciel chroma. Ambient animations slow/pause. Battlefield goes quiet. |
| Resume daemon | Battle resumes | AtmosphereShift to active. Gustave restores. Dawn mood. Ambient animations resume. |
| Resource at threshold | Critical HP | StatBar critical-pulse. ReactionRing danger marker. Color shifts accent -> warning -> error. |
| Resource exhaustion | HP depleted, auto-retreat | Health system forces pause. Tactical withdrawal to preserve the system. |
| Stall detected | Unit frozen/stunned | Orange stall badge with CrossingOut. Status ailment -- job neither progresses nor fails. Stall signals are diagnostic vitals. |
| Recurring job triggers | Reinforcements arrive | FlowerSystem garland decoration. Pin icon = strategic marker for time slots. |
| Rebalance schedule | Strategic repositioning | Density strip shows load distribution. Rebalance spreads forces to avoid bottlenecks. Success flash confirms. |
| Model loaded | Weapon equipped | `loaded` status dot in green. Model ready for combat. VRAM column = weapon weight. |
| Model pull | Acquiring new weapon | PaintLoading during download. Catalog = armory. Completion = CanvasSaved. Cancel = VoidErase. |
| Gantt conflict | Overlapping turns | Red conflict badge at overlap. Two heavy models cannot both occupy GPU. Tactical warning. |

---

## 6. Composition Examples

### 6.1 Running Job Panel (Now Tab, Left Column)

```
CurrentJob.jsx (current)
t-frame[data-label="Current"]
  div.flex.flex-col.gap-2
    div.flex.items-center.justify-between
      StatusBadge[state="running"]       -> .t-status.t-status-healthy
      span.data-mono                      (source name)
      span.data-mono                      (model name)
      [if stalled] span                   ("stalled" orange badge)
      span.data-mono                      (elapsed / ~estimated)
        [if overrun] span                 ("+Xm over" orange badge)
    div                                   (progress bar track, 4px)
      div                                 (fill: accent or orange if overrun)
    ResourceGauges                        (compact, 4 horizontal bars)
```

Target structure:
```
ChromaProvider[chroma="gustave"]
  BattlePanel[data-side="player"]
    HUDFrame[data-label="Current"]
      header
        GlyphBadge[data-status="healthy", breathing-dot]
        span.data-mono                    (source)
        ModelBadge[profile, typeTag]
        [if stalled] GlyphBadge[data-status="warning"]
      StatBar[data-bar="hp"]              (progress, with ReactionRing at 100%)
      OrnamentDivider[variant="subtle"]
      section
        StatBar[data-bar="hp", label="RAM"]   (ReactionRing at pause threshold)
        StatBar[data-bar="ap", label="VRAM"]  (ReactionRing at pause threshold)
        StatBar[label="Load"]
        StatBar[label="Swap"]
```

Key rules:
- Gustave chroma -- the running job is the guardian actively defending the queue
- Progress bar uses HP variant (health of execution: time remaining to complete)
- Stall elevates to maelle on the badge only (the wound), not the entire panel
- Resources are subordinate to job display -- environment, not actor
- Overrun state (>100% progress) shifts bar to warning color, adds `+Xm over` DamageFloat badge
- Three daemon states render mutually exclusively: running (full panel), paused (badge + reason), idle (badge + "Idle")

### 6.2 Resource Gauge Panel

```
ResourceGauges.jsx (current)
div.flex.gap-3.flex-wrap
  per gauge (RAM, VRAM, Load, Swap):
    div.flex.items-center.gap-1  (min-width: 80px, flex: 1)
      span.data-mono             Label (32px, right-aligned, type-micro, text-tertiary)
      div                        Bar container (flex: 1, 6px, bg-inset, rounded, relative)
        div                      Threshold marker (absolute, left={pause}%, dashed 1px, opacity 0.5)
        div                      Fill bar (width={pct}%, transition 0.3s)
                                   accent     -> below resume threshold
                                   warning    -> between resume and pause thresholds
                                   error      -> at or above pause threshold
      span.data-mono             Value (28px, type-micro, text-secondary, "{pct}%")
```

Target structure:
```
per gauge:
  StatBar[label, data-bar="hp"|"ap"]
    ReactionRing[at pause threshold]     (dashed marker -> QTE-style danger ring)
    fill with tri-state color:
      lune chroma (normal)  -> accent     (below resume)
      enemy chroma (warning) -> warning   (between resume and pause)
      maelle chroma (danger) -> error     (at/above pause)
```

Key rules:
- RAM = HP (system memory health), VRAM = AP (action points / GPU compute budget)
- Load and Swap get secondary StatBars without the HP/AP narrative distinction
- Color transitions are smooth (0.3s ease) to prevent flicker during polling
- Threshold marker is a static reference point -- never animates, always visible
- The compact variant (inside CurrentJob) and standalone variant (in ResourceGauges frame) share the same component -- only container width differs

### 6.3 Queue List (Now Tab, Left Column)

```
QueueList.jsx (current)
t-frame[data-label="Queue", data-footer="Est. total wait: Xm"]
  [if tags] Filter chips (All + per-tag spans)
  div.flex.flex-col.gap-1
    [if currentJob] Row: drag-handle(transparent) + "RUN" chip + source + model + ETA + cancel
    per pending job: Row (draggable): drag-handle + star-rating + source + [retry badge] + model + ETA + cancel
      [if expanded] Command panel: label + command text + timeout/profile
```

Target structure:
```
ChromaProvider[chroma="verso"]
  HUDFrame[data-label="Queue", data-footer="..."]
    FilterPills[tags]
    TurnQueue
      TurnSlot[data-active=true, chroma="gustave"]   (running, not draggable)
        GlyphBadge[label="RUN"]
      TurnSlot[data-active=false, draggable]          (each pending job)
        GlyphBadge[priority-mapped chroma]
      DetailPanel[expandable]                         (command details)
```

Key rules:
- Verso chroma (waiting) -- these jobs are observers, not yet acting
- Running job at position 0 overrides to gustave (graduated from waiting to acting)
- Drag-to-reorder is TurnQueue native behavior; optimistic update + backend persist
- Cancel button fires CrossingOut micro-animation; confirm dialog only for running job
- Tag filter pills use GlyphBadge styling: accent background when active, bg-inset when inactive
- Footer shows estimated total wait for queued items (excludes running job)
- Expanded command panel shows command text, timeout, resource profile

### 6.4 DLQ Entry (History Tab)

```
History.jsx DLQ (current)
t-frame[data-label="Failed Jobs (N)"]
  header: count label + "Retry all" button + "Clear" button
  per entry:
    Left: source #job_id + failure_reason + retry_count
    Right: Retry button + Dismiss button
```

Target structure:
```
ChromaProvider[chroma="maelle"]
  HUDFrame[data-label="Failed Jobs (N)", data-mood="dread"]
    header: count + actions
    AnimatedList
      HUDFrame[per entry, compact]
        InkSplatter[on-entry]
        GlyphBadge[data-status="error"]
        source + reason + TattooStrip[retry_count]
        Retry button -> ChromaSpread (maelle -> gustave)
        Dismiss button -> VoidErase
```

Key rules:
- Maelle chroma (passionate destruction) -- these jobs died in action
- `data-mood="dread"` on section sets wasteland atmosphere
- InkSplatter fires on each DLQ entry's first render (violent paint burst = system error)
- Retry transitions chroma from maelle to gustave (recovery from destruction to guardian)
- Dismiss uses VoidErase (permanent deletion = Gommage erasure, most dramatic delete)
- Clear All requires confirmation before cascading VoidErase across all entries
- Retry All button disables during operation with "Retrying..." label
- DLQ count badge on History sidebar nav uses `t3-badge-appear` + `t3-counter-bump`

### 6.5 Model Catalog Card (Models Tab)

```
ModelsTab.jsx catalog card (current)
t-frame
  name + [if recommended] "rec" badge
  description paragraph
  ModelBadge (profile pill + type tag)
  [if VRAM] VRAM estimate label
  Download/Installed button
```

Target structure:
```
ChromaProvider[chroma="lune"]
  JournalCard
    header: model name + GlyphBadge[recommended, gustave]
    description
    ModelBadge
    VRAM label
    footer: Download button (PaintLoading on pull) / Installed (muted)
```

Key rules:
- Lune chroma (scholar/analyst) -- browsing models is an analytical, contemplative activity
- JournalCard provides reading/catalog aesthetic vs HUDFrame's operational aesthetic
- Recommended badge uses gustave (gold = endorsement, trust)
- Download triggers PaintLoading within the card, CanvasSaved on completion
- Installed state is muted (`t-btn-secondary`, opacity 0.5) -- weapon already in armory
- Model catalog search uses 300ms debounce to prevent excessive API calls
- Cards use grid layout: `repeat(auto-fill, minmax(220px, 1fr))`

### 6.6 KPI Hero Cards (Now Tab, Right Column)

```
Now.jsx KPI grid (current)
div.grid.grid-cols-2.gap-3
  HeroCard[label="Jobs / 24h"]     value + sparkline + delta
  HeroCard[label="Avg Wait"]       value + unit + sparkline + delta
  HeroCard[label="Pause Time"]     value + unit + warning state + sparkline + delta
  HeroCard[label="Success Rate"]   value + unit + warning state + delta
```

Target structure:
```
div.grid.grid-cols-2.gap-3
  MonolithDisplay[label="Jobs / 24h", chroma="gustave"]
    ExpeditionCounter[value] + StatBar[thin sparkline] + delta
  ExpeditionCounter[label="Avg Wait", chroma="lune", inline]
    value + unit + StatBar[thin sparkline] + delta
  ExpeditionCounter[label="Pause Time", chroma="sciel"]
    value + unit (warning shifts to maelle) + StatBar + delta
  MonolithDisplay[label="Success Rate", chroma="gustave"]
    value + "%" (warning shifts to enemy) + delta
```

Key rules:
- Primary metrics (Jobs/24h, Success Rate) use MonolithDisplay -- existential numbers, gold numerals
- Secondary metrics (Avg Wait, Pause Time) use ExpeditionCounter -- important but not primary
- Warning state shifts chroma from healthy to maelle/enemy (metric is in danger)
- Delta text provides narrative context: "queue flowing smoothly", "all completed successfully", "3 jobs failed this week -- check History or DLQ for patterns"
- Sparklines transition from uPlot TimeChart to StatBar thin variant
- 2x2 grid with `gap-3` maintains at all breakpoints (cards are small enough to never need single-column)

### 6.7 Gantt Timeline (Plan Tab)

```
GanttChart.jsx (current)
div (relative, full width)
  Density strip (10px, 24 buckets, opacity encodes job count)
  Time axis labels (now, +6h, +12h, +18h, +24h)
  Chart area (relative, calculated height)
    Lane dividers (horizontal)
    [if conflicts] Conflict badges ("conflict" in red)
    Job bars (absolute positioned):
      left={startOffset/window*100}%, width={duration/window*100}%
      background=sourceColor(source), opacity=0.85
      [if heavy] border-left: 3px solid warning
      [if conflict] outline: 2px solid error
      Name label + [if wide] model chip + status dot
```

Target: CommandBar layout with TurnSlot timeline. Source colors -> ChromaProvider per source group. Conflict badges -> CrossingOut overlays. Density strip -> heat-encoded CommandBar summary.

---

## 7. Real-Time Update Rules

### 7.1 Polling Data Flow

```
startPolling()
  -> fetchStatus() every 5s
     -> updates: status, queue signals
     -> every 12th poll (~60s): health, durations, heatmap, history
  -> fetchDLQ() on initial load
  -> fetchAll() on visibility change (tab becomes visible)

Plan tab: fetchSchedule() + fetchModels() on mount, 10s refresh
Models tab: fetchModels() on mount, catalog on debounced search (300ms)
```

### 7.2 Animation Rules per Update Type

| Update Type | Animation | Duration | Guard |
|-------------|-----------|----------|-------|
| Numeric value changes (KPIs) | `t2-typewriter` on number | 0.3s | Only if `prevValue !== newValue` |
| Progress bar movement | CSS `transition: width 1s linear` | 1s | Always (smooth interpolation between polls) |
| Resource gauge changes | `t2-bar-grow` on fill | 0.5s | Only if delta > 2% (ignore micro-fluctuations) |
| Queue item added | AnimatedList enter (`fade-in-up`) | 0.3s | Stagger delay by position |
| Queue item removed (completed) | CanvasSaved flash + fade-out | 0.3s | On `onAnimationEnd`, remove DOM node |
| Queue item removed (failed) | CrossingOut + fade-out | 0.3s | Same |
| Queue item removed (cancelled) | VoidErase | 0.2s | Same |
| Queue reorder (drag-drop) | CSS transform slide | 0.15s | Optimistic; next poll self-heals if rejected |
| DLQ entry appears | `t3-badge-appear` on History badge + InkSplatter on section | 0.4s | Badge gets `t3-counter-bump` |
| Connection lost (3+ failures) | `t3-orange-pulse` on banner | 0.6s x3 | Repeat on subsequent backoff retries |
| Connection restored | Banner exit + `t2-tick-flash` on data containers | 0.4s | Signal all data is fresh |
| Daemon state change | Sidebar chip + AtmosphereShift | 0.3s/1s | Chip instant, atmosphere cross-fade |
| Setting saved | `t2-tick-flash` on input background | 0.4s | Already implemented as `flashKey` state |
| Page tab switch | PaintReveal (enter) + FractureExit (exit) | 0.25s/0.2s | Not on hot-reload |
| Initial page load | No animations (populate silently) | -- | Check if previous signal was `null` |
| Tab visibility restore | `t2-tick-flash` on updated containers (no entry animations) | 0.4s | Page already rendered |

### 7.3 Signal-Driven Rendering Rules

All state lives in `@preact/signals`. Components subscribe by reading `.value` in render. When a signal updates, only subscribing components re-render.

**AnimatedList requirement:** Any list that changes on poll (queue, DLQ, history) must use keyed AnimatedList that diffs by `id`. Existing items stay in DOM with data updates in place. Only added/removed items get entry/exit animations.

**No janky re-renders:** The current `queue.value = data.queue` replaces the entire array. This works for Preact's VDOM diffing but would cause visual glitches with entry/exit animations. AnimatedList diffs by key to prevent this.

### 7.4 Exponential Backoff Behavior

```
Failure: _pollFailures++; if >= 3 -> connectionStatus = 'disconnected'
         _backoffMs = Math.min(_backoffMs * 2, 30000)  // 5s -> 10s -> 20s -> 30s cap
Recovery: _pollFailures = 0; connectionStatus = 'ok'; _backoffMs = POLL_INTERVAL
```

Progressive visual escalation:
1. First failure (1/3): No visible change (transient errors normal)
2. Second failure (2/3): Subtle `t1-pulse-ring` on sidebar chip
3. Third+ failure (3+/3): Disconnected banner with `t3-orange-pulse`, mood -> dread, sidebar chip -> maelle
4. Recovery: Banner exits, mood restores, all containers get `t2-tick-flash`

### 7.5 Anti-Patterns for Real-Time Updates

- **Never re-render entire lists on every poll.** Use keyed AnimatedList.
- **Never animate unchanged values.** Guard with `prevValue !== newValue`.
- **Never use `setInterval` for visual animations.** CSS-only. JS intervals compete with poll cycles.
- **Never block polls with long animations.** Fire-and-forget (CSS class toggle).
- **Never flash on initial load.** First fetch populates silently; check if previous was `null`.
- **Never remove DOM elements synchronously.** Play exit animation first, remove `onAnimationEnd`.

---

## 8. Anti-Patterns

### Design System Violations

**1. Do not use hardcoded hex for priority.** `QueueList.jsx` has `PRIORITY_COLORS = { critical: '#ef4444', ... }`. Use `var(--status-*)` tokens so themes work. `Plan.jsx`'s `CATEGORY_COLORS` is the reference pattern.

**2. Do not use dread mood for the entire Now page.** Dread is for actual problems (DLQ > 0, failures). Healthy Now uses dawn. Dynamic switching based on `showAlerts`.

**3. Do not use VoidErase for queue completion.** VoidErase = permanent deletion (Gommage). Completed jobs use CanvasSaved (success). VoidErase is only for DLQ dismiss/clear.

**4. Do not put InkSplatter on validation errors.** InkSplatter is dramatic (violent paint burst) -- DLQ and system errors only. Settings validation uses CrossingOut (less dramatic).

**5. Do not animate on every 5s poll.** Only when data actually changes. Resource gauge at 45% on two polls = no re-animation.

**6. Do not use maelle chroma for success.** Maelle = passionate destruction. Completed = gustave (guardian gold). Retry success transitions TO gustave, not stays in maelle.

**7. Do not mix chromas in same hierarchy.** Each ChromaProvider = single chroma for subtree. Running job = gustave. Queue = verso. Separate trees.

**8. Do not use garland ornaments on error states.** Garlands = ceremony/beauty. DLQ and failures get no decorative flourishes.

**9. Do not use dread mood for paused daemon.** Paused is intentional (operator choice or health protection). Use nostalgic (warm, calm). Dread is for unintentional problems.

**10. Do not use MonolithDisplay for every KPI.** Reserve for 1-2 most critical numbers (queue depth, jobs/24h). Using it for pause time dilutes impact.

### Technical Constraints

**11. Do not shadow the `h` JSX factory.** Never use `h` as a callback parameter (`.map(h => ...)`). esbuild injects `h`. Shadowing causes silent render crashes. Use descriptive names (`job`, `entry`, `model`).

**12. Do not replace uPlot with CSS sparklines.** uPlot is sub-millisecond. Keep TimeChart for duration trends and HeroCard sparklines.

**13. Do not use `.exp-*` class prefixes yet.** Codebase uses `.t-*`. Migration to `.exp-*` is a coordinated cross-project effort. Continue with `.t-frame`, `.t-status`, `.t-btn`.

**14. Do not add font imports to the SPA.** expedition33-ui handles font loading via `@import`. Adding `<link>` tags causes FOUT and duplicated downloads.

**15. Do not add battle/game components to Settings.** Settings = camp/rest screen. No atmospheric layers, dramatic animations, or ornamental frames. Current `.t-frame` with save-on-blur flash is appropriate.

**16. Do not apply `data-chroma` to structural layout.** Not on `.layout-root`, `.layout-sidebar`, `.layout-main`. Apply at section level (DLQ section = `data-chroma` danger, running job frame = success).

**17. Do not add atmosphere to sidebar or bottom nav.** Navigation chrome must be stable and scannable. Canvas texture, vignette, dust motes = main content only.

**18. No SSR, no React, no Framer Motion.** Preact 10 SPA. Use `h`, `class`, `@preact/signals`. Animations are pure CSS. Build required after changes (`npm run build`).

---

## Appendix A: Signal Store -> Component Data Flow

```
store.js signals               Page/Component consumers
-----------------              ------------------------
status          --------->     Now (daemon, kpis, current_job)
                --------->     Plan (running job banner)
                --------->     Settings (daemonState)
                --------->     App (daemonState for sidebar)

queue           --------->     Now -> QueueList
                --------->     QueueList (drag-reorder writes back)

history         --------->     Now (failure count for alert strip)
                --------->     History -> HistoryList

healthData      --------->     Now (latestHealth for gauges + sparklines)

durationData    --------->     Now (sparklines in HeroCard)
                --------->     History (TimeChart duration trends)

heatmapData     --------->     History -> ActivityHeatmap

settings        --------->     Now (resource gauge thresholds)
                --------->     Settings -> SettingsForm
                --------->     CurrentJob (gauge thresholds)

scheduleJobs    --------->     Plan (table + Gantt)
scheduleEvents  --------->     Plan (rebalance log)

dlqEntries      --------->     History (DLQ section)
dlqCount        --------->     Now (alert strip)
                --------->     App -> Sidebar (badge)
                --------->     App -> BottomNav (badge)

models          --------->     ModelsTab (installed table)
                --------->     Plan (model select dropdown)

modelCatalog    --------->     ModelsTab (download panel)
queueEtas       --------->     Plan (via fetchSchedule)
connectionStatus -------->     Now (disconnected banner)
currentTab      --------->     App (view switching)
```

## Appendix B: File Inventory

```
src/
|-- index.jsx           Entry point, renders <App /> into #app
|-- app.jsx             Router: Sidebar + BottomNav + tab view switching
|-- store.js            Signal store: all API calls, polling, state
|-- index.css           CSS entry: Tailwind, expedition33-ui, all custom CSS
|-- preact-shim.js      Preact compatibility shim
|
|-- pages/
|   |-- Now.jsx         Command center: 2-column ops/health grid
|   |-- Plan.jsx        Schedule: Gantt + recurring job table + rebalance
|   |-- History.jsx     DLQ + duration trends + heatmap + job history
|   |-- ModelsTab.jsx   Installed models table + download catalog
|   +-- Settings.jsx    Configuration: delegates to SettingsForm
|
|-- components/
|   |-- Sidebar.jsx     Fixed sidebar nav (desktop) with daemon status chip
|   |-- BottomNav.jsx   Mobile bottom tab bar
|   |-- CurrentJob.jsx  Running job / paused / idle display
|   |-- QueueList.jsx   Drag-to-reorder priority queue
|   |-- HeroCard.jsx    KPI card: large value + sparkline + delta
|   |-- ResourceGauges.jsx  4 horizontal bars (RAM/VRAM/Load/Swap)
|   |-- StatusBadge.jsx Terminal-style status pill
|   |-- GanttChart.jsx  24h timeline with lane allocation + conflict detection
|   |-- TimeChart.jsx   uPlot wrapper for sparklines and duration trends
|   |-- ActivityHeatmap.jsx  7x24 CSS grid heatmap
|   |-- HistoryList.jsx Recent jobs with expandable failure details
|   |-- SettingsForm.jsx  7-section form with save-on-blur flash
|   |-- ModelBadge.jsx  Profile pill + type tag badge
|   |-- CollapsibleSection.jsx  Cursor-state expand/collapse
|   |-- ErrorState.jsx  Error display component
|   +-- LoadingState.jsx  Loading display component
|
+-- __mocks__/          Test mocks
```

## Appendix C: CSS Custom Property Reference (App-Specific)

Defined in `index.css`, supplementing the expedition33-ui token system:

```css
/* Layout tokens (app-specific) */
--sidebar-width: 200px;      /* Desktop sidebar */
--sidebar-width-sm: 64px;    /* Tablet collapsed sidebar */

/* Consumed from expedition33-ui (key subset) */
--bg-base                     /* Page background */
--bg-surface                  /* Card/panel background */
--bg-surface-raised           /* Hover/elevated surface */
--bg-inset                    /* Sunken/recessed areas */
--bg-terminal                 /* Terminal texture background */
--text-primary                /* Primary text */
--text-secondary              /* Secondary text */
--text-tertiary               /* Tertiary/hint text */
--text-accent                 /* Gold accent text */
--accent                      /* Primary accent color */
--accent-text                 /* Text on accent backgrounds */
--accent-glow                 /* Accent glow (active nav bg) */
--accent-warm                 /* Warm accent variant */
--accent-warm-glow            /* Warm accent glow */
--border-primary              /* Visible border */
--border-subtle               /* Structural border */
--status-healthy              /* Green */
--status-warning              /* Amber */
--status-error                /* Red */
--status-waiting              /* Muted pending */
--status-healthy-glow         /* Green glow (settings flash) */
--status-error-glow           /* Red glow (alert strip bg) */
--card-shadow                 /* Default card shadow */
--card-shadow-hover           /* Hover card shadow */
--radius                      /* Border radius */
--font-mono                   /* Monospace font stack */
--type-hero                   /* Largest display size */
--type-headline               /* Section heading */
--type-body                   /* Body text */
--type-label                  /* Small labels */
--type-micro                  /* Smallest text */
--scan-line                   /* Scan line overlay color */
```

## Appendix D: Settings Section -> Chroma Mapping

| Settings Section | data-label | Chroma | Rationale |
|-----------------|------------|--------|-----------|
| Health Thresholds | "Health Thresholds" | lune | Analytical configuration |
| Defaults | "Defaults" | gustave | Core operational parameters |
| Retention | "Retention" | sciel | Data lifecycle management |
| Retry Defaults | "Retry Defaults" | maelle | Failure recovery |
| Stall Detection | "Stall Detection" | maelle | Failure detection |
| Concurrency | "Concurrency" | lune | Resource allocation |
| Daemon Controls | "Daemon Controls" | gustave | Primary operational control |
