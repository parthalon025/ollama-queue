# UI Endpoint Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire 6 unexposed API endpoints into the dashboard SPA — submit job FAB/modal, load map density strip, add recurring job modal, re-enable button, and proxy mini-stat.

**Architecture:** Four new JSX components (`SubmitJobModal`, `LoadMapStrip`, `AddRecurringJobModal`, and inline re-enable logic in Plan.jsx). Four new store functions + one new signal. All frontend-only except `fetchLoadMap()` which calls an existing backend endpoint that was never wired up.

**Tech Stack:** Preact 10, @preact/signals, Tailwind v4, native `<dialog>` element, existing `.t-frame` / `.t-status` CSS vocabulary. Tests via pytest (API-level). Build via `npm run build` in `ollama_queue/dashboard/spa/`.

**Design doc:** `docs/plans/2026-03-04-ui-endpoint-integration-design.md`

**Critical notes:**
- Never name `.map()` callbacks `h` — esbuild injects `h` as JSX factory. Use descriptive names.
- Use `var(--token-name)` CSS variables, never hardcoded hex except for known gaps.
- All new signals go in `store.js` alongside existing signals at the top.
- `parseInterval()` for shorthand intervals already exists in `Plan.jsx` — reuse it in `AddRecurringJobModal`.
- `inputStyle` and `labelStyle` objects are defined at top of `Plan.jsx` — reuse pattern in new components.
- Test command: `cd /home/justin/Documents/projects/ollama-queue && source .venv/bin/activate && pytest --timeout=120 -x -q`
- Build command: `cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build`

---

## Task 1: store.js — Add loadMap signal and 4 new functions

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**What to add** (after line 18 — after `queueEtas` signal):

```js
export const loadMap = signal(null);  // /api/schedule/load-map response
```

**Four new async functions** — add at the end of store.js (after `assignModelToJob`):

```js
export async function fetchLoadMap() {
    try {
        const resp = await fetch(`${API}/schedule/load-map`);
        if (resp.ok) loadMap.value = await resp.json();
    } catch (e) {
        console.error('fetchLoadMap failed:', e);
    }
}

export async function submitJob(body) {
    const resp = await fetch(`${API}/queue/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `Submit failed: ${resp.status}`);
    }
    return resp.json(); // { job_id: N }
}

