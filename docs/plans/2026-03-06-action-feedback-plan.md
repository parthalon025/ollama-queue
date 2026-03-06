# Action Feedback — Inline Status for All Buttons

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Every non-immediate button in the SPA shows exactly what is happening — loading state while in-flight, a specific success message (e.g. "Run #12 started", "Job #6350 queued"), or a specific error message.

**Architecture:** A `useActionFeedback` hook manages `idle → loading → success/error → idle` state. Each button gets its own hook instance. Success message is a string or `(result) => string` callback so API response data (job_id, run_id, etc.) can surface to the user. Inline status line appears below the button in all states.

**Tech Stack:** Preact + preact/hooks `useState`. No new dependencies. CSS uses existing design token variables.

---

### Task 1: Create `useActionFeedback` hook + CSS

**Files:**
- Create: `ollama_queue/dashboard/spa/src/hooks/useActionFeedback.js`
- Modify: `ollama_queue/dashboard/spa/src/bundle.css` (add 3 CSS classes at end)

**Step 1: Create the hook file**

```js
// What it shows: Nothing directly — pure logic hook that tracks loading/success/error state
//   for a single async action button.
// Decision it drives: Lets every action button show exactly what is happening — "Cancelling…",
//   "Run #12 started", "Cancel failed: already complete" — without duplicating state boilerplate.
import { useState } from 'preact/hooks';

export function useActionFeedback() {
  const [state, setState] = useState({ phase: 'idle', msg: '' });

  async function run(loadingLabel, fn, successLabel) {
    setState({ phase: 'loading', msg: loadingLabel });
    try {
      const result = await fn();
      const msg = typeof successLabel === 'function'
        ? successLabel(result)
        : (successLabel || 'Done');
      setState({ phase: 'success', msg });
      setTimeout(() => setState({ phase: 'idle', msg: '' }), 3000);
    } catch (e) {
      setState({ phase: 'error', msg: e.message || 'Failed' });
    }
  }

  return [state, run];
}
```

**Step 2: Add CSS classes to bundle.css**

Append to the end of `ollama_queue/dashboard/spa/src/bundle.css`:

```css
/* Action feedback inline status */
.action-fb { font-size: var(--type-label); margin-top: 4px; min-height: 1.2em; }
.action-fb--loading { color: var(--text-muted, #888); }
.action-fb--success { color: var(--status-ok, #4caf50); }
.action-fb--error   { color: var(--status-error, #f44336); white-space: pre-wrap; }
```

**Step 3: Build to verify no syntax errors**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -5
```
Expected: `dist/bundle.js  NNNkb — Done in NNms`

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/hooks/useActionFeedback.js ollama_queue/dashboard/spa/src/bundle.css
git commit -m "feat(spa): add useActionFeedback hook + CSS for inline action status"
```

---

### Task 2: Fix stale `evalActiveRun` sessionStorage

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**Problem:** If the service restarts while a run is active, `evalActiveRun` stays in sessionStorage showing a live progress panel for a run that is now `failed`. The panel is stuck and there's no way to dismiss it.

**Step 1: Read current store.js evalActiveRun init block (lines 49-54)**

```js
// Current code (do not touch this line):
export const evalActiveRun = signal(
  (() => { try { const v = sessionStorage.getItem('evalActiveRun'); return v ? JSON.parse(v) : null; } catch { return null; } })()
);
```

**Step 2: Add startup staleness check immediately after the evalActiveRun signal declaration**

Find the line:
```js
export const evalActiveRun = signal(
```

After that entire signal declaration (the closing `);`), add:

```js
// On startup: verify stored active run is still live — clear it if the API says it's terminal.
// This prevents a stale "Run #N generating" panel persisting after a service restart.
if (evalActiveRun.value) {
  const _storedId = evalActiveRun.value.run_id;
  fetch(`${API}/eval/runs/${_storedId}/progress`)
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (!data || ['complete', 'failed', 'cancelled'].includes(data.status)) {
        evalActiveRun.value = null;
        sessionStorage.removeItem('evalActiveRun');
      }
    })
    .catch(() => {
      // If API is unreachable, leave the stored run — it may come back
    });
}
```

