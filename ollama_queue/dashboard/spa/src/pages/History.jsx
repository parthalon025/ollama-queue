import { h } from 'preact';
import {
    dlqEntries, dlqCount, durationData, heatmapData, history,
    fetchDLQ, API,
} from '../store';
import { useEffect } from 'preact/hooks';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import ActivityHeatmap from '../components/ActivityHeatmap.jsx';
import HistoryList from '../components/HistoryList.jsx';
import TimeChart from '../components/TimeChart.jsx';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

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

    useEffect(() => { fetchDLQ(); }, []);

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

            {/* DLQ section — only shown when entries exist */}
            {dlqCnt > 0 && (
                <div class="t-frame" data-label={`Jobs That Couldn't Complete (${dlqCnt})`}>
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
                    {dlq.map(entry => (
                        <DLQRow key={entry.id} entry={entry} onAction={handleDLQAction} />
                    ))}
                </div>
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

// ── DLQ sub-component ──────────────────────────────────────────────────────

// What it shows: A single DLQ entry — job source/id, failure reason, retry count — with
//   per-row Retry and Dismiss controls and inline feedback for each action.
// Decision it drives: User can retry a specific failed job (requeues it) or permanently
//   dismiss it (removes from DLQ), with immediate visual confirmation of the outcome.
function DLQRow({ entry, onAction }) {
    const [retryFb, retryAct] = useActionFeedback();
    const [dismissFb, dismissAct] = useActionFeedback();

    return (
        <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            padding: '0.4rem 0',
            borderBottom: '1px solid var(--border-subtle)',
            gap: '0.5rem',
            flexWrap: 'wrap',
        }}>
            <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0;">
                <span style={{
                    fontSize: 'var(--type-body)',
                    color: 'var(--text-primary)',
                    fontFamily: 'var(--font-mono)',
                }}>
                    {entry.source || '—'} #{entry.job_id}
                </span>
                <span style={{
                    fontSize: 'var(--type-label)',
                    color: 'var(--text-tertiary)',
                }}>
                    {entry.failure_reason || 'unknown reason'}
                    {entry.retry_count > 0 && ` · ${entry.retry_count} retries`}
                </span>
            </div>
            <div class="flex gap-2" style="flex-shrink: 0; align-items: flex-start;">
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
                        onClick={() => dismissAct(
                            'Dismissing…',
                            () => onAction('dismiss', entry.id),
                            'Deleted from failed queue',
                        )}
                    >
                        {dismissFb.phase === 'loading' ? 'Dismissing…' : 'Delete'}
                    </button>
                    {dismissFb.msg && (
                        <div class={`action-fb action-fb--${dismissFb.phase}`}>{dismissFb.msg}</div>
                    )}
                </div>
            </div>
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