export async function addRecurringJob(body) {
    const resp = await fetch(`${API}/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `Add job failed: ${resp.status}`);
    }
    return resp.json();
}

export async function enableJobByName(name) {
    const resp = await fetch(`${API}/schedule/jobs/${encodeURIComponent(name)}/enable`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error(`Enable failed: ${resp.status}`);
    await fetchSchedule();
}
```

**Step 1:** Add the `loadMap` signal on line 19 of `store.js` (after `queueEtas`).

**Step 2:** Add the four functions at the end of `store.js` (after `assignModelToJob`, line 260, before the end).

**Step 3:** Verify the file still has no syntax errors by running the build:

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build 2>&1 | tail -20
```

Expected: build completes with no errors (warnings about bundle size OK).

**Step 4:** Commit:

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(ui): add loadMap signal + 4 store functions for unexposed endpoints"
```

---

## Task 2: API test — /api/schedule/load-map and /api/schedule POST

**Files:**
- Modify: `tests/test_api.py`

**Step 1: Write the failing tests** — add to the end of `tests/test_api.py`:

```python
def test_get_load_map(client):
    resp = client.get("/api/schedule/load-map")
    assert resp.status_code == 200
    data = resp.json()
    assert "slots" in data
    assert "slot_minutes" in data
    assert data["slot_minutes"] == 30
    assert data["count"] == len(data["slots"])


def test_add_recurring_job_minimal(client):
    resp = client.post(
        "/api/schedule",
        json={
            "name": "test-job",
            "command": "echo hello",
            "interval_seconds": 3600,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test-job"
    assert data["command"] == "echo hello"


def test_add_recurring_job_all_fields(client):
    resp = client.post(
        "/api/schedule",
        json={
            "name": "full-job",
            "command": "aria run",
            "interval_seconds": 86400,
            "model": "qwen2.5:7b",
            "priority": 3,
            "timeout": 300,
            "source": "test",
            "tag": "aria",
            "max_retries": 2,
            "resource_profile": "ollama",
            "pinned": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "full-job"
    assert data["tag"] == "aria"


def test_enable_job_by_name(client):
    # Create a recurring job first
    client.post(
        "/api/schedule",
        json={"name": "disable-me", "command": "echo x", "interval_seconds": 3600},
    )
    # Disable it
    jobs = client.get("/api/schedule").json()
    rj = next(j for j in jobs if j["name"] == "disable-me")
    client.put(f"/api/schedule/{rj['id']}", json={"enabled": False})

    # Re-enable via by-name endpoint
    resp = client.post("/api/schedule/jobs/disable-me/enable")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify re-enabled
    jobs = client.get("/api/schedule").json()
    rj = next(j for j in jobs if j["name"] == "disable-me")
    assert rj["enabled"] is True
```

**Step 2: Run to verify they pass** (these test existing backend endpoints):

```bash
cd /home/justin/Documents/projects/ollama-queue && source .venv/bin/activate && pytest tests/test_api.py::test_get_load_map tests/test_api.py::test_add_recurring_job_minimal tests/test_api.py::test_add_recurring_job_all_fields tests/test_api.py::test_enable_job_by_name -v
```

Expected: all 4 PASS (backend already implements these).

**Step 3: Commit:**

```bash
git add tests/test_api.py
git commit -m "test(api): add coverage for load-map, schedule POST, enable-by-name"
```

---

## Task 3: LoadMapStrip component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/LoadMapStrip.jsx`

**Step 1: Create the component:**

```jsx
import { h } from 'preact';

/**
 * 48-bar density strip visualizing the 48×30-min daily load slots.
 * Opacity encoding (Treisman preattentive): dark = busy, light = free.
 * Fixed height bars so Cleveland & McGill baseline comparison works.
 *
 * Props:
 *   data: { slots: number[], slot_minutes: 30, count: 48 } | null
 */
export default function LoadMapStrip({ data }) {
    if (!data || !data.slots || data.slots.length === 0) return null;

    const slots = data.slots;
    const maxLoad = Math.max(...slots, 1); // avoid div-by-zero

    // Compute opacity: min 0.12 (always visible), max 1.0
    function slotOpacity(count) {
        return 0.12 + (count / maxLoad) * 0.88;
    }

    // X-axis tick labels at 00:00, 06:00, 12:00, 18:00, 24:00
    // Slots are 0-indexed: slot 0 = 00:00–00:30, slot 12 = 06:00, etc.
    const ticks = [
        { slot: 0,  label: '00:00' },
        { slot: 12, label: '06:00' },
        { slot: 24, label: '12:00' },
        { slot: 36, label: '18:00' },
        { slot: 47, label: '24:00' },
    ];

    return (
        <div style={{ marginBottom: '0.5rem' }}>
            {/* Header row */}
            <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '0.25rem',
            }}>
                <span class="data-mono" style={{
                    fontSize: 'var(--type-label)',
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                }}>
                    Load
                </span>
                <span class="data-mono" style={{
                    fontSize: 'var(--type-label)',
                    color: 'var(--text-tertiary)',
                }}>
                    24h
                </span>
            </div>

            {/* Bar strip */}
            <div style={{
                display: 'flex',
                gap: '1px',
                height: '24px',
                alignItems: 'flex-end',
            }}>
                {slots.map((count, idx) => (
                    <div
                        key={idx}
                        title={`${String(Math.floor(idx / 2)).padStart(2, '0')}:${idx % 2 === 0 ? '00' : '30'} — ${count} job${count !== 1 ? 's' : ''}`}
                        style={{
                            flex: 1,
                            height: '100%',
                            background: 'var(--accent)',
                            opacity: slotOpacity(count),
                            borderRadius: '1px',
                        }}
                    />
                ))}
            </div>

            {/* Tick labels */}
            <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginTop: '2px',
                paddingRight: '0px',
            }}>
                {ticks.map(({ slot, label }) => (
                    <span key={label} class="data-mono" style={{
                        fontSize: '9px',
                        color: 'var(--text-tertiary)',
                        lineHeight: 1,
                    }}>
                        {label}
                    </span>
                ))}
            </div>
        </div>
    );
}
```

**Step 2: Build to verify no errors:**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build 2>&1 | tail -10
```

Expected: clean build.

**Step 3: Commit:**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/LoadMapStrip.jsx
git commit -m "feat(ui): add LoadMapStrip component — 48-slot daily density visualization"
```

---

## Task 4: SubmitJobModal component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx`

**Step 1: Create the component:**

```jsx
import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { settings, submitJob, API } from '../store';

/**
 * FAB + native <dialog> modal for submitting one-off jobs.
 *
 * Props:
 *   onJobSubmitted: (jobId: number) => void  — called after successful submit
 */
export default function SubmitJobModal({ onJobSubmitted }) {
    const dialogRef = useRef(null);
    const [open, setOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);

    const sett = settings.value;
    const defaultPriority = sett?.default_priority ?? 5;
    const defaultTimeout = sett?.default_timeout_seconds ?? 120;

    const [form, setForm] = useState({
        command: '',
        source: 'dashboard',
        model: '',
        priority: defaultPriority,
        timeout: defaultTimeout,
    });

    // Sync defaults when settings load
    useEffect(() => {
        setForm(prev => ({
            ...prev,
            priority: sett?.default_priority ?? prev.priority,
            timeout: sett?.default_timeout_seconds ?? prev.timeout,
        }));
    }, [sett?.default_priority, sett?.default_timeout_seconds]);

    function openModal() {
        setError(null);
        setOpen(true);
        // showModal() called after render via effect
    }

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        if (open) {
            dialog.showModal();
        } else {
            dialog.close();
        }
    }, [open]);

    // Close on backdrop click (native dialog click-outside)
    function handleDialogClick(e) {
        if (e.target === dialogRef.current) setOpen(false);
    }

    // Close on Escape (native dialog already handles this, but sync state)
    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        function onClose() { setOpen(false); }
        dialog.addEventListener('close', onClose);
        return () => dialog.removeEventListener('close', onClose);
    }, []);

    function setField(key, value) {
        setForm(prev => ({ ...prev, [key]: value }));
    }

    function validate() {
        if (!form.command.trim()) return 'Command is required';
        if (!form.source.trim()) return 'Source is required';
        const p = Number(form.priority);
        if (!Number.isInteger(p) || p < 0 || p > 10) return 'Priority must be an integer 0–10';
        const t = Number(form.timeout);
        if (!Number.isInteger(t) || t < 1) return 'Timeout must be a positive integer (seconds)';
        return null;
    }

    async function handleSubmit(e) {
        e.preventDefault();
        const err = validate();
        if (err) { setError(err); return; }

        setSubmitting(true);
        setError(null);
        try {
            const body = {
                command: form.command.trim(),
                source: form.source.trim(),
                priority: Number(form.priority),
                timeout: Number(form.timeout),
            };
            if (form.model.trim()) body.model = form.model.trim();

            const result = await submitJob(body);
            setOpen(false);
            if (onJobSubmitted) onJobSubmitted(result.job_id);
        } catch (err) {
            setError(err.message || 'Submit failed');
        } finally {
            setSubmitting(false);
        }
    }

    const inputStyle = {
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--type-body)',
        background: 'var(--bg-surface-raised)',
        color: 'var(--text-primary)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius)',
        padding: '0.3rem 0.5rem',
        width: '100%',
        boxSizing: 'border-box',
    };

    const labelStyle = {
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--type-label)',
        color: 'var(--text-tertiary)',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.03em',
        marginBottom: '0.2rem',
        display: 'block',
    };

    return (
        <>
            {/* FAB */}
            <button
                onClick={openModal}
                aria-label="Submit job"
                style={{
                    position: 'fixed',
                    bottom: '5rem',  // above mobile bottom nav
                    right: '1.25rem',
                    width: '44px',
                    height: '44px',
                    borderRadius: '50%',
                    background: 'var(--accent)',
                    color: 'var(--bg-base)',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '1.4rem',
                    fontWeight: 700,
                    lineHeight: 1,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
                    zIndex: 50,
                    fontFamily: 'var(--font-mono)',
                }}
            >
                +
            </button>

            {/* Modal */}
            <dialog
                ref={dialogRef}
                onClick={handleDialogClick}
                style={{
                    background: 'var(--bg-surface)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius)',
                    padding: 0,
                    width: 'min(480px, 92vw)',
                    maxHeight: '90vh',
                    overflow: 'auto',
                }}
            >
                <div
                    class="t-frame"
                    data-label="Submit Job"
                    style={{ margin: 0, border: 'none' }}
                >
                    <form onSubmit={handleSubmit}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

                            <div>
                                <label style={labelStyle}>Command *</label>
                                <textarea
                                    rows={2}
                                    style={{ ...inputStyle, resize: 'vertical' }}
                                    value={form.command}
                                    onInput={e => setField('command', e.target.value)}
                                    placeholder="echo hello"
                                    required
                                />
                            </div>

                            <div>
                                <label style={labelStyle}>Source *</label>
                                <input
                                    type="text"
                                    style={inputStyle}
                                    value={form.source}
                                    onInput={e => setField('source', e.target.value)}
                                    required
                                />
                            </div>

                            <div>
                                <label style={labelStyle}>Model (optional)</label>
                                <input
                                    type="text"
                                    style={inputStyle}
                                    value={form.model}
                                    onInput={e => setField('model', e.target.value)}
                                    placeholder="qwen2.5:7b"
                                />
                            </div>

                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                                <div>
                                    <label style={labelStyle}>Priority (0–10)</label>
                                    <input
                                        type="number"
                                        min={0} max={10}
                                        style={inputStyle}
                                        value={form.priority}
                                        onInput={e => setField('priority', e.target.value)}
                                    />
                                </div>
                                <div>
                                    <label style={labelStyle}>Timeout (s)</label>
                                    <input
                                        type="number"
                                        min={1}
                                        style={inputStyle}
                                        value={form.timeout}
                                        onInput={e => setField('timeout', e.target.value)}
                                    />
                                </div>
                            </div>

                            {error && (
                                <div style={{
                                    color: 'var(--status-error)',
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: 'var(--type-label)',
                                    padding: '0.25rem 0',
                                }}>
                                    ✕ {error}
                                </div>
                            )}

                            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '0.25rem' }}>
                                <button
                                    type="button"
                                    onClick={() => setOpen(false)}
                                    style={{
                                        fontFamily: 'var(--font-mono)',
                                        fontSize: 'var(--type-body)',
                                        background: 'transparent',
                                        border: '1px solid var(--border-subtle)',
                                        color: 'var(--text-tertiary)',
                                        padding: '0.35rem 0.75rem',
                                        borderRadius: 'var(--radius)',
                                        cursor: 'pointer',
                                    }}
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={submitting}
                                    style={{
                                        fontFamily: 'var(--font-mono)',
                                        fontSize: 'var(--type-body)',
                                        background: submitting ? 'var(--text-tertiary)' : 'var(--accent)',
                                        color: 'var(--bg-base)',
                                        border: 'none',
                                        padding: '0.35rem 0.75rem',
                                        borderRadius: 'var(--radius)',
                                        cursor: submitting ? 'wait' : 'pointer',
                                        fontWeight: 700,
                                    }}
                                >
                                    {submitting ? 'Submitting…' : 'Submit'}
                                </button>
                            </div>
                        </div>
                    </form>
                </div>
            </dialog>
        </>
    );
}
```

**Step 2: Build:**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build 2>&1 | tail -10
```

Expected: clean build.

**Step 3: Commit:**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx
git commit -m "feat(ui): add SubmitJobModal — FAB + native dialog for one-off job submission"
```

---

## Task 5: Wire SubmitJobModal and proxy mini-stat into Now.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`

**Step 1:** Add imports at the top (after existing imports):

```js
import { history, ... } from '../store';
// ADD to existing store import:
import { fetchStatus } from '../store';
import SubmitJobModal from '../components/SubmitJobModal.jsx';
```

Wait — `fetchStatus` is not exported from store.js (it's an internal async function). The correct refresh after submit is to call `fetchAll` equivalent. Looking at the store, `startPolling` and the poll cycle handle this. The right approach post-submit is to directly call `fetchStatus` but it's unexported. **Use the exported pattern instead:**

The store exports no `fetchStatus` directly. After `submitJob()` succeeds, call `fetchSchedule()` is wrong. Instead, update the `queue` signal by calling the exported `fetchQueueEtas` + noting queue is updated by `status` poll. The simplest approach: add an exported `refreshQueue` function to store:

```js
// Add to store.js (end of file):
export async function refreshQueue() {
    try {
        const [statusResp, queueResp] = await Promise.all([
            fetch(`${API}/status`),
            fetch(`${API}/queue`),
        ]);
        if (statusResp.ok) {
            const data = await statusResp.json();
            status.value = data;
            if (Array.isArray(data.queue)) queue.value = data.queue;
        }
        if (queueResp.ok) queue.value = await queueResp.json();
    } catch (e) {
        console.error('refreshQueue failed:', e);
    }
}
```

Then add `refreshQueue` to the store.js and import it in Now.jsx.

**Step 2:** Update `store.js` — add `refreshQueue` function at the end.

**Step 3:** Update `Now.jsx`:

Add `useState` to the import from `preact/hooks`:
```js
import { useState } from 'preact/hooks';
```

Add store imports:
```js
import { status, queue, history, healthData, durationData, settings,
    dlqCount, connectionStatus, currentTab, refreshQueue } from '../store';
import SubmitJobModal from '../components/SubmitJobModal.jsx';
```

Inside the `Now()` function body, add after existing signal reads:

```js
const [toast, setToast] = useState(null);

// Proxy mini-stat: count jobs in last 24h where source starts with "proxy:"
const oneDayAgo = Date.now() / 1000 - 86400;
const proxyGenerate = (hist || []).filter(
    job => job.source === 'proxy:/api/generate' && (job.completed_at ?? 0) >= oneDayAgo
).length;
const proxyEmbed = (hist || []).filter(
    job => job.source === 'proxy:/api/embed' && (job.completed_at ?? 0) >= oneDayAgo
).length;
const showProxyStat = proxyGenerate > 0 || proxyEmbed > 0;

function handleJobSubmitted(jobId) {
    setToast(`Job #${jobId} queued`);
    setTimeout(() => setToast(null), 2000);
    refreshQueue();
}
```

In the JSX, add the proxy stat below the KPI grid (after the closing `</div>` of `grid grid-cols-2`):

```jsx
{/* Proxy mini-stat — shown only when proxy calls exist in history */}
{showProxyStat && (
    <div class="data-mono" style={{
        fontSize: 'var(--type-label)',
        color: 'var(--text-tertiary)',
        paddingTop: '0.25rem',
    }}>
        proxy{' '}
        {proxyGenerate > 0 && `${proxyGenerate} generate`}
        {proxyGenerate > 0 && proxyEmbed > 0 && ' · '}
        {proxyEmbed > 0 && `${proxyEmbed} embed`}
        {' '}(last 24h)
    </div>
)}
```

Add toast and SubmitJobModal just before the closing `</div>` of the outer `flex flex-col`:

```jsx
{/* Toast notification after job submit */}
{toast && (
    <div style={{
        position: 'fixed',
        bottom: '6rem',
        right: '4.5rem',
        background: 'var(--bg-surface-raised)',
        border: '1px solid var(--status-healthy)',
        color: 'var(--status-healthy)',
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--type-label)',
        padding: '0.4rem 0.75rem',
        borderRadius: 'var(--radius)',
        zIndex: 60,
    }}>
        ✓ {toast}
    </div>
)}

<SubmitJobModal onJobSubmitted={handleJobSubmitted} />
```

**Step 4: Build:**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build 2>&1 | tail -10
```

Expected: clean.

**Step 5: Commit:**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/store.js ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(ui): wire SubmitJobModal + proxy mini-stat into Now tab"
```

---

## Task 6: AddRecurringJobModal component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx`

**Step 1:** Create the component. Note: `parseInterval` already exists in `Plan.jsx` — copy the function into this component (DRY violation acceptable since Plan.jsx is not a module export; extracting it would require refactoring Plan.jsx):

```jsx
import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { loadMap, addRecurringJob, fetchSchedule, fetchLoadMap } from '../store';

// Copied from Plan.jsx — parse interval shorthand like "4h", "30m", "1d", "90s"
function parseInterval(str) {
    if (!str) return null;
    const trimmed = str.trim().toLowerCase();
    const match = trimmed.match(/^(\d+(?:\.\d+)?)\s*(d|h|m|s)?$/);
    if (!match) return null;
    const val = parseFloat(match[1]);
    if (val <= 0 || !isFinite(val)) return null;
    const unit = match[2] || 's';
    const multipliers = { d: 86400, h: 3600, m: 60, s: 1 };
    return Math.round(val * multipliers[unit]);
}

/** Compute top-3 lightest 1h (2-slot) windows from load map slots array. */
function suggestTimes(slots) {
    if (!slots || slots.length < 2) return [];
    const windows = [];
    for (let i = 0; i < slots.length - 1; i++) {
        const load = slots[i] + slots[i + 1];
        const hour = Math.floor(i / 2);
        const half = i % 2 === 0 ? '00' : '30';
        windows.push({ slot: i, load, label: `${String(hour).padStart(2, '0')}:${half}` });
    }
    return windows.sort((a, b) => a.load - b.load).slice(0, 3);
}

/**
 * "Add Recurring Job" button + native <dialog> modal.
 * Props:
 *   onAdded: () => void — called after successful creation
 */
export default function AddRecurringJobModal({ onAdded }) {
    const dialogRef = useRef(null);
    const [open, setOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [scheduleMode, setScheduleMode] = useState('interval'); // 'interval' | 'cron'

    const lm = loadMap.value;
    const suggestions = suggestTimes(lm?.slots);

    const [form, setForm] = useState({
        name: '',
        command: '',
        interval: '1h',
        cron: '',
        model: '',
        priority: 5,
        // Advanced
        timeout: 600,
        tag: '',
        source: '',
        max_retries: 0,
        resource_profile: 'ollama',
        pinned: false,
        check_command: '',
        max_runs: '',
    });

    function openModal() {
        setError(null);
        setShowAdvanced(false);
        setOpen(true);
        fetchLoadMap(); // refresh load map when opening
    }

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        if (open) dialog.showModal();
        else dialog.close();
    }, [open]);

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        function onClose() { setOpen(false); }
        dialog.addEventListener('close', onClose);
        return () => dialog.removeEventListener('close', onClose);
    }, []);

    function handleDialogClick(e) {
        if (e.target === dialogRef.current) setOpen(false);
    }

    function setField(key, value) {
        setForm(prev => ({ ...prev, [key]: value }));
    }

    function applySuggestion(label) {
        // Parse "HH:MM" into a "pin time" — set interval to 24h and note the time as a hint
        // The actual pin time would require a cron expression or server-side pinning.
        // For now: switch to interval mode and note the suggestion visually (non-binding).
        setScheduleMode('interval');
        setField('interval', '24h');
    }

    function validate() {
        if (!form.name.trim()) return 'Name is required';
        if (!form.command.trim()) return 'Command is required';
        if (scheduleMode === 'interval') {
            const secs = parseInterval(form.interval);
            if (!secs) return 'Interval must be a valid duration (e.g. 4h, 30m, 1d)';
        } else {
            if (!form.cron.trim()) return 'Cron expression is required';
        }
        const p = Number(form.priority);
        if (!Number.isInteger(p) || p < 0 || p > 10) return 'Priority must be 0–10';
        return null;
    }

    async function handleSubmit(e) {
        e.preventDefault();
        const err = validate();
        if (err) { setError(err); return; }

        setSubmitting(true);
        setError(null);
        try {
            const body = {
                name: form.name.trim(),
                command: form.command.trim(),
                priority: Number(form.priority),
            };
            if (scheduleMode === 'interval') {
                body.interval_seconds = parseInterval(form.interval);
            } else {
                body.cron_expression = form.cron.trim();
            }
            if (form.model.trim()) body.model = form.model.trim();
            if (showAdvanced) {
                body.timeout = Number(form.timeout) || 600;
                if (form.tag.trim()) body.tag = form.tag.trim();
                if (form.source.trim()) body.source = form.source.trim();
                body.max_retries = Number(form.max_retries) || 0;
                body.resource_profile = form.resource_profile;
                body.pinned = form.pinned;
                if (form.check_command.trim()) body.check_command = form.check_command.trim();
                if (form.max_runs) body.max_runs = Number(form.max_runs);
            }

            await addRecurringJob(body);
            await fetchSchedule();
            await fetchLoadMap();
            setOpen(false);
            if (onAdded) onAdded();
        } catch (err) {
            setError(err.message || 'Failed to add job');
        } finally {
            setSubmitting(false);
        }
    }

    const inputStyle = {
        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
        background: 'var(--bg-surface-raised)', color: 'var(--text-primary)',
        border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
        padding: '0.3rem 0.5rem', width: '100%', boxSizing: 'border-box',
    };
    const labelStyle = {
        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
        color: 'var(--text-tertiary)', fontWeight: 600,
        textTransform: 'uppercase', letterSpacing: '0.03em',
        marginBottom: '0.2rem', display: 'block',
    };

    return (
        <>
            <button
                onClick={openModal}
                style={{
                    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                    background: 'transparent', border: '1px solid var(--border-subtle)',
                    color: 'var(--accent)', padding: '0.25rem 0.6rem',
                    borderRadius: 'var(--radius)', cursor: 'pointer',
                    whiteSpace: 'nowrap',
                }}
            >
                + Add Job
            </button>

            <dialog
                ref={dialogRef}
                onClick={handleDialogClick}
                style={{
                    background: 'var(--bg-surface)', color: 'var(--text-primary)',
                    border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
                    padding: 0, width: 'min(520px, 94vw)', maxHeight: '90vh', overflow: 'auto',
                }}
            >
                <div class="t-frame" data-label="Add Recurring Job" style={{ margin: 0, border: 'none' }}>
                    <form onSubmit={handleSubmit}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

                            {/* Suggested times from load map */}
                            {suggestions.length > 0 && (
                                <div>
                                    <span style={labelStyle}>Suggested times</span>
                                    <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                                        {suggestions.map((sug, idx) => (
                                            <button
                                                key={sug.label}
                                                type="button"
                                                onClick={() => applySuggestion(sug.label)}
                                                style={{
                                                    fontFamily: 'var(--font-mono)',
                                                    fontSize: 'var(--type-label)',
                                                    background: idx === 0 ? 'var(--accent)' : 'var(--bg-surface-raised)',
                                                    color: idx === 0 ? 'var(--bg-base)' : 'var(--text-secondary)',
                                                    border: '1px solid var(--border-subtle)',
                                                    padding: '0.15rem 0.5rem',
                                                    borderRadius: 'var(--radius)',
                                                    cursor: 'pointer',
                                                }}
                                            >
                                                {idx === 0 ? '★ ' : ''}{sug.label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Essential fields */}
                            <div>
                                <label style={labelStyle}>Name *</label>
                                <input type="text" style={inputStyle}
                                    value={form.name} onInput={e => setField('name', e.target.value)}
                                    placeholder="my-daily-job" required />
                            </div>

                            <div>
                                <label style={labelStyle}>Command *</label>
                                <textarea rows={2} style={{ ...inputStyle, resize: 'vertical' }}
                                    value={form.command} onInput={e => setField('command', e.target.value)}
                                    placeholder="aria run" required />
                            </div>

                            <div>
                                <label style={labelStyle}>Schedule *</label>
                                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.4rem' }}>
                                    <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                        <input type="radio" name="scheduleMode" value="interval"
                                            checked={scheduleMode === 'interval'}
                                            onChange={() => setScheduleMode('interval')} />{' '}
                                        Interval
                                    </label>
                                    <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                        <input type="radio" name="scheduleMode" value="cron"
                                            checked={scheduleMode === 'cron'}
                                            onChange={() => setScheduleMode('cron')} />{' '}
                                        Cron
                                    </label>
                                </div>
                                {scheduleMode === 'interval' ? (
                                    <input type="text" style={inputStyle}
                                        value={form.interval} onInput={e => setField('interval', e.target.value)}
                                        placeholder="4h · 30m · 1d" />
                                ) : (
                                    <input type="text" style={inputStyle}
                                        value={form.cron} onInput={e => setField('cron', e.target.value)}
                                        placeholder="0 3 * * *" />
                                )}
                            </div>

                            <div>
                                <label style={labelStyle}>Model (optional)</label>
                                <input type="text" style={inputStyle}
                                    value={form.model} onInput={e => setField('model', e.target.value)}
                                    placeholder="qwen2.5:7b" />
                            </div>

                            <div>
                                <label style={labelStyle}>Priority (0–10)</label>
                                <input type="number" min={0} max={10} style={inputStyle}
                                    value={form.priority} onInput={e => setField('priority', e.target.value)} />
                            </div>

                            {/* Advanced toggle */}
                            <button
                                type="button"
                                onClick={() => setShowAdvanced(v => !v)}
                                style={{
                                    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                    background: 'none', border: 'none',
                                    color: 'var(--text-tertiary)', cursor: 'pointer',
                                    textAlign: 'left', padding: '0.1rem 0',
                                }}
                            >
                                {showAdvanced ? '▼' : '▶'} Advanced options
                            </button>

                            {showAdvanced && (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', paddingLeft: '0.5rem', borderLeft: '2px solid var(--border-subtle)' }}>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                                        <div>
                                            <label style={labelStyle}>Timeout (s)</label>
                                            <input type="number" min={1} style={inputStyle}
                                                value={form.timeout} onInput={e => setField('timeout', e.target.value)} />
                                        </div>
                                        <div>
                                            <label style={labelStyle}>Max Retries</label>
                                            <input type="number" min={0} style={inputStyle}
                                                value={form.max_retries} onInput={e => setField('max_retries', e.target.value)} />
                                        </div>
                                    </div>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                                        <div>
                                            <label style={labelStyle}>Tag</label>
                                            <input type="text" style={inputStyle}
                                                value={form.tag} onInput={e => setField('tag', e.target.value)}
                                                placeholder="aria" />
                                        </div>
                                        <div>
                                            <label style={labelStyle}>Source</label>
                                            <input type="text" style={inputStyle}
                                                value={form.source} onInput={e => setField('source', e.target.value)} />
                                        </div>
                                    </div>
                                    <div>
                                        <label style={labelStyle}>Resource Profile</label>
                                        <select style={inputStyle}
                                            value={form.resource_profile}
                                            onChange={e => setField('resource_profile', e.target.value)}>
                                            <option value="ollama">ollama</option>
                                            <option value="embed">embed</option>
                                            <option value="heavy">heavy</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label style={labelStyle}>Check Command</label>
                                        <input type="text" style={inputStyle}
                                            value={form.check_command} onInput={e => setField('check_command', e.target.value)}
                                            placeholder="test -f /tmp/ready" />
                                    </div>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                                        <div>
                                            <label style={labelStyle}>Max Runs</label>
                                            <input type="number" min={1} style={inputStyle}
                                                value={form.max_runs} onInput={e => setField('max_runs', e.target.value)}
                                                placeholder="unlimited" />
                                        </div>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '1.4rem' }}>
                                            <input type="checkbox" id="pinned-check"
                                                checked={form.pinned}
                                                onChange={e => setField('pinned', e.target.checked)} />
                                            <label for="pinned-check" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                                Pinned
                                            </label>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {error && (
                                <div style={{ color: 'var(--status-error)', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}>
                                    ✕ {error}
                                </div>
                            )}

                            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '0.25rem' }}>
                                <button type="button" onClick={() => setOpen(false)}
                                    style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', background: 'transparent', border: '1px solid var(--border-subtle)', color: 'var(--text-tertiary)', padding: '0.35rem 0.75rem', borderRadius: 'var(--radius)', cursor: 'pointer' }}>
                                    Cancel
                                </button>
                                <button type="submit" disabled={submitting}
                                    style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', background: submitting ? 'var(--text-tertiary)' : 'var(--accent)', color: 'var(--bg-base)', border: 'none', padding: '0.35rem 0.75rem', borderRadius: 'var(--radius)', cursor: submitting ? 'wait' : 'pointer', fontWeight: 700 }}>
                                    {submitting ? 'Adding…' : 'Add Job'}
                                </button>
                            </div>
                        </div>
                    </form>
                </div>
            </dialog>
        </>
    );
}
```

**Step 2: Build:**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build 2>&1 | tail -10
```

Expected: clean.

**Step 3: Commit:**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx
git commit -m "feat(ui): add AddRecurringJobModal — create recurring jobs from dashboard"
```

---

## Task 7: Wire LoadMapStrip, AddRecurringJobModal, and re-enable into Plan.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan.jsx`

**Step 1:** Add imports at the top of Plan.jsx (add to existing imports):

```js
import { loadMap, fetchLoadMap, enableJobByName } from '../store';
import LoadMapStrip from '../components/LoadMapStrip.jsx';
import AddRecurringJobModal from '../components/AddRecurringJobModal.jsx';
```

**Step 2:** In the `useEffect` that calls `fetchSchedule()` + `fetchModels()`, also call `fetchLoadMap()`:

Find:
```js
useEffect(() => {
    fetchSchedule();
    fetchModels();
```

Replace with:
```js
useEffect(() => {
    fetchSchedule();
    fetchModels();
    fetchLoadMap();
```

**Step 3:** Add `enableJobByName` handler in Plan function body (alongside other handlers):

```js
async function handleReenableJob(name) {
    try {
        await enableJobByName(name);
    } catch (e) {
        setRunError(`Re-enable failed: ${e.message}`);
    }
}
```

**Step 4:** Find the section in Plan.jsx where the Gantt chart is rendered (look for `<GanttChart`) and add `<LoadMapStrip>` above it:

```jsx
{/* Load map density strip — 48-slot daily load visualization */}
<LoadMapStrip data={loadMap.value} />

<GanttChart ... />
```

**Step 5:** Find the Plan tab header row that has the Rebalance button and add `<AddRecurringJobModal>` alongside it. Search for `triggerRebalance` or `Rebalance` in Plan.jsx to find the button, and add the modal component next to it.

**Step 6:** Find the enabled toggle cell in the job row rendering. Search for `toggleScheduleJob` in Plan.jsx. The toggle is rendered for each job row. Modify the enabled column to check for `outcome_reason`:

Find the pattern (look for the enabled toggle render — it uses `toggleScheduleJob(rj.id, !rj.enabled)`). Wrap it:

```jsx
{rj.outcome_reason && !rj.enabled ? (
    // Auto-disabled by daemon — show reason + re-enable button
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', alignItems: 'flex-start' }}>
        <span class="t-status t-status-warning" style={{ fontSize: '9px', whiteSpace: 'normal', maxWidth: '8rem' }}>
            {rj.outcome_reason}
        </span>
        <button
            onClick={() => handleReenableJob(rj.name)}
            style={{
                fontFamily: 'var(--font-mono)', fontSize: '9px',
                background: 'transparent', border: '1px solid var(--status-warning)',
                color: 'var(--status-warning)', padding: '0.1rem 0.3rem',
                borderRadius: 'var(--radius)', cursor: 'pointer',
            }}
        >
            Re-enable
        </button>
    </div>
) : (
    // Normal toggle
    <input
        type="checkbox"
        checked={rj.enabled}
        onChange={() => toggleScheduleJob(rj.id, !rj.enabled)}
    />
)}
```

**Step 7:** Build:

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build 2>&1 | tail -15
```

Expected: clean build.

**Step 8:** Run all tests to verify nothing broke:

```bash
cd /home/justin/Documents/projects/ollama-queue && source .venv/bin/activate && pytest --timeout=120 -x -q 2>&1 | tail -10
```

Expected: all tests pass.

**Step 9:** Commit:

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git commit -m "feat(ui): wire LoadMapStrip, AddRecurringJobModal, re-enable into Plan tab"
```

---

## Task 8: Final build verification and push

**Step 1:** Full test suite:

```bash
cd /home/justin/Documents/projects/ollama-queue && source .venv/bin/activate && pytest --timeout=120 -q 2>&1 | tail -15
```

Expected: all tests pass (check count vs. prior baseline of 195).

**Step 2:** Production build:

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build 2>&1 | tail -10
```

Expected: clean.

**Step 3:** Restart the service to pick up the new build:

```bash
systemctl --user restart ollama-queue.service && systemctl --user status ollama-queue.service
```

Expected: `active (running)`.

**Step 4:** Smoke test in browser — navigate to `/queue/ui/` and verify:
- Now tab: FAB `+` visible bottom-right. Click → Submit Job modal opens. Type a command, submit → "Job #N queued" toast appears.
- Now tab: If history has proxy calls, mini-stat appears below KPIs.
- Plan tab: Density strip appears above Gantt chart.
- Plan tab: `+ Add Job` button opens modal with suggested times and essential/advanced fields.
- Plan tab: Any auto-disabled job (if present) shows warning badge + Re-enable button.

**Step 5:** Create PR:

```bash
cd /home/justin/Documents/projects/ollama-queue && gh pr create --title "feat(ui): integrate 6 unexposed API endpoints into dashboard" --body "$(cat <<'EOF'
## Summary
- **Submit Job** — FAB + native dialog modal on Now tab (POST /api/queue/submit)
- **Load Map** — 48-slot density strip above Gantt on Plan tab (GET /api/schedule/load-map)
- **Add Recurring Job** — modal with essential+advanced fields + suggested times (POST /api/schedule)
- **Re-enable** — outcome_reason badge + Re-enable button for auto-disabled jobs (POST /api/schedule/jobs/{name}/enable)
- **Proxy mini-stat** — derived from history signal, no new endpoint (GET /api/generate + /api/embed signal)

## Design
Docs: `docs/plans/2026-03-04-ui-endpoint-integration-design.md`
Research principles: Sweller (≤7 fields default), Cleveland & McGill (opacity not height), Treisman (preattentive), WCAG 2.1 AA (native dialog)

## Test plan
- [ ] `pytest --timeout=120 -q` passes
- [ ] `npm run build` clean
- [ ] Now tab: FAB opens modal, submit queues job, toast appears
- [ ] Plan tab: density strip visible, Add Job modal works, suggested times appear
- [ ] Plan tab: auto-disabled jobs (outcome_reason set) show Re-enable button

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Batch Summary

| Batch | Tasks | Can parallelize? |
|---|---|---|
| 1 | Task 1 (store.js) | No — foundation for all others |
| 2 | Task 2 (API tests) | Yes, alongside Task 1 |
| 3 | Tasks 3, 4, 6 (new components) | Yes — all independent |
| 4 | Task 5 (wire Now.jsx) | After Tasks 1, 4 |
| 5 | Task 7 (wire Plan.jsx) | After Tasks 1, 3, 6 |
| 6 | Task 8 (final verify + PR) | Sequential |