**Step 3: Build and verify**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/store.js
git commit -m "fix(spa): clear stale evalActiveRun from sessionStorage on startup"
```

---

### Task 3: `ActiveRunProgress.jsx` — Cancel, Resume, RetryFailed

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/ActiveRunProgress.jsx`

**Step 1: Add import at top of file (after existing imports)**

```js
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
```

**Step 2: Replace the three handler functions and their buttons**

Find and replace the three handler functions (currently lines ~55-68):

```js
// OLD:
async function handleCancel() {
  if (!confirm('Cancel this eval run? In-progress jobs will still complete.')) return;
  await cancelEvalRun(run_id);
}
async function handleResume() {
  await fetch(`/api/eval/runs/${run_id}/resume`, { method: 'POST' });
  startEvalPoll(run_id);
}
async function handleRetryFailed() {
  await fetch(`/api/eval/runs/${run_id}/retry-failed`, { method: 'POST' });
  startEvalPoll(run_id);
}
```

Replace with:

```js
const [cancelFb, cancelAct] = useActionFeedback();
const [resumeFb, resumeAct] = useActionFeedback();
const [retryFb, retryAct] = useActionFeedback();

async function handleCancel() {
  if (!confirm('Cancel this eval run? In-progress jobs will still complete.')) return;
  await cancelAct('Cancelling…', () => cancelEvalRun(run_id), `Run #${run_id} cancelled`);
}
async function handleResume() {
  await resumeAct(
    'Resuming…',
    async () => {
      const res = await fetch(`${API}/eval/runs/${run_id}/resume`, { method: 'POST' });
      if (!res.ok) throw new Error(`Resume failed: ${res.status}`);
      startEvalPoll(run_id);
    },
    'Resumed'
  );
}
async function handleRetryFailed() {
  await retryAct(
    'Retrying failed…',
    async () => {
      const res = await fetch(`${API}/eval/runs/${run_id}/retry-failed`, { method: 'POST' });
      if (!res.ok) throw new Error(`Retry failed: ${res.status}`);
      startEvalPoll(run_id);
    },
    'Retry queued'
  );
}
```

**Step 3: Add feedback lines below each button in the circuit breaker banner JSX**

Find the three buttons in the `{isPaused && (...)}` block and wrap each one:

```jsx
<div>
  <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
    disabled={resumeFb.phase === 'loading'} onClick={handleResume}>
    {resumeFb.phase === 'loading' ? 'Resuming…' : 'Resume anyway'}
  </button>
  {resumeFb.msg && <div class={`action-fb action-fb--${resumeFb.phase}`}>{resumeFb.msg}</div>}
</div>
<div>
  <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
    disabled={retryFb.phase === 'loading'} onClick={handleRetryFailed}>
    {retryFb.phase === 'loading' ? 'Retrying…' : 'Retry failed'}
  </button>
  {retryFb.msg && <div class={`action-fb action-fb--${retryFb.phase}`}>{retryFb.msg}</div>}
</div>
<div>
  <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
    disabled={cancelFb.phase === 'loading'} onClick={handleCancel}>
    {cancelFb.phase === 'loading' ? 'Cancelling…' : 'Cancel'}
  </button>
  {cancelFb.msg && <div class={`action-fb action-fb--${cancelFb.phase}`}>{cancelFb.msg}</div>}
</div>
```

Also find the second Cancel button (around line 161) and apply the same pattern:

```jsx
<div>
  <button ... disabled={cancelFb.phase === 'loading'} onClick={handleCancel}>
    {cancelFb.phase === 'loading' ? 'Cancelling…' : 'Cancel Run'}
  </button>
  {cancelFb.msg && <div class={`action-fb action-fb--${cancelFb.phase}`}>{cancelFb.msg}</div>}
</div>
```

**Step 4: Build and verify**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/ActiveRunProgress.jsx
git commit -m "feat(spa): action feedback on Cancel/Resume/RetryFailed in ActiveRunProgress"
```

