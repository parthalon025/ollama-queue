import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import {
    scheduleJobs, scheduleEvents,
    fetchSchedule, toggleScheduleJob, triggerRebalance, runScheduleJobNow,
    updateScheduleJob,
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
    if (!seconds) return '—';
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
}

// Parse shorthand like "4h", "30m", "1d", "7d", "90s", or plain seconds
function parseInterval(str) {
    const trimmed = str.trim().toLowerCase();
    const match = trimmed.match(/^(\d+(?:\.\d+)?)\s*(d|h|m|s)?$/);
    if (!match) return null;
    const val = parseFloat(match[1]);
    if (val <= 0 || !isFinite(val)) return null;
    const unit = match[2] || 's';
    const multipliers = { d: 86400, h: 3600, m: 60, s: 1 };
    return Math.round(val * multipliers[unit]);
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
                         title={`${rj.pinned ? '★ PINNED: ' : ''}${rj.name} — ${formatCountdown(rj.next_run)}`}
                         style={{
                             position: 'absolute', left: `${pct}%`,
                             width: rj.pinned ? 5 : 3,
                             top: rj.pinned ? 0 : 4,
                             bottom: rj.pinned ? 0 : 4,
                             background: color,
                             opacity: rj.pinned ? 1.0 : 0.75,
                             borderRadius: rj.pinned ? 0 : 2,
                         }} />
                );
            })}
        </div>
    );
}

