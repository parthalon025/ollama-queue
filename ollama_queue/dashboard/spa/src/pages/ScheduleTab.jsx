import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import {
    scheduleJobs, scheduleEvents,
    fetchSchedule, toggleScheduleJob, triggerRebalance, runScheduleJobNow,
} from '../store';

// Note: local vars named 'hrs'/'mins' to avoid shadowing the injected 'h' JSX factory.
function formatCountdown(next_run) {
    const diff = next_run - Date.now() / 1000;
    if (diff < 0) return 'overdue';
    const hrs = Math.floor(diff / 3600);
    const mins = Math.floor((diff % 3600) / 60);
    const secs = Math.floor(diff % 60);
    if (hrs > 0) return `${hrs}h ${mins}m ${secs}s`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
}

function formatInterval(seconds) {
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
}

// Priority → design token colors (theme-aware)
const CATEGORY_COLORS = {
    critical:   'var(--status-error)',
    high:       'var(--status-warning)',
    normal:     'var(--accent)',
    low:        'var(--text-tertiary)',
    background: 'var(--text-tertiary)',
};

function priorityCategory(p) {
    if (p <= 2) return 'critical';
    if (p <= 4) return 'high';
    if (p <= 6) return 'normal';
    if (p <= 8) return 'low';
    return 'background';
}

function TimelineBar({ jobs, tick }) {
    // tick dependency forces re-render each second
    void tick;
    const now = Date.now() / 1000;
    const daySeconds = 86400;
    return (
        <div style={{ position: 'relative', height: 40,
                      background: 'var(--bg-inset)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius)',
                      overflow: 'hidden', margin: '1rem 0' }}>
            {jobs.map(rj => {
                const secsFromNow = rj.next_run - now;
                const pct = secsFromNow <= 0
                    ? 0
                    : Math.min(100, (secsFromNow / daySeconds) * 100);
                const color = CATEGORY_COLORS[priorityCategory(rj.priority)];
                return (
                    <div key={rj.id}
                         title={`${rj.name} — ${formatCountdown(rj.next_run)}`}
                         style={{
                             position: 'absolute', left: `${pct}%`,
                             width: 3, top: 0, bottom: 0,
                             background: color, opacity: 0.85,
                         }} />
                );
            })}
        </div>
    );
}