---

### Task 4: `RunTriggerPanel.jsx` — Start Run

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`

**Step 1: Add import**

```js
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
```

**Step 2: Read the handleSubmit function (around line 46)**

The current pattern uses local `submitting` state. Replace with `useActionFeedback`:

Find:
```js
const [submitting, setSubmitting] = useState(false);
const [submitError, setSubmitError] = useState(null);
```

Replace with:
```js
const [fb, act] = useActionFeedback();
```

**Step 3: Replace handleSubmit**

Find the `async function handleSubmit(e)` block and replace its body to use `act`:

```js
async function handleSubmit(e) {
  e.preventDefault();
  await act(
    'Starting run…',
    async () => {
      const body = buildBody();
      const result = await triggerEvalRun(body);
      // Set active run for live polling
      const activeState = { run_id: result.run_id, status: 'pending' };
      evalActiveRun.value = activeState;
      sessionStorage.setItem('evalActiveRun', JSON.stringify(activeState));
      startEvalPoll(result.run_id);
      await fetchEvalRuns();
      return result;
    },
    result => `Run #${result.run_id} started`
  );
}
```

Note: `buildBody()` extracts the existing form-to-body logic from the original handleSubmit.

**Step 4: Update the Submit button and add feedback line**

Find the submit button and replace:
```jsx
<button type="submit" class="t-btn t-btn-primary" disabled={fb.phase === 'loading'}>
  {fb.phase === 'loading' ? 'Starting…' : 'Start Run'}
</button>
{fb.msg && <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>}
```

**Step 5: Build and commit**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
git add ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx
git commit -m "feat(spa): action feedback on Start Run in RunTriggerPanel"
```

---

### Task 5: `RunRow.jsx` — Repeat + Judge Rerun

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx`

**Step 1: Add import**

```js
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
```

**Step 2: Replace Repeat handler**

Find the existing `repeating`/`repeatError` state and `handleRepeat` function. Replace:

```js
// Remove: const [repeating, setRepeating] = useState(false);
// Remove: const [repeatError, setRepeatError] = useState(null);
const [repeatFb, repeatAct] = useActionFeedback();

