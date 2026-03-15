# Superhot UX Polish — Full Design System Compliance

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply all outstanding design system guidance from `docs/llm-guide-design-system.md` using superhot-ui only — fixing animation timing bugs, replacing hardcoded hex with CSS tokens, adding CRT consistency, wiring the data-mood atmosphere system, and completing the badge/connection animation spec.

**Architecture:** All changes are SPA-only (`ollama_queue/dashboard/spa/src/`). No new files. No backend changes. No expedition33-ui components. Six independent tasks that can be reviewed and committed separately. The mood system adds CSS rules to `index.css` and `data-mood` attributes to page roots — no superhot-ui rebuild needed.

**Tech Stack:** Preact 10, `@preact/signals`, superhot-ui, Tailwind v4, esbuild. Build: `cd ollama_queue/dashboard/spa && npm run build`. No frontend test runner — verify with build + visual smoke test.

**Design system reference:** `docs/llm-guide-design-system.md` — read §1.5 Strategy Stack, §3 Tab-to-Mood, §7.2 State Transition Animations, §8 Anti-Patterns before modifying any file.

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue
git checkout -b feature/superhot-ux-polish
cd ollama_queue/dashboard/spa && npm run build   # must be green before starting
```

---

## Task 1: Fix ShShatter Timing — DLQ Dismiss + Clear All

**File:** `ollama_queue/dashboard/spa/src/pages/History.jsx`

**Why:** `shatterElement` is fired concurrently with the API call. `fetchDLQ()` updates the signal, Preact unmounts the row DOM node mid-animation. Design system §7.5: "Never remove DOM elements synchronously. Play exit animation first."

**How `shatterElement` works:** Hides element immediately → runs fragments for `--sh-shatter-duration` (600ms default) → calls `element.remove()` AND `onComplete` at 650ms. Moving the API call into `onComplete` guarantees DOM stays alive for the full animation.

---

### Step 1.1 — Fix DLQ row dismiss

Find the dismiss button `onClick` in `DLQRow` (search `shatterElement(rowRef.current)`). It currently fires shatter + API call in parallel:

```jsx
// CURRENT (broken — fires API call immediately, unmounts row mid-animation)
onClick={() => {
    if (rowRef.current) shatterElement(rowRef.current);
    dismissAct(
        'Dismissing…',
        () => onAction('dismiss', entry.id),
        `DLQ #${entry.id} dismissed`,
    );
}}
```

- [ ] Replace with sequenced version — API call moves into `onComplete`:

```jsx
// FIXED — API call only fires after animation completes
onClick={() => {
    if (rowRef.current) {
        shatterElement(rowRef.current, {
            onComplete: () => dismissAct(
                'Dismissing…',
                () => onAction('dismiss', entry.id),
                `DLQ #${entry.id} dismissed`,
            ),
        });
    } else {
        dismissAct(
            'Dismissing…',
            () => onAction('dismiss', entry.id),
            `DLQ #${entry.id} dismissed`,
        );
    }
}}
```

The `else` branch handles `rowRef.current === null` — `shatterElement` would call `onComplete` directly in this case anyway (it checks `element.parentNode`), but explicit fallback makes the intent clear.

---

### Step 1.2 — Fix cascade shatter in handleClearDLQ

Find `handleClearDLQ`. It currently staggers shatters then waits a hardcoded 300ms:

```jsx
// CURRENT (broken — 5 rows needs ~970ms, API fires at 300ms)
rows.forEach((row, i) => {
    setTimeout(() => shatterElement(row), i * 80);
});
await new Promise(resolve => setTimeout(resolve, 300));
const res = await fetch(`${API}/dlq`, { method: 'DELETE' });
```

- [ ] Replace with Promise that resolves on the last row's `onComplete`:

```jsx
// FIXED — API call waits for last animation to complete
if (dlqListRef.current) {
    const rows = Array.from(dlqListRef.current.children);
    if (rows.length > 0) {
        await new Promise(resolve => {
            rows.forEach((row, i) => {
                setTimeout(() => {
                    shatterElement(row, {
                        onComplete: i === rows.length - 1 ? resolve : undefined,
                    });
                }, i * 80);
            });
        });
    }
}
const res = await fetch(`${API}/dlq`, { method: 'DELETE' });
```

The `rows.length > 0` guard handles an empty container (stale badge data). Only the last row (`i === rows.length - 1`) gets `onComplete: resolve` — earlier rows animate independently; `shatterElement` skips the callback silently when `undefined`. The wait is now adaptive: 3 rows = ~890ms, 10 rows = ~1370ms.

---

- [ ] **Build:**
  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  ```
  Expected: exits 0.

