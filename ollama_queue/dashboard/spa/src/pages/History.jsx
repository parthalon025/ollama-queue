import { ShFrozen } from 'superhot-ui/preact';
import { shatterElement } from 'superhot-ui';
import {
    dlqEntries, dlqCount, durationData, heatmapData, history,
    fetchDLQ, rescheduleDLQEntry, API,
    highlightJobId, dlqSchedulePreview,
} from '../stores';
import { currentTab } from '../stores/health.js';
import { useEffect, useRef, useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import ActivityHeatmap from '../components/ActivityHeatmap.jsx';
import HistoryList from '../components/HistoryList.jsx';
import TimeChart from '../components/TimeChart.jsx';
import PageBanner from '../components/PageBanner.jsx';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

// What it shows: Eval run events (started/completed) fetched from the eval runs API.
// Decision it drives: Would annotate the activity heatmap with eval milestones if ActivityHeatmap
//   accepted an events prop. Fetched here for future use; ActivityHeatmap currently only accepts
//   data (gpu_minutes heatmap) so events are not passed through.
const evalEvents = signal([]);

// Freshness thresholds for DLQ entries (in seconds):
//   cooling = 1h (entry has been sitting a while), frozen = 6h (neglected),
//   stale = 24h (long-ignored failure). DLQ entries that age toward stale
//   are the highest-priority unaddressed failures.
const DLQ_FRESHNESS = { cooling: 3600, frozen: 21600, stale: 86400 };

// What it shows: The past — completed/failed job list, Dead Letter Queue (jobs that exhausted
//   all retries and need manual intervention), GPU activity heatmap (7d × 24h), and duration
//   trend sparkline.
// Decision it drives: Are there failed jobs that need retrying or dismissing? Is there a
//   time-of-day pattern to failures (visible in the heatmap)? Are jobs getting slower over
//   time (visible in the duration chart)?
export default function History() {
    const dlq = dlqEntries.value;
    const dlqCnt = dlqCount.value;
    const durations = durationData.value;
    const heatmap = heatmapData.value;
    const hist = history.value;

    // Hooks must be called before any conditional early returns
    const [retryAllFb, retryAllAct] = useActionFeedback();
    const [clearFb, clearAct] = useActionFeedback();
    const dlqListRef = useRef(null);

    useEffect(() => { fetchDLQ(); }, []);

    // Fetch eval run events for heatmap annotation (future use — ActivityHeatmap does not yet
    // accept an events prop, so these are prepared but not passed through).
    useEffect(() => {
        fetch('/api/eval/runs?limit=50')
            .then(r => r.ok ? r.json() : { items: [] })
            .then(data => {
                const runs = Array.isArray(data) ? data : (data.items || []);
                evalEvents.value = runs.flatMap(run => {
                    const events = [];
                    if (run.started_at) events.push({ type: 'eval_started', timestamp: run.started_at, label: 'Eval started' });
                    if (run.completed_at && run.status === 'complete') events.push({ type: 'eval_completed', timestamp: run.completed_at, label: `Eval complete (F1 ${run.winner_f1?.toFixed(2) || '?'})` });
                    return events;
                });
            })
            .catch(() => {});
    }, []);

    async function handleRetryAll() {
        if (!window.confirm(`Re-queue all ${dlq.length} failed jobs so they try again?`)) return;
        await retryAllAct(
            'Retrying all…',
            async () => {
                const res = await fetch(`${API}/dlq/retry-all`, { method: 'POST' });
                if (!res.ok) throw new Error(`Retry all failed: ${res.status}`);
                const data = await res.json();
                await fetchDLQ();
                return data;
            },
            data => `${data.retried ?? data.count ?? 'All'} jobs re-queued`,
        );
    }

    async function handleClearDLQ() {
        if (!window.confirm('Permanently delete all failed jobs? This cannot be undone.')) return;
        await clearAct(
            'Clearing DLQ…',
            async () => {
                // Stagger-shatter all visible DLQ row elements before clearing
                if (dlqListRef.current) {
                    const rows = Array.from(dlqListRef.current.children);
                    rows.forEach((row, i) => {
                        setTimeout(() => shatterElement(row), i * 80);
                    });
                }
                // Wait for animations to be visible before making the API call
                await new Promise(resolve => setTimeout(resolve, 300));
                const res = await fetch(`${API}/dlq`, { method: 'DELETE' });
                if (!res.ok) throw new Error(`Clear failed: ${res.status}`);
                await fetchDLQ();
            },
            'All failed jobs deleted',
        );
    }

    async function handleDLQAction(action, id) {
        if (action === 'retry') {
            const res = await fetch(`${API}/dlq/${id}/retry`, { method: 'POST' });
            if (!res.ok) throw new Error(`Retry failed: ${res.status}`);
            await fetchDLQ();
        } else if (action === 'dismiss') {
            const res = await fetch(`${API}/dlq/${id}/dismiss`, { method: 'POST' });
            if (!res.ok) throw new Error(`Dismiss failed: ${res.status}`);
            await fetchDLQ();
        }
    }

    return (
        <div class="flex flex-col gap-6 animate-page-enter">
            <PageBanner title="History" subtitle="completed and failed jobs" />

            {/* DLQ section — only shown when entries exist */}
            {dlqCnt > 0 && (
                <div class="t-frame" data-label={`Jobs That Couldn't Complete (${dlqCnt})`} data-chroma="maelle">
                    <div style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: '0.75rem',
                        flexWrap: 'wrap',
                        gap: '0.5rem',
                    }}>
                        <span style={{
                            color: 'var(--status-error)',
                            fontSize: 'var(--type-label)',
                            fontFamily: 'var(--font-mono)',
                        }}>
                            {dlqCnt} {dlqCnt === 1 ? 'job' : 'jobs'} failed all retry attempts and {dlqCnt === 1 ? 'needs' : 'need'} your attention
                            <span style="display: block; font-size: var(--type-micro); color: var(--text-tertiary); font-family: var(--font-mono); margin-top: 1px;">dead-letter queue</span>
                        </span>
                        <div class="flex gap-2" style="align-items: flex-start;">
                            <div>
                                <button
                                    class="t-btn t-btn-secondary"
                                    style="font-size: var(--type-label); padding: 3px 10px;"
                                    onClick={handleRetryAll}
                                    disabled={retryAllFb.phase === 'loading'}
                                >
                                    {retryAllFb.phase === 'loading' ? 'Retrying all…' : 'Re-queue all failed jobs'}
                                </button>
                                {retryAllFb.msg && (
                                    <div class={`action-fb action-fb--${retryAllFb.phase}`}>{retryAllFb.msg}</div>
                                )}
                            </div>
                            <div>
                                <button
                                    class="t-btn t-btn-secondary"
                                    style="font-size: var(--type-label); padding: 3px 10px;"
                                    onClick={handleClearDLQ}
                                    disabled={clearFb.phase === 'loading'}
                                >
                                    {clearFb.phase === 'loading' ? 'Clearing…' : 'Delete all'}
                                </button>
                                {clearFb.msg && (
                                    <div class={`action-fb action-fb--${clearFb.phase}`}>{clearFb.msg}</div>
                                )}
                            </div>
                        </div>
                    </div>
                    <div ref={dlqListRef}>
                        {dlq.map(entry => (
                            <ShFrozen key={entry.id} timestamp={entry.moved_at * 1000} thresholds={DLQ_FRESHNESS}>
                                <DLQRow entry={entry} onAction={handleDLQAction} />
                            </ShFrozen>
                        ))}
                    </div>
                </div>
            )}

            {/* C22: DLQ Schedule Preview — expandable failure classification + retry slots */}
            {dlqSchedulePreview.value?.entries?.length > 0 && (
                <DLQSchedulePreviewPanel preview={dlqSchedulePreview.value} />
            )}

            {/* Duration trends + Activity heatmap — side by side on desktop */}
            <div class="history-top-grid">
                <div class="t-frame" data-label="How Long Jobs Take Over Time">
                    {durations && durations.length > 0 ? (
                        buildDurationBySources(durations).map(({ source, data }) => (
                            <div key={source} style="margin-bottom: 0.75rem;">
                                <div style={{
                                    fontSize: 'var(--type-micro)',
                                    color: 'var(--text-tertiary)',
                                    fontFamily: 'var(--font-mono)',
                                    marginBottom: '2px',
                                }}>
                                    {source}
                                </div>
                                <TimeChart
                                    data={data}
                                    series={[{ label: source, color: 'var(--accent)', width: 1.5 }]}
                                    height={60}
                                />
                            </div>
                        ))
                    ) : (
                        <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
                            No timing data yet — run some jobs first
                        </p>
                    )}
                </div>

                {/* ActivityHeatmap renders its own t-frame wrapper internally */}
                <ActivityHeatmap data={heatmap} />
            </div>

            {/* HistoryList renders its own t-frame wrapper internally */}
            <HistoryList jobs={hist} />
        </div>
    );
}