export default function ScheduleTab() {
    // tick increments every second to force live countdown re-renders
    const [tick, setTick] = useState(0);
    const [runningId, setRunningId] = useState(null);

    useEffect(() => {
        fetchSchedule();
        const interval = setInterval(() => setTick(t => t + 1), 1000);
        return () => clearInterval(interval);
    }, []);

    const jobs = scheduleJobs.value;
    const events = scheduleEvents.value;

    async function handleRunNow(rj) {
        setRunningId(rj.id);
        await runScheduleJobNow(rj.id);
        setRunningId(null);
    }

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                    Schedule
                </h2>
                <button class="t-btn t-btn-primary px-4 py-2 text-sm" onClick={triggerRebalance}>
                    Rebalance Now
                </button>
            </div>

            <TimelineBar jobs={jobs} tick={tick} />

            {jobs.length === 0 ? (
                <div class="t-frame" style={{ textAlign: 'center', padding: '2rem',
                                              color: 'var(--text-tertiary)' }}>
                    No recurring jobs. Add one via CLI:{' '}
                    <code class="data-mono">ollama-queue schedule add</code>
                </div>
            ) : (
                <div class="t-frame" style={{ padding: 0, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse',
                                    fontSize: 'var(--type-body)' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                         background: 'var(--bg-surface-raised)' }}>
                                {['Name', 'Tag', 'Interval', 'Priority', 'Next Run', 'Enabled', ''].map(col => (
                                    <th key={col} style={{
                                        textAlign: col === 'Name' ? 'left' : 'center',
                                        padding: '0.5rem 0.75rem',
                                        fontSize: 'var(--type-label)',
                                        color: 'var(--text-secondary)',
                                        fontWeight: 600,
                                        textTransform: 'uppercase',
                                        letterSpacing: '0.05em',
                                        fontFamily: 'var(--font-mono)',
                                        whiteSpace: 'nowrap',
                                    }}>{col}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {jobs.map(rj => {
                                const cat = priorityCategory(rj.priority);
                                const color = CATEGORY_COLORS[cat];
                                const overdue = rj.next_run < Date.now() / 1000;
                                const isRunning = runningId === rj.id;
                                // Read tick to subscribe this row to per-second updates
                                void tick;
                                return (
                                    <tr key={rj.id}
                                        style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                        <td style={{ padding: '0.5rem 0.75rem',
                                                     borderLeft: `3px solid ${color}` }}>
                                            <span style={{ color: 'var(--text-primary)',
                                                           fontFamily: 'var(--font-mono)',
                                                           fontSize: 'var(--type-body)' }}>
                                                {rj.name}
                                            </span>
                                        </td>
                                        <td style={{ textAlign: 'center',
                                                     color: 'var(--text-secondary)',
                                                     fontSize: 'var(--type-label)',
                                                     fontFamily: 'var(--font-mono)' }}>
                                            {rj.tag || '—'}
                                        </td>
                                        <td style={{ textAlign: 'center',
                                                     fontFamily: 'var(--font-mono)',
                                                     color: 'var(--text-primary)' }}>
                                            {formatInterval(rj.interval_seconds)}
                                        </td>
                                        <td style={{ textAlign: 'center' }}>
                                            <span style={{
                                                background: color,
                                                color: 'var(--accent-text)',
                                                padding: '0.1rem 0.5rem',
                                                borderRadius: 'var(--radius)',
                                                fontSize: 'var(--type-label)',
                                                fontFamily: 'var(--font-mono)',
                                                fontWeight: 600,
                                            }}>
                                                {cat} ({rj.priority})
                                            </span>
                                        </td>
                                        <td style={{ textAlign: 'center',
                                                     fontFamily: 'var(--font-mono)',
                                                     color: overdue
                                                         ? 'var(--status-error)'
                                                         : 'var(--text-primary)',
                                                     fontVariantNumeric: 'tabular-nums',
                                                     minWidth: '7rem' }}>
                                            {formatCountdown(rj.next_run)}
                                        </td>
                                        <td style={{ textAlign: 'center' }}>
                                            <input type="checkbox" checked={!!rj.enabled}
                                                   style={{ accentColor: 'var(--accent)',
                                                            width: 16, height: 16 }}
                                                   onChange={ev => toggleScheduleJob(rj.id, ev.target.checked)} />
                                        </td>
                                        <td style={{ textAlign: 'center', padding: '0.25rem 0.5rem' }}>
                                            <button
                                                class="t-btn t-btn-secondary"
                                                style={{ fontSize: 'var(--type-label)',
                                                         padding: '0.2rem 0.6rem',
                                                         opacity: isRunning ? 0.5 : 1 }}
                                                disabled={isRunning}
                                                onClick={() => handleRunNow(rj)}>
                                                {isRunning ? '…' : '▶ Run'}
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            <section>
                <h3 style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                             textTransform: 'uppercase', letterSpacing: '0.05em',
                             margin: '0 0 0.5rem' }}>
                    Rebalance Log
                </h3>
                {events.length === 0 ? (
                    <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-body)' }}>
                        No events yet.
                    </p>
                ) : (
                    <div class="t-frame" style={{ padding: 0, overflow: 'hidden' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse',
                                        fontSize: 'var(--type-label)' }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                             background: 'var(--bg-surface-raised)' }}>
                                    {['Time', 'Event', 'Details'].map(col => (
                                        <th key={col} style={{
                                            textAlign: 'left', padding: '0.4rem 0.75rem',
                                            color: 'var(--text-secondary)',
                                            fontWeight: 600,
                                        }}>{col}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {events.slice(0, 20).map(ev => (
                                    <tr key={ev.id}
                                        style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                        <td style={{ padding: '0.4rem 0.75rem',
                                                     color: 'var(--text-tertiary)',
                                                     fontFamily: 'var(--font-mono)',
                                                     whiteSpace: 'nowrap' }}>
                                            {new Date(ev.timestamp * 1000).toLocaleTimeString()}
                                        </td>
                                        <td style={{ padding: '0.4rem 0.75rem' }}>
                                            <code class="data-mono"
                                                  style={{ color: 'var(--accent)',
                                                           fontSize: 'var(--type-label)' }}>
                                                {ev.event_type}
                                            </code>
                                        </td>
                                        <td style={{ padding: '0.4rem 0.75rem',
                                                     color: 'var(--text-secondary)' }}>
                                            {ev.details || '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </section>
        </div>
    );
}