- [ ] **Commit:**
  ```bash
  git add ollama_queue/dashboard/spa/src/pages/History.jsx
  git commit -m "fix(spa): sequence shatter — DLQ dismiss and clear-all wait for animation before API call"
  ```

---

## Task 2: Replace Hardcoded Hex with CSS Tokens

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/QueueList.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx` (via `CurrentJob.jsx`)
- Modify: `ollama_queue/dashboard/spa/src/components/CurrentJob.jsx`
- Modify: `ollama_queue/dashboard/spa/src/index.css`

**Why:** Design system anti-pattern #1: "Do not use hardcoded hex for priority. Use `var(--status-*)` tokens so themes work." Reference pattern already exists in `Plan/helpers.js:CATEGORY_COLORS` — same priority levels, already uses `var()` tokens.

---

### Step 2.1 — Add new tokens to index.css

Source colors (telegram/notion/eval) and regime colors (burst/trough) have no semantic token in superhot-ui. Add them as app-level tokens at the top of `index.css` `:root` block:

- [ ] Find the `:root {` block in `index.css` (it contains `--font-mono`). Append after the existing tokens:

```css
/* ── Source identity colors — categorical, not status ── */
--source-telegram:      var(--status-warning);       /* orange — high-frequency source */
--source-notion:        oklch(0.68 0.14 295);         /* purple — Notion brand */
--source-eval:          oklch(0.62 0.18 270);         /* indigo — eval pipeline */
--source-default:       var(--text-secondary);

/* ── Burst regime colors ── */
--regime-burst:         var(--status-warning);        /* orange — high traffic pressure */
--regime-trough:        oklch(0.62 0.13 230);         /* muted blue — quiet window */
--regime-steady:        var(--status-healthy);        /* green — normal flow */
--regime-unknown:       var(--text-tertiary);
--regime-burst-bg:      oklch(0.72 0.18 55 / 0.1);
--regime-trough-bg:     oklch(0.62 0.13 230 / 0.1);
--regime-steady-bg:     oklch(0.55 0.18 145 / 0.1);
--regime-unknown-bg:    var(--bg-inset);
```

---

### Step 2.2 — QueueList.jsx: PRIORITY_COLORS → var()

- [ ] Find `PRIORITY_COLORS` (lines ~22–25). Replace:

```jsx
// BEFORE
const PRIORITY_COLORS = {
  critical: '#ef4444', high: '#f97316',
  normal: '#3b82f6', low: '#6b7280', background: '#374151',
};

// AFTER — aligned with Plan/helpers.js CATEGORY_COLORS reference pattern
const PRIORITY_COLORS = {
  critical:   'var(--status-error)',
  high:       'var(--status-warning)',
  normal:     'var(--accent)',
  low:        'var(--text-tertiary)',
  background: 'var(--text-tertiary)',
};
```

---

### Step 2.3 — GanttChart.jsx: SOURCE_COLORS + legend hex → var()

- [ ] Find `SOURCE_COLORS` object (lines ~11–15) and the `sourceColor()` function (lines ~22–25). Replace:

```jsx
// BEFORE
export const SOURCE_COLORS = {
    telegram: '#f97316',
    notion:   '#a78bfa',
    eval:     '#6366f1',
};

// AFTER
export const SOURCE_COLORS = {
    telegram: 'var(--source-telegram)',
    notion:   'var(--source-notion)',
    eval:     'var(--source-eval)',
};
```

- [ ] Find the `sourceColor()` function body (hardcoded hex returns). Replace each:

```jsx
// BEFORE
if (s === 'telegram' || s.startsWith('telegram-')) return '#f97316';
if (s === 'notion'   || s.startsWith('notion-'))   return '#a78bfa';
if (s === 'eval'     || s.startsWith('eval-'))      return '#6366f1';

// AFTER
if (s === 'telegram' || s.startsWith('telegram-')) return 'var(--source-telegram)';
if (s === 'notion'   || s.startsWith('notion-'))   return 'var(--source-notion)';
if (s === 'eval'     || s.startsWith('eval-'))      return 'var(--source-eval)';
```

- [ ] Find the legend color entries (lines ~858–859) and replace:

```jsx
// BEFORE
{ color: '#f97316', label: 'telegram', symbol: '●' },
{ color: '#a78bfa', label: 'notion',   symbol: '▲' },

// AFTER
{ color: 'var(--source-telegram)', label: 'telegram', symbol: '●' },
{ color: 'var(--source-notion)',   label: 'notion',   symbol: '▲' },
```

> **Note on uPlot legend colors:** uPlot reads `series[i].stroke` for the chart lines and expects a CSS color string. `var(--source-telegram)` is a valid CSS color string and uPlot passes it directly to the canvas context — it works because `<canvas>` supports CSS variables in stroke/fill. If the legend renders incorrectly, fall back to the resolved oklch values directly.

---

### Step 2.4 — CurrentJob.jsx: REGIME_STYLE + overrun hex → var()

- [ ] Find `REGIME_STYLE` (lines ~305–310). Replace:

```jsx
// BEFORE
const REGIME_STYLE = {
  burst:   { color: '#f97316', border: '#f97316', bg: 'rgba(249,115,22,0.1)' },
  trough:  { color: '#60a5fa', border: '#60a5fa', bg: 'rgba(96,165,250,0.1)' },
  ...
};

// AFTER
const REGIME_STYLE = {
  burst:   { color: 'var(--regime-burst)',   border: 'var(--regime-burst)',   bg: 'var(--regime-burst-bg)' },
  trough:  { color: 'var(--regime-trough)',  border: 'var(--regime-trough)',  bg: 'var(--regime-trough-bg)' },
  steady:  { color: 'var(--regime-steady)',  border: 'var(--regime-steady)',  bg: 'var(--regime-steady-bg)' },
  unknown: { color: 'var(--regime-unknown)', border: 'var(--regime-unknown)', bg: 'var(--regime-unknown-bg)' },
};
```

> If `steady` and `unknown` entries don't exist in the current object, add them using the tokens above. Check the full object at the actual line numbers.

- [ ] Find the overrun progress bar color (line ~197):

```jsx
// BEFORE
background: isOverrun ? '#f97316' : 'var(--accent)',

// AFTER
background: isOverrun ? 'var(--status-warning)' : 'var(--accent)',
```

---

- [ ] **Build:**
  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  ```
  Expected: exits 0.

- [ ] **Visual check:** Open the dashboard. Priority borders in the queue list, Gantt source colors, and the burst regime badge should all render correctly. Light mode should also look correct (no hardcoded colors breaking the theme).

- [ ] **Commit:**
  ```bash
  git add \
    ollama_queue/dashboard/spa/src/components/QueueList.jsx \
    ollama_queue/dashboard/spa/src/components/GanttChart.jsx \
    ollama_queue/dashboard/spa/src/components/CurrentJob.jsx \
    ollama_queue/dashboard/spa/src/index.css
  git commit -m "fix(spa): replace hardcoded hex with CSS tokens — priority, source, regime colors"
  ```

---

## Task 3: `.sh-crt` on AddRecurringJobModal

**File:** `ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx` (or wherever the Plan tab's recurring job modal lives — check `src/pages/Plan/` if not in components)

**Why:** Every other modal with a `<dialog>` element has `.sh-crt` for CRT scanline effect. `AddRecurringJobModal` is the only one missing it. Check `SubmitJobModal.jsx` for the reference pattern.

---

- [ ] Find the `<dialog>` element in `AddRecurringJobModal.jsx`. It currently has a `class` without `.sh-crt`:

```jsx
// BEFORE
<dialog ...>

// AFTER — add sh-crt to match SubmitJobModal and OnboardingOverlay
<dialog class="sh-crt" ...>
```

  If there's already a `class` prop, append: `class={`... sh-crt`}` or use template literal. Don't replace other classes.

- [ ] **Build + commit:**
  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  git add ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx
  # (or wherever the modal lives)
  git commit -m "fix(spa): add .sh-crt to AddRecurringJobModal — CRT consistency with other modals"
  ```

---

## Task 4: data-mood Atmosphere System

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/index.css`
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/History.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan/index.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Settings.jsx`

**Why:** Design system §3 specifies tab-to-mood mapping with zero current implementation. `data-mood` adds a subtle atmosphere shift via CSS cascade without changing component structure. In the SUPERHOT theme, moods are expressed through `.t-frame` border tints and ambient background shifts.

**Mood mapping (from §3):**
- `Now` → dynamic: `dread` when `showAlerts`, `dawn` when healthy
- `History` → always `dread` (wasteland/DLQ)
- `Plan` → always `wonder` (planning/wonder)
- `Models` → always `nostalgic` (analytical/contemplative)
- `Settings` → always `nostalgic`

---

### Step 4.1 — Add data-mood CSS rules to index.css

- [ ] Append to `index.css` after the existing `/* Page mood system */` comment if it exists, or add a new section at the end:

```css
/* ── Page mood system — atmosphere per operational state ──────────────────
   data-mood cascades a subtle visual shift through .t-frame borders and
   background. dread = wasteland threat (DLQ/failures), dawn = healthy
   baseline, wonder = planning headspace, nostalgic = analytical quiet.
   All shifts are additive — they don't override component-level chroma.
   ────────────────────────────────────────────────────────────────────── */

[data-mood="dread"] .t-frame {
    box-shadow: inset 0 0 0 1px oklch(0.45 0.25 10 / 0.25),
                0 0 12px oklch(0.45 0.25 10 / 0.06);
}

[data-mood="dread"] {
    --mood-accent: var(--status-error);
}

[data-mood="dawn"] .t-frame {
    box-shadow: inset 0 0 0 1px oklch(0.55 0.2 145 / 0.12);
}

[data-mood="dawn"] {
    --mood-accent: var(--status-healthy);
}

[data-mood="wonder"] .t-frame {
    box-shadow: inset 0 0 0 1px oklch(0.55 0.2 145 / 0.18),
                0 0 8px oklch(0.55 0.2 145 / 0.04);
}

[data-mood="wonder"] {
    --mood-accent: var(--status-healthy);
}

[data-mood="nostalgic"] {
    filter: saturate(0.9);
}

@media (prefers-reduced-motion: reduce) {
    [data-mood] .t-frame { box-shadow: none; }
    [data-mood="nostalgic"] { filter: none; }
}
```

**Design intent:**
- `dread`: `.t-frame` borders glow with a faint red tint (wasteland atmosphere). Subtle — this adds ~0.25 opacity red inset shadow to every frame on the page.
- `dawn`: `.t-frame` borders get a faint phosphor green tint (healthy/operational).
- `wonder`: `.t-frame` borders glow faintly phosphor green (slightly brighter than dawn — planning headspace).
- `nostalgic`: Entire page slightly desaturated (analytical quiet). Uses `filter: saturate(0.9)` on the container — 10% desaturation is visible but not jarring.
- `prefers-reduced-motion`: Removes shadow/filter overrides entirely.

---

### Step 4.2 — Add data-mood to Now.jsx

- [ ] Find the outermost `<div>` in `Now`'s return (the one with `class="flex flex-col ..."` or similar). `showAlerts` is already computed at line 68. Add `data-mood`:

```jsx
// BEFORE
<div class="flex flex-col gap-6 animate-page-enter">

// AFTER — dynamic: dread when DLQ/failures, dawn when healthy
<div class="flex flex-col gap-6 animate-page-enter"
     data-mood={showAlerts ? 'dread' : 'dawn'}>
```

---

### Step 4.3 — Add data-mood to History.jsx

- [ ] Find the outermost `<div>` in `History`'s return:

```jsx
// BEFORE
<div class="flex flex-col gap-6 animate-page-enter">

// AFTER — History is always wasteland
<div class="flex flex-col gap-6 animate-page-enter" data-mood="dread">
```

---

### Step 4.4 — Add data-mood to Plan/index.jsx

- [ ] Find the outermost `<div>` returned by the Plan component (search `animate-page-enter` in the file):

```jsx
// BEFORE
<div class="... animate-page-enter">

// AFTER
<div class="... animate-page-enter" data-mood="wonder">
```

---

### Step 4.5 — Add data-mood to ModelsTab.jsx and Settings.jsx

- [ ] Find the outermost `<div>` in `ModelsTab`'s return, add `data-mood="nostalgic"`.
- [ ] Find the outermost `<div>` in `Settings`'s return, add `data-mood="nostalgic"`.

---

- [ ] **Build:**
  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  ```
  Expected: exits 0.

- [ ] **Visual check:** Navigate to each tab. History should have a faint red border tint on all frames. Now with DLQ entries should also have the red tint. Now when clean should have faint green. Models/Settings should be slightly desaturated. Plan should have the faint green wonder tint.

- [ ] **Commit:**
  ```bash
  git add \
    ollama_queue/dashboard/spa/src/index.css \
    ollama_queue/dashboard/spa/src/pages/Now.jsx \
    ollama_queue/dashboard/spa/src/pages/History.jsx \
    ollama_queue/dashboard/spa/src/pages/Plan/index.jsx \
    ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx \
    ollama_queue/dashboard/spa/src/pages/Settings.jsx
  git commit -m "feat(spa): data-mood atmosphere system — dread/dawn/wonder/nostalgic per tab"
  ```

---

## Task 5: DLQ Badge Animations

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/Sidebar.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/BottomNav.jsx`

**Why:** Design system §7.2: "DLQ entry appears → `t3-badge-appear` on History badge + `t3-counter-bump`." The DLQ badge in Sidebar and BottomNav renders when `dlqCount > 0`, but appears/changes silently. The animation classes are already defined in `index.css` — they just need to be toggled.

**Pattern:** Track the previous `dlqCount` value with `useRef`. On each render:
- If previous was 0 and current is > 0: apply `t3-badge-appear` (new badge appearing)
- If previous was > 0 and current changed: apply `t3-counter-bump` (count changed)
- Store key in `useRef` and toggle a class — CSS animation reruns when the class is removed and re-added via a key change.

**Important:** CSS animations only re-trigger when the class is re-added. Use a `key` prop or toggle a separate state variable to force remount. The cleanest pattern: track `animClass` in a `useRef` string and update it each render when the count changes.

---

### Step 5.1 — Sidebar.jsx badge animation

- [ ] Find the Sidebar component. At the top of the function body (after existing hooks), add:

```jsx
import { useRef } from 'preact/hooks';

// In Sidebar function body, after existing hooks:
// What it does: fires t3-badge-appear when DLQ badge first appears (count was 0),
// t3-counter-bump when count changes. Uses a render-key to force CSS re-trigger.
const prevDlqRef = useRef(dlqCount);
const badgeAnimKey = useRef(0);
const badgeAnimClass = useRef('');

if (dlqCount !== prevDlqRef.current) {
    if (prevDlqRef.current === 0 && dlqCount > 0) {
        badgeAnimClass.current = 't3-badge-appear';
    } else if (dlqCount > 0) {
        badgeAnimClass.current = 't3-counter-bump';
    } else {
        badgeAnimClass.current = '';
    }
    badgeAnimKey.current += 1;
    prevDlqRef.current = dlqCount;
}
```

- [ ] Find where the badge is rendered (line ~59: `const badge = item.id === 'history' && dlqCount > 0 ? dlqCount : null`). Find the badge element rendering (likely a `<span>` with a count). Add the animation class and key:

```jsx
// Find the badge span — it will look something like:
<span class="badge ...">{badge}</span>

// AFTER — add animation class + key to force CSS re-trigger
<span key={badgeAnimKey.current}
      class={`badge ... ${badgeAnimClass.current}`}>
    {badge}
</span>
```

> **Find the exact badge element:** `grep -n "badge" ollama_queue/dashboard/spa/src/components/Sidebar.jsx` — look for the element that renders the `badge` variable.

---

### Step 5.2 — BottomNav.jsx badge animation

- [ ] Apply the same pattern to `BottomNav.jsx`. Find `issueCount` usage (line ~38: `const issueCount = dlqCount || 0`). Add the same `prevDlqRef` / `badgeAnimKey` / `badgeAnimClass` logic, then apply to the badge span with `key` and class.

---

- [ ] **Build + commit:**
  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  git add \
    ollama_queue/dashboard/spa/src/components/Sidebar.jsx \
    ollama_queue/dashboard/spa/src/components/BottomNav.jsx
  git commit -m "feat(spa): DLQ badge t3-badge-appear on first show, t3-counter-bump on count change"
  ```

---

## Task 6: Connection Recovery t2-tick-flash (Stage 4)

**File:** `ollama_queue/dashboard/spa/src/stores/index.js`

**Why:** Design system §7.2 specifies 4-stage progressive visual escalation for connection failures. Stage 4 (recovery) is missing: "Connection restored → Banner exit + `t2-tick-flash` on data containers." The signal goes `ok → disconnected → ok` but no visual confirmation fires on recovery.

**How `t2-tick-flash` works:** The class triggers a `tick-flash` keyframe (0.4s, amber glow flash). It's defined in `index.css`. To re-trigger: remove the class and re-add it on the next animation frame.

**Implementation approach:** In `stores/index.js`, when `connectionStatus` transitions from `'disconnected'` to `'ok'`, dispatch a custom event that `app.jsx` listens to. `app.jsx` then momentarily adds `t2-tick-flash` to the main content container.

---

### Step 6.1 — Dispatch recovery event in stores/index.js

- [ ] Find the recovery line in `stores/index.js` (line ~100: `connectionStatus.value = 'ok'`). Wrap with a previous-state check:

```js
// BEFORE
connectionStatus.value = 'ok';

// AFTER — fire recovery event when transitioning from disconnected → ok
if (connectionStatus.value === 'disconnected') {
    connectionStatus.value = 'ok';
    // Notify UI that connection was restored — triggers t2-tick-flash on data containers
    if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('queue:connection-restored'));
    }
} else {
    connectionStatus.value = 'ok';
}
```

---

### Step 6.2 — Listen and apply t2-tick-flash in app.jsx

- [ ] In `app.jsx`, find a `useEffect` block (or add one near the component top). Add a listener for the `queue:connection-restored` event:

```jsx
// Add near top of App component, after existing useEffects:
// What it does: flashes all main content containers when connection restores after outage.
// Design system §7.2 stage 4: "t2-tick-flash on data containers" on recovery.
useEffect(() => {
    function onRestored() {
        const main = document.querySelector('.layout-main');
        if (!main) return;
        // Remove + re-add class on next frame to re-trigger CSS animation
        main.classList.remove('t2-tick-flash');
        requestAnimationFrame(() => main.classList.add('t2-tick-flash'));
        // Clean up after animation (0.4s)
        setTimeout(() => main.classList.remove('t2-tick-flash'), 500);
    }
    window.addEventListener('queue:connection-restored', onRestored);
    return () => window.removeEventListener('queue:connection-restored', onRestored);
}, []);
```

> **Important:** Import `useEffect` from `preact/hooks` if not already present. The `.layout-main` selector matches the main content area per the SPA layout defined in `index.css`. Verify the selector: `grep -n "layout-main" src/index.css`.

---

- [ ] **Build + commit:**
  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  git add \
    ollama_queue/dashboard/spa/src/stores/index.js \
    ollama_queue/dashboard/spa/src/app.jsx
  git commit -m "feat(spa): t2-tick-flash on connection recovery — complete 4-stage escalation"
  ```

---

## Finish

```bash
cd ~/Documents/projects/ollama-queue
git log --oneline -8    # verify all 6 commits clean
cd ollama_queue/dashboard/spa && npm run build   # final green build
```

Then invoke `superpowers:finishing-a-development-branch` to PR and merge.

---

## Smoke Test Checklist

With `ollama-queue serve` running:

- [ ] **Task 1:** Click Delete on a DLQ entry — row shatters fully before disappearing from list
- [ ] **Task 1:** Click Delete All on DLQ — cascade shatter, list only clears after last animation
- [ ] **Task 2:** Queue items show priority border colors (red/orange/blue/grey) matching theme tokens, not hardcoded orange/blue
- [ ] **Task 2:** Gantt source bars render in correct colors; legend matches
- [ ] **Task 2:** Burst regime badge in CurrentJob renders in orange/blue/green/grey via tokens
- [ ] **Task 3:** Open Plan tab → add recurring job → modal has CRT scanlines
- [ ] **Task 4:** Navigate to History — all `.t-frame` borders have faint red tint
- [ ] **Task 4:** Navigate to Now with 0 DLQ — frames have faint green tint. Navigate to Now with DLQ entries — frames shift to red tint
- [ ] **Task 4:** Navigate to Models or Settings — slight desaturation visible
- [ ] **Task 5:** Trigger a DLQ entry — sidebar/bottom nav badge appears with bounce animation
- [ ] **Task 5:** Resolve one DLQ entry leaving others — counter changes with bump animation
- [ ] **Task 6:** Kill `ollama-queue serve`, wait for disconnected banner, restart — on reconnection the main area flashes amber briefly