export default function ScheduleTab() {
    // tick increments every second to force live countdown re-renders
    const [tick, setTick] = useState(0);
    const [runningIds, setRunningIds] = useState(new Set());
    const [runError, setRunError] = useState(null);
    const [editingInterval, setEditingInterval] = useState(null);   // { id, value }
    const [editingPriority, setEditingPriority] = useState(null);   // { id, value }

    useEffect(() => {
        fetchSchedule();
        const interval = setInterval(() => setTick(t => t + 1), 1000);
        return () => clearInterval(interval);
    }, []);

    const jobs = scheduleJobs.value;
    const events = scheduleEvents.value;

    async function handleIntervalSave(rjId) {
        if (!editingInterval || editingInterval.id !== rjId) return;
        const seconds = parseInterval(editingInterval.value);
        if (!seconds) {
            setEditingInterval(null);
            return;
        }
        try {
            await updateScheduleJob(rjId, { interval_seconds: seconds });
        } catch (e) {
            console.error('Interval update failed:', e);
            setRunError('Failed to update interval');
        }
        setEditingInterval(null);
    }

    async function handlePrioritySave(rjId) {
        if (!editingPriority || editingPriority.id !== rjId) return;
        const val = parseInt(editingPriority.value, 10);
        if (isNaN(val) || val < 1 || val > 10) { setEditingPriority(null); return; }
        try {
            await updateScheduleJob(rjId, { priority: val });
        } catch (e) {
            console.error('Priority update failed:', e);
            setRunError('Failed to update priority');
        }
        setEditingPriority(null);
    }

    async function handlePinToggle(rj) {
        try {
            await updateScheduleJob(rj.id, { pinned: !rj.pinned });
        } catch (e) {
            console.error('Pin toggle failed:', e);
            setRunError(`Failed to toggle pin for "${rj.name}"`);
        }
    }

    async function handleRunNow(rj) {
        setRunningIds(prev => new Set([...prev, rj.id]));
        setRunError(null);
        try {
            await runScheduleJobNow(rj.id);
        } catch (e) {
            console.error('Run Now failed:', e);
            setRunError(`Failed to run "${rj.name}"`);
        } finally {
            setRunningIds(prev => {
                const next = new Set(prev);
                next.delete(rj.id);
                return next;
            });
        }
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

            {runError && (
                <div style={{ padding: '0.5rem 0.75rem', background: 'var(--status-error)',
                              color: 'var(--accent-text)', borderRadius: 'var(--radius)',
                              fontSize: 'var(--type-body)', fontFamily: 'var(--font-mono)' }}>
                    {runError}
                </div>
            )}

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
                                {['Name', 'Tag', 'Schedule', 'Priority', 'Next Run', '★', 'Enabled', ''].map(col => (
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
                                const isRunning = runningIds.has(rj.id);
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
                                            {editingInterval && editingInterval.id === rj.id ? (
                                                <input
                                                    type="text"
                                                    value={editingInterval.value}
                                                    onInput={ev => setEditingInterval({ id: rj.id, value: ev.target.value })}
                                                    onBlur={() => handleIntervalSave(rj.id)}
                                                    onKeyDown={ev => {
                                                        if (ev.key === 'Enter') handleIntervalSave(rj.id);
                                                        if (ev.key === 'Escape') setEditingInterval(null);
                                                    }}
                                                    ref={el => el && el.focus()}
                                                    style={{
                                                        width: '4.5rem', textAlign: 'center',
                                                        fontFamily: 'var(--font-mono)',
                                                        fontSize: 'var(--type-body)',
                                                        background: 'var(--bg-inset)',
                                                        color: 'var(--text-primary)',
                                                        border: '1px solid var(--accent)',
                                                        borderRadius: 'var(--radius)',
                                                        padding: '0.1rem 0.3rem',
                                                        outline: 'none',
                                                    }}
                                                />
                                            ) : rj.cron_expression ? (
                                                <span style={{ color: 'var(--text-secondary)',
                                                               fontSize: 'var(--type-label)' }}>
                                                    {rj.cron_expression}
                                                </span>
                                            ) : (
                                                <span
                                                    style={{ cursor: 'pointer', borderBottom: '1px dashed var(--text-tertiary)' }}
                                                    title="Click to edit interval (e.g. 4h, 30m, 7d)"
                                                    onClick={() => setEditingInterval({ id: rj.id, value: formatInterval(rj.interval_seconds) })}>
                                                    {formatInterval(rj.interval_seconds)}
                                                </span>
                                            )}
                                        </td>
                                        <td style={{ textAlign: 'center' }}>
                                            {editingPriority && editingPriority.id === rj.id ? (
                                                <input
                                                    type="number" min="1" max="10"
                                                    value={editingPriority.value}
                                                    onInput={ev => setEditingPriority({ id: rj.id, value: ev.target.value })}
                                                    onBlur={() => handlePrioritySave(rj.id)}
                                                    onKeyDown={ev => {
                                                        if (ev.key === 'Enter') handlePrioritySave(rj.id);
                                                        if (ev.key === 'Escape') setEditingPriority(null);
                                                    }}
                                                    ref={el => el && el.focus()}
                                                    style={{
                                                        width: '3rem', textAlign: 'center',
                                                        fontFamily: 'var(--font-mono)',
                                                        background: 'var(--bg-inset)',
                                                        color: 'var(--text-primary)',
                                                        border: '1px solid var(--accent)',
                                                        borderRadius: 'var(--radius)',
                                                        padding: '0.1rem 0.2rem',
                                                    }}
                                                />
                                            ) : (
                                                <span
                                                    style={{ background: color,
                                                             color: 'var(--accent-text)',
                                                             padding: '0.1rem 0.5rem',
                                                             borderRadius: 'var(--radius)',
                                                             fontSize: 'var(--type-label)',
                                                             fontFamily: 'var(--font-mono)',
                                                             fontWeight: 600, cursor: 'pointer',
                                                             borderBottom: '1px dashed var(--accent-text)' }}
                                                    title="Click to edit priority (1=highest, 10=lowest)"
                                                    onClick={() => setEditingPriority({ id: rj.id, value: String(rj.priority) })}>
                                                    {cat} ({rj.priority})
                                                </span>
                                            )}
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
                                            <button
                                                title={rj.pinned ? 'Pinned — click to unpin' : 'Click to pin this time slot'}
                                                onClick={() => handlePinToggle(rj)}
                                                style={{
                                                    background: 'none', border: 'none',
                                                    cursor: 'pointer', fontSize: '1.1rem',
                                                    color: rj.pinned ? 'var(--status-warning)' : 'var(--text-tertiary)',
                                                    opacity: rj.pinned ? 1 : 0.4,
                                                }}>
                                                ★
                                            </button>
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