// ── C22: DLQ Schedule Preview panel ───────────────────────────────────────

// What it shows: Expandable panel with failure classification + predicted retry slots for
//   unscheduled DLQ entries. Helps the user understand what will happen to stuck jobs.
// Decision it drives: "Does the system have a plan for these failures, or do I need to act?"
function DLQSchedulePreviewPanel({ preview }) {
    const [open, setOpen] = useState(false);
    if (!preview?.entries?.length) return null;
    return (
        <div class="t-frame" style={{ borderLeft: '3px solid var(--status-warning)' }}>
            <button
                onClick={() => setOpen(o => !o)}
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--status-warning)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 'var(--type-label)',
                    padding: 0,
                    width: '100%',
                    textAlign: 'left',
                }}
            >
                <span>{open ? '▼' : '▶'}</span>
                <span>DLQ Retry Schedule Preview — {preview.count} {preview.count === 1 ? 'entry' : 'entries'} pending</span>
            </button>
            {open && (
                <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                    {preview.entries.map((slot, i) => (
                        <div key={i} class="data-mono" style={{ display: 'flex', gap: '0.75rem', fontSize: 'var(--type-label)', padding: '0.375rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                            <span style={{ color: 'var(--text-tertiary)', minWidth: '3rem' }}>#{slot.id ?? i + 1}</span>
                            <span style={{ flex: 1, color: 'var(--text-secondary)' }}>{slot.failure_type ?? 'unknown'}</span>
                            <span style={{ color: 'var(--text-tertiary)' }}>{slot.predicted_slot ? new Date(slot.predicted_slot * 1000).toLocaleString() : 'unscheduled'}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ── DLQ sub-component ──────────────────────────────────────────────────────

// What it shows: A single DLQ entry — job source/id, failure reason, retry count,
//   auto-reschedule status (awaiting/scheduled/rescheduled/chronic), and decision reasoning.
// Decision it drives: User can retry, dismiss, or manually reschedule a failed job.
//   The reschedule status tells the user whether the system has already handled it.
//   Expanding the reasoning panel shows WHY the scheduler made its decision.

function dlqRescheduleStatus(entry) {
    const count = entry.auto_reschedule_count || 0;
    if (count >= 5) return { label: 'Chronic', cls: 'dlq-status--chronic' };
    if (entry.rescheduled_job_id) return { label: 'Rescheduled', cls: 'dlq-status--rescheduled' };
    if (entry.rescheduled_for) {
        const when = new Date(entry.rescheduled_for * 1000).toLocaleTimeString();
        return { label: `Scheduled ${when}`, cls: 'dlq-status--scheduled' };
    }
    return { label: 'Awaiting', cls: 'dlq-status--awaiting' };
}

function DLQRow({ entry, onAction }) {
    // All hooks before any conditional returns (Rules of Hooks)
    const [retryFb, retryAct] = useActionFeedback();
    const [dismissFb, dismissAct] = useActionFeedback();
    const [rescheduleFb, rescheduleAct] = useActionFeedback();
    const [expanded, setExpanded] = useState(false);
    const rowRef = useRef(null);

    // ThreatPulse: DLQ entries are always critical — fire on mount so the user
    // can't miss that these jobs need attention.
    useEffect(() => {
        if (rowRef.current) rowRef.current.setAttribute('data-sh-effect', 'threat-pulse');
    }, []);

    const reschedule = dlqRescheduleStatus(entry);
    const hasReasoning = !!entry.reschedule_reasoning;
    const alreadyRescheduled = !!entry.rescheduled_job_id;

    return (
        <div ref={rowRef} style={{
            padding: '0.4rem 0',
            borderBottom: '1px solid var(--border-subtle)',
        }}>
            <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
                gap: '0.5rem',
                flexWrap: 'wrap',
            }}>
                <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1;">
                    <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                        <span style={{
                            fontSize: 'var(--type-body)',
                            color: 'var(--text-primary)',
                            fontFamily: 'var(--font-mono)',
                        }}>
                            {entry.source || '—'} #{entry.job_id}
                        </span>
                        <span class={`dlq-status ${reschedule.cls}`}>
                            {reschedule.label}
                        </span>
                    </div>
                    <span style={{
                        fontSize: 'var(--type-label)',
                        color: 'var(--text-tertiary)',
                    }}>
                        {entry.failure_reason || 'unknown reason'}
                        {entry.retry_count > 0 && ` · ${entry.retry_count} retries`}
                        {entry.auto_reschedule_count > 0 && ` · ${entry.auto_reschedule_count} reschedule${entry.auto_reschedule_count !== 1 ? 's' : ''}`}
                    </span>
                </div>
                <div class="flex gap-2" style="flex-shrink: 0; align-items: flex-start;">
                    {!alreadyRescheduled && (
                        <div>
                            <button
                                class="t-btn t-btn-secondary"
                                style="font-size: var(--type-label); padding: 2px 8px;"
                                disabled={rescheduleFb.phase === 'loading'}
                                onClick={() => rescheduleAct(
                                    'Scheduling…',
                                    () => rescheduleDLQEntry(entry.id),
                                    `DLQ #${entry.id} rescheduled`,
                                )}
                            >
                                {rescheduleFb.phase === 'loading' ? 'Scheduling…' : 'Reschedule'}
                            </button>
                            {rescheduleFb.msg && (
                                <div class={`action-fb action-fb--${rescheduleFb.phase}`}>{rescheduleFb.msg}</div>
                            )}
                        </div>
                    )}
                    <div>
                        <button
                            class="t-btn t-btn-secondary"
                            style="font-size: var(--type-label); padding: 2px 8px;"
                            disabled={retryFb.phase === 'loading'}
                            onClick={() => retryAct(
                                'Retrying…',
                                () => onAction('retry', entry.id),
                                'Job re-queued for retry',
                            )}
                        >
                            {retryFb.phase === 'loading' ? 'Retrying…' : 'Re-queue'}
                        </button>
                        {retryFb.msg && (
                            <div class={`action-fb action-fb--${retryFb.phase}`}>{retryFb.msg}</div>
                        )}
                    </div>
                    <div>
                        <button
                            class="t-btn t-btn-secondary"
                            style="font-size: var(--type-label); padding: 2px 8px;"
                            disabled={dismissFb.phase === 'loading'}
                            onClick={() => {
                                if (rowRef.current) shatterElement(rowRef.current);
                                dismissAct(
                                    'Dismissing…',
                                    () => onAction('dismiss', entry.id),
                                    `DLQ #${entry.id} dismissed`,
                                );
                            }}
                        >
                            {dismissFb.phase === 'loading' ? 'Dismissing…' : 'Delete'}
                        </button>
                        {dismissFb.msg && (
                            <div class={`action-fb action-fb--${dismissFb.phase}`}>{dismissFb.msg}</div>
                        )}
                    </div>
                    {hasReasoning && (
                        <button
                            class="t-btn t-btn-secondary"
                            style="font-size: var(--type-label); padding: 2px 8px;"
                            onClick={() => setExpanded(prev => !prev)}
                        >
                            {expanded ? 'Hide' : 'Why?'}
                        </button>
                    )}
                    {entry.job_id && (
                        <button
                            class="dlq-view-context"
                            onClick={() => { highlightJobId.value = entry.job_id; currentTab.value = 'now'; }}
                        >
                            → View context
                        </button>
                    )}
                </div>
            </div>
            {expanded && hasReasoning && (
                <div style={{
                    marginTop: '0.4rem',
                    padding: '0.4rem 0.6rem',
                    background: 'var(--bg-surface)',
                    borderRadius: '4px',
                    fontSize: 'var(--type-label)',
                    color: 'var(--text-secondary)',
                    fontFamily: 'var(--font-mono)',
                    whiteSpace: 'pre-wrap',
                }}>
                    {entry.reschedule_reasoning}
                </div>
            )}
        </div>
    );
}

// ── Data helper ────────────────────────────────────────────────────────────

function buildDurationBySources(rows) {
    const bySource = {};
    for (const r of rows) {
        const s = r.source || 'unknown';
        if (!bySource[s]) bySource[s] = [];
        bySource[s].push(r);
    }
    return Object.entries(bySource).map(([source, items]) => {
        const sorted = [...items].sort((a, b) => a.recorded_at - b.recorded_at);
        return {
            source,
            data: [sorted.map(r => r.recorded_at), sorted.map(r => r.duration)],
        };
    });
}