async function handleRepeat(evt) {
  evt.stopPropagation();
  await repeatAct(
    'Repeating run…',
    async () => {
      const res = await fetch(`${API}/eval/runs/${id}/repeat`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Repeat failed');
      evalSubTab.value = 'runs';
      const activeState = { run_id: data.run_id, status: 'pending' };
      evalActiveRun.value = activeState;
      sessionStorage.setItem('evalActiveRun', JSON.stringify(activeState));
      startEvalPoll(data.run_id);
      await fetchEvalRuns();
      return data;
    },
    data => `Run #${data.run_id} started`
  );
}
```

**Step 3: Check for judge-rerun button and apply same pattern**

Look for any judge-rerun button in RunRow.jsx. If it exists, add:
```js
const [judgeFb, judgeAct] = useActionFeedback();

async function handleJudgeRerun(evt) {
  evt.stopPropagation();
  await judgeAct(
    'Re-judging…',
    async () => {
      const res = await fetch(`${API}/eval/runs/${id}/judge-rerun`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Judge rerun failed');
      await fetchEvalRuns();
      return data;
    },
    data => `Re-judge run #${data.run_id} started`
  );
}
```

**Step 4: Update Repeat button JSX**

```jsx
<div>
  <button class="t-btn t-btn-secondary" disabled={repeatFb.phase === 'loading'} onClick={handleRepeat}>
    {repeatFb.phase === 'loading' ? 'Repeating…' : 'Repeat'}
  </button>
  {repeatFb.msg && <div class={`action-fb action-fb--${repeatFb.phase}`}>{repeatFb.msg}</div>}
</div>
```

Remove any existing error display that used `repeatError`.

**Step 5: Build and commit**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
git add ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx
git commit -m "feat(spa): action feedback on Repeat/JudgeRerun in RunRow"
```

---

### Task 6: `VariantToolbar.jsx`, `VariantRow.jsx`, `TemplateRow.jsx`

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/VariantToolbar.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/eval/TemplateRow.jsx`

**Step 1: VariantToolbar.jsx — Generate Variants button**

Add import, replace `handleGenerateConfirm`:

```js
import { useActionFeedback } from '../../hooks/useActionFeedback.js';

// In component:
const [genFb, genAct] = useActionFeedback();

async function handleGenerateConfirm() {
  setGenerating(true);
  try {
    await genAct(
      'Generating variants…',
      async () => {
        const res = await fetch(`${API}/eval/variants/generate`, { method: 'POST' });
        if (!res.ok) throw new Error(`Generate failed: ${res.status}`);
        const data = await res.json();
        await fetchEvalVariants();
        setGenPreview(null);
        return data;
      },
      data => `${data.created ?? 'Variants'} generated`
    );
  } finally {
    setGenerating(false);
  }
}
```

Button:
```jsx
<button class="t-btn t-btn-primary" disabled={genFb.phase === 'loading'} onClick={handleGenerateConfirm}>
  {genFb.phase === 'loading' ? 'Generating…' : 'Generate'}
</button>
{genFb.msg && <div class={`action-fb action-fb--${genFb.phase}`}>{genFb.msg}</div>}
```

**Step 2: VariantRow.jsx — Delete button**

Find the delete handler. Add:
```js
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
const [deleteFb, deleteAct] = useActionFeedback();

async function handleDelete(evt) {
  evt.stopPropagation();
  if (!confirm(`Delete variant "${name}"?`)) return;
  await deleteAct(
    'Deleting…',
    async () => {
      const res = await fetch(`${API}/eval/variants/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
      await fetchEvalVariants();
    },
    `Variant "${name}" deleted`
  );
}
```

**Step 3: TemplateRow.jsx — Delete button**

Same pattern as VariantRow but for templates:
```js
const [deleteFb, deleteAct] = useActionFeedback();

async function handleDelete(evt) {
  evt.stopPropagation();
  if (!confirm(`Delete template "${name}"?`)) return;
  await deleteAct(
    'Deleting…',
    async () => {
      const res = await fetch(`${API}/eval/templates/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
      await fetchEvalVariants();
    },
    `Template "${name}" deleted`
  );
}
```

**Step 4: Build and commit**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
git add ollama_queue/dashboard/spa/src/components/eval/VariantToolbar.jsx \
        ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx \
        ollama_queue/dashboard/spa/src/components/eval/TemplateRow.jsx
git commit -m "feat(spa): action feedback on Generate/Delete in Variant and Template rows"
```

---

### Task 7: `Settings.jsx` — Pause, Resume, Save

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Settings.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/SettingsForm.jsx`

**Step 1: Settings.jsx — replace Pause/Resume handlers**

Add import and replace handlers:

```js
import { useActionFeedback } from '../hooks/useActionFeedback.js';

// In component:
const [pauseFb, pauseAct] = useActionFeedback();
const [resumeFb, resumeAct] = useActionFeedback();

const handlePause = useCallback(async () => {
  await pauseAct(
    'Pausing daemon…',
    async () => {
      const res = await fetch(`${API}/daemon/pause`, { method: 'POST' });
      if (!res.ok) throw new Error(`Pause failed: ${res.status}`);
    },
    'Daemon paused'
  );
}, []);

const handleResume = useCallback(async () => {
  await resumeAct(
    'Resuming daemon…',
    async () => {
      const res = await fetch(`${API}/daemon/resume`, { method: 'POST' });
      if (!res.ok) throw new Error(`Resume failed: ${res.status}`);
    },
    'Daemon resumed'
  );
}, []);
```

Pass `pauseFb` and `resumeFb` as props to `SettingsForm` so it can display them.

**Step 2: SettingsForm.jsx — show feedback under Pause/Resume buttons**

Find the Pause and Resume buttons and add feedback display. Pass `pauseFb`/`resumeFb` as props:

```jsx
// Props: { ..., pauseFb, resumeFb, saveFb }
<div>
  <button disabled={pauseFb?.phase === 'loading'} onClick={onPause}>
    {pauseFb?.phase === 'loading' ? 'Pausing…' : 'Pause Daemon'}
  </button>
  {pauseFb?.msg && <div class={`action-fb action-fb--${pauseFb.phase}`}>{pauseFb.msg}</div>}
</div>
<div>
  <button disabled={resumeFb?.phase === 'loading'} onClick={onResume}>
    {resumeFb?.phase === 'loading' ? 'Resuming…' : 'Resume Daemon'}
  </button>
  {resumeFb?.msg && <div class={`action-fb action-fb--${resumeFb.phase}`}>{resumeFb.msg}</div>}
</div>
```

For Save buttons in SettingsForm, add local `useActionFeedback` instances per setting group.

**Step 3: Build and commit**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
git add ollama_queue/dashboard/spa/src/pages/Settings.jsx \
        ollama_queue/dashboard/spa/src/components/SettingsForm.jsx
git commit -m "feat(spa): action feedback on Pause/Resume/Save in Settings"
```

---

### Task 8: `SubmitJobModal.jsx`

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx`

**Step 1: Add import**

```js
import { useActionFeedback } from '../hooks/useActionFeedback.js';
```

**Step 2: Replace submit state**

The component already has `submitting`/`error` state. Add feedback hook alongside (keep validation error separate):

```js
const [fb, act] = useActionFeedback();
```

**Step 3: Update handleSubmit**

```js
async function handleSubmit(e) {
  e.preventDefault();
  const validationError = validate();
  if (validationError) { setError(validationError); return; }
  setError(null);
  await act(
    'Submitting job…',
    async () => {
      const body = { command: command.trim(), source: source.trim(),
                     priority: Number(priority), timeout: Number(timeout) };
      if (model.trim()) body.model = model.trim();
      const result = await submitJob(body);
      if (onJobSubmitted) onJobSubmitted(result.job_id);
      return result;
    },
    result => `Job #${result.job_id} queued`
  );
  // Close modal only on success
  if (fb.phase === 'success') { setOpen(false); resetForm(); }
}
```

Note: since `act` is async and resolves before the timeout clears, check the phase after `await act(...)`. Actually, close the modal inside the success callback:

```js
async function handleSubmit(e) {
  e.preventDefault();
  const validationError = validate();
  if (validationError) { setError(validationError); return; }
  setError(null);
  await act(
    'Submitting job…',
    async () => {
      const body = { command: command.trim(), source: source.trim(),
                     priority: Number(priority), timeout: Number(timeout) };
      if (model.trim()) body.model = model.trim();
      const result = await submitJob(body);
      if (onJobSubmitted) onJobSubmitted(result.job_id);
      // Close modal after brief success display
      setTimeout(() => { setOpen(false); resetForm(); }, 1500);
      return result;
    },
    result => `Job #${result.job_id} queued`
  );
}
```

**Step 4: Add feedback line near submit button**

```jsx
<button type="submit" disabled={fb.phase === 'loading'}>
  {fb.phase === 'loading' ? 'Submitting…' : 'Submit'}
</button>
{fb.msg && <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>}
```

**Step 5: Build and commit**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
git add ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx
git commit -m "feat(spa): action feedback on Submit Job modal"
```

---

### Task 9: `Plan.jsx` — All recurring job actions

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan.jsx`

**Actions to cover:** handleDelete, handleRunNow, handlePinToggle, handleBatchRun, handleBatchToggle, handleRebalance, handleReenableJob, handleDetailSave, handleGenerateDescription

**Step 1: Add import**

```js
import { useActionFeedback } from '../hooks/useActionFeedback.js';
```

**Step 2: Add one hook instance per action type**

```js
const [deleteFb, deleteAct] = useActionFeedback();
const [runNowFb, runNowAct] = useActionFeedback();
const [pinFb, pinAct] = useActionFeedback();
const [batchRunFb, batchRunAct] = useActionFeedback();
const [batchToggleFb, batchToggleAct] = useActionFeedback();
const [rebalanceFb, rebalanceAct] = useActionFeedback();
const [reenableFb, reenableAct] = useActionFeedback();
const [saveFb, saveAct] = useActionFeedback();
const [descFb, descAct] = useActionFeedback();
```

**Step 3: Wrap each handler with its act function**

Pattern for each (example — handleRunNow):

```js
async function handleRunNow(rj) {
  await runNowAct(
    `Triggering ${rj.name}…`,
    async () => {
      const res = await fetch(`${API}/schedule/${rj.id}/run-now`, { method: 'POST' });
      if (!res.ok) throw new Error(`Run now failed: ${res.status}`);
      await fetchPlanData(); // refresh
    },
    `${rj.name} triggered`
  );
}

async function handleDelete(rjId) {
  const rj = jobs.find(j => j.id === rjId);
  if (!confirm(`Delete "${rj?.name}"?`)) return;
  await deleteAct(
    'Deleting…',
    async () => {
      const res = await fetch(`${API}/schedule/${rjId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
      await fetchPlanData();
    },
    `Deleted`
  );
}

async function handleRebalance() {
  await rebalanceAct(
    'Rebalancing…',
    async () => {
      const res = await fetch(`${API}/schedule/rebalance`, { method: 'POST' });
      if (!res.ok) throw new Error(`Rebalance failed: ${res.status}`);
      await fetchPlanData();
    },
    'Schedule rebalanced'
  );
}
```

Apply same pattern to remaining handlers.

**Step 4: Add feedback lines near action buttons**

For each button, add:
```jsx
<div>
  <button disabled={runNowFb.phase === 'loading'} onClick={() => handleRunNow(rj)}>
    {runNowFb.phase === 'loading' ? 'Triggering…' : 'Run Now'}
  </button>
  {runNowFb.msg && <div class={`action-fb action-fb--${runNowFb.phase}`}>{runNowFb.msg}</div>}
</div>
```

Add the Rebalance feedback below the rebalance button:
```jsx
{rebalanceFb.msg && <div class={`action-fb action-fb--${rebalanceFb.phase}`}>{rebalanceFb.msg}</div>}
```

**Step 5: Build and commit**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git commit -m "feat(spa): action feedback on all recurring job actions in Plan"
```

---

### Task 10: `History.jsx` — DLQ actions

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/History.jsx`

**Actions:** handleRetryAll, handleClearDLQ, retryDLQEntry (per-row), dismissDLQEntry (per-row)

**Step 1: Add import**

```js
import { useActionFeedback } from '../hooks/useActionFeedback.js';
```

**Step 2: Add hook instances**

```js
const [retryAllFb, retryAllAct] = useActionFeedback();
const [clearFb, clearAct] = useActionFeedback();
```

For per-row retry/dismiss, the hook must be in a sub-component (hooks can't be in callbacks). Create an inline `DLQEntryRow` component that has its own hook instance.

```jsx
// What it shows: A single DLQ entry with Retry and Dismiss action buttons and inline feedback.
// Decision it drives: User can retry a specific failed job or dismiss it from the queue.
function DLQEntryRow({ entry, onAction }) {
  const [retryFb, retryAct] = useActionFeedback();
  const [dismissFb, dismissAct] = useActionFeedback();

  return (
    <tr>
      {/* existing cells */}
      <td>
        <div>
          <button disabled={retryFb.phase === 'loading'}
            onClick={() => retryAct('Retrying…', () => onAction('retry', entry.id), 'Queued for retry')}>
            {retryFb.phase === 'loading' ? 'Retrying…' : 'Retry'}
          </button>
          {retryFb.msg && <div class={`action-fb action-fb--${retryFb.phase}`}>{retryFb.msg}</div>}
        </div>
        <div>
          <button disabled={dismissFb.phase === 'loading'}
            onClick={() => dismissAct('Dismissing…', () => onAction('dismiss', entry.id), 'Dismissed')}>
            {dismissFb.phase === 'loading' ? 'Dismissing…' : 'Dismiss'}
          </button>
          {dismissFb.msg && <div class={`action-fb action-fb--${dismissFb.phase}`}>{dismissFb.msg}</div>}
        </div>
      </td>
    </tr>
  );
}
```

**Step 3: Update bulk action handlers**

```js
async function handleRetryAll() {
  await retryAllAct(
    'Retrying all…',
    async () => {
      const res = await fetch(`${API}/dlq/retry-all`, { method: 'POST' });
      if (!res.ok) throw new Error(`Retry all failed: ${res.status}`);
      const data = await res.json();
      await fetchDLQ();
      return data;
    },
    data => `${data.retried ?? 'All'} entries requeued`
  );
}

async function handleClearDLQ() {
  if (!confirm('Clear all DLQ entries?')) return;
  await clearAct(
    'Clearing DLQ…',
    async () => {
      const res = await fetch(`${API}/dlq`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Clear failed: ${res.status}`);
      await fetchDLQ();
    },
    'DLQ cleared'
  );
}
```

**Step 4: Update bulk action buttons JSX**

```jsx
<div>
  <button disabled={retryAllFb.phase === 'loading'} onClick={handleRetryAll}>
    {retryAllFb.phase === 'loading' ? 'Retrying all…' : 'Retry All'}
  </button>
  {retryAllFb.msg && <div class={`action-fb action-fb--${retryAllFb.phase}`}>{retryAllFb.msg}</div>}
</div>
<div>
  <button disabled={clearFb.phase === 'loading'} onClick={handleClearDLQ}>
    {clearFb.phase === 'loading' ? 'Clearing…' : 'Clear DLQ'}
  </button>
  {clearFb.msg && <div class={`action-fb action-fb--${clearFb.phase}`}>{clearFb.msg}</div>}
</div>
```

**Step 5: Build and commit**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -3
git add ollama_queue/dashboard/spa/src/pages/History.jsx
git commit -m "feat(spa): action feedback on DLQ Retry/Dismiss/RetryAll/Clear in History"
```

---

### Task 11: Final build, deploy, verify

**Step 1: Run full Python test suite (unchanged — no Python was modified)**

```bash
cd /home/justin/Documents/projects/ollama-queue
.venv/bin/python -m pytest --timeout=120 -x -q 2>&1 | tail -5
```
Expected: `535 passed`

**Step 2: Final SPA build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -5
```
Expected: bundle builds, no errors.

**Step 3: Restart service**

```bash
systemctl --user restart ollama-queue && sleep 2 && systemctl --user status ollama-queue --no-pager | head -4
```

**Step 4: Smoke test — verify 5 endpoints**

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:7683/ui/ && echo
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:7683/ui/bundle.js && echo
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:7683/api/eval/runs && echo
```
All expected: `200`

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat(spa): action feedback complete — all buttons show exact loading/success/error state"
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `src/hooks/useActionFeedback.js` | New — shared hook |
| `src/bundle.css` | +4 lines CSS |
| `src/store.js` | +10 lines startup staleness check |
| `src/components/eval/ActiveRunProgress.jsx` | Cancel/Resume/Retry feedback |
| `src/components/eval/RunTriggerPanel.jsx` | Start Run feedback |
| `src/components/eval/RunRow.jsx` | Repeat/JudgeRerun feedback |
| `src/components/eval/VariantToolbar.jsx` | Generate feedback |
| `src/components/eval/VariantRow.jsx` | Delete feedback |
| `src/components/eval/TemplateRow.jsx` | Delete feedback |
| `src/pages/Settings.jsx` | Pause/Resume feedback |
| `src/components/SettingsForm.jsx` | Pause/Resume/Save feedback display |
| `src/components/SubmitJobModal.jsx` | Submit feedback |
| `src/pages/Plan.jsx` | 9 action handler feedbacks |
| `src/pages/History.jsx` | DLQ Retry/Dismiss/RetryAll/Clear |
