import { h } from 'preact';
import {
    dlqEntries, dlqCount, durationData, heatmapData, history,
    retryDLQEntry, retryAllDLQ, dismissDLQEntry, clearDLQ, fetchDLQ,
} from '../store';
import { useEffect, useState } from 'preact/hooks';
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
    const [retryingAll, setRetryingAll] = useState(false);

    useEffect(() => { fetchDLQ(); }, []);

    async function handleRetryAll() {
        if (!window.confirm(`Retry all ${dlq.length} failed jobs?`)) return;
        setRetryingAll(true);
        try { await retryAllDLQ(); }
        finally { setRetryingAll(false); }
    }

    async function handleClearDLQ() {
        if (!window.confirm('Clear all DLQ entries? This cannot be undone.')) return;
        await clearDLQ();
    }

    return (
        <div class="flex flex-col gap-6 animate-page-enter">

            {/* DLQ section — only shown when entries exist */}
            {dlqCnt > 0 && (
                <div class="t-frame" data-label={`Failed Jobs (${dlqCnt})`}>
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
                            {dlqCnt} {dlqCnt === 1 ? 'entry' : 'entries'} in dead-letter queue
                        </span>
                        <div class="flex gap-2">
                            <button
                                class="t-btn t-btn-secondary"
                                style="font-size: var(--type-label); padding: 3px 10px;"
                                onClick={handleRetryAll}
                                disabled={retryingAll}
                            >
                                {retryingAll ? 'Retrying...' : 'Retry all'}
                            </button>
                            <button
                                class="t-btn t-btn-secondary"
                                style="font-size: var(--type-label); padding: 3px 10px;"
                                onClick={handleClearDLQ}
                            >
                                Clear
                            </button>
                        </div>
                    </div>
                    {dlq.map(entry => (
                        <div key={entry.id} style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
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
                            <div class="flex gap-2" style="flex-shrink: 0;">
                                <button
                                    class="t-btn t-btn-secondary"
                                    style="font-size: var(--type-label); padding: 2px 8px;"
                                    onClick={() => retryDLQEntry(entry.id)}
                                >
                                    Retry
                                </button>
                                <button
                                    class="t-btn t-btn-secondary"
                                    style="font-size: var(--type-label); padding: 2px 8px;"
                                    onClick={() => dismissDLQEntry(entry.id)}
                                >
                                    Dismiss
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Duration trends + Activity heatmap — side by side on desktop */}
            <div class="history-top-grid">
                <div class="t-frame" data-label="Duration Trends">
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
                            No data yet
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
