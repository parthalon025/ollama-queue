# Action Feedback — Inline Status for All Buttons

**Date:** 2026-03-06
**Status:** Approved

## Problem

Non-immediate actions (cancel eval run, submit job, pause daemon, DLQ retry, etc.) give no feedback while in-flight. Users can't tell if the click registered. Cancel button on a stuck eval run is especially confusing — looks broken when it's just slow.

Also: `evalActiveRun` in sessionStorage persists after a service restart, leaving the UI showing a live progress panel for a run that no longer exists.

## Solution

A shared `useActionFeedback` hook that manages `idle → loading → success/error → idle` state for every action button. Inline status line appears directly below the button.

## Hook

```js
// src/hooks/useActionFeedback.js
import { useState } from 'preact/hooks';

export function useActionFeedback() {
  const [state, setState] = useState({ phase: 'idle', msg: '' });
  async function run(loadingLabel, fn) {
    setState({ phase: 'loading', msg: loadingLabel });
    try {
      await fn();
      setState({ phase: 'success', msg: 'Done' });
      setTimeout(() => setState({ phase: 'idle', msg: '' }), 2000);
    } catch (e) {
      setState({ phase: 'error', msg: e.message || 'Failed' });
    }
  }
  return [state, run];
}
```

## Usage Pattern

```jsx
const [fb, act] = useActionFeedback();
<button
  disabled={fb.phase === 'loading'}
  onClick={() => act('Cancelling…', () => cancelEvalRun(id))}
>
  {fb.phase === 'loading' ? 'Cancelling…' : 'Cancel'}
</button>
{fb.msg && <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>}
```

## CSS (add to bundle.css or tokens)

```css
.action-fb { font-size: var(--type-label); margin-top: 4px; }
.action-fb--loading { color: var(--text-muted); }
.action-fb--success { color: var(--status-ok); }
.action-fb--error   { color: var(--status-error); }
```

## Scope — All Action Buttons

| Component | Action | Loading label |
|-----------|--------|---------------|
| `ActiveRunProgress.jsx` | Cancel run | "Cancelling…" |
| `RunTriggerPanel.jsx` | Start run | "Starting…" |
| `RunRow.jsx` | Repeat run | "Repeating…" |
| `RunRow.jsx` | Judge rerun | "Re-judging…" |
| `VariantToolbar.jsx` | Generate variants | "Generating…" |
| `VariantRow.jsx` | Delete variant | "Deleting…" |
| `TemplateRow.jsx` | Delete template | "Deleting…" |
| `Settings.jsx` | Pause daemon | "Pausing…" |
| `Settings.jsx` | Resume daemon | "Resuming…" |
| `Settings.jsx` | Save settings | "Saving…" |
| `SettingsForm.jsx` | Save thresholds | "Saving…" |
| `SubmitJobModal.jsx` | Submit job | "Submitting…" |
| `AddRecurringJobModal.jsx` | Add recurring job | "Adding…" |
| Plan page | Pause/remove recurring job | "Updating…" |
| History/DLQ | Retry DLQ job | "Retrying…" |

## Stale sessionStorage Fix

On store init, if `evalActiveRun` is loaded from sessionStorage, immediately verify against the API:

```js
// In store.js init, after loading evalActiveRun from sessionStorage:
if (evalActiveRun.value) {
  fetch(`${API}/eval/runs/${evalActiveRun.value.run_id}/progress`)
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (!data || ['complete','failed','cancelled'].includes(data.status)) {
        evalActiveRun.value = null;
        sessionStorage.removeItem('evalActiveRun');
      }
    })
    .catch(() => {});
}
```

## Constraints

- No new dependencies — Preact `useState` only
- Hook lives in `src/hooks/useActionFeedback.js`
- CSS classes use existing design token variables
- Each button gets its own hook instance (not shared state)
- `// What it shows` / `// Decision it drives` comments required on hook file
