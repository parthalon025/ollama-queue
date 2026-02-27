import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import {
    scheduleJobs, scheduleEvents,
    fetchSchedule, toggleScheduleJob, triggerRebalance,
} from '../store';

// Note: local vars named 'hrs'/'mins' to avoid shadowing the injected 'h' JSX factory.
function formatCountdown(next_run) {
    const diff = next_run - Date.now() / 1000;
    if (diff < 0) return 'overdue';
    const hrs = Math.floor(diff / 3600);
    const mins = Math.floor((diff % 3600) / 60);
    return hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
}

function formatInterval(seconds) {
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
}

const CATEGORY_COLORS = {
    critical: '#ef4444', high: '#f97316',
    normal: '#3b82f6', low: '#6b7280', background: '#374151',
};

function priorityCategory(p) {
    if (p <= 2) return 'critical';
    if (p <= 4) return 'high';
    if (p <= 6) return 'normal';
    if (p <= 8) return 'low';
    return 'background';
}

function TimelineBar({ jobs }) {
    const now = Date.now() / 1000;
    const daySeconds = 86400;
    return (
        <div style={{ position: 'relative', height: 48, background: '#1e293b',
                      borderRadius: 4, overflow: 'hidden', margin: '1rem 0' }}>
            {jobs.map(rj => {
                const pct = Math.min(100, Math.max(0, ((rj.next_run - now) % daySeconds) / daySeconds * 100));
                const color = CATEGORY_COLORS[priorityCategory(rj.priority)];
                return (
                    <div key={rj.id}
                         title={`${rj.name} — ${formatCountdown(rj.next_run)}`}
                         style={{
                             position: 'absolute', left: `${pct}%`,
                             width: 3, top: 0, bottom: 0, background: color, opacity: 0.8,
                         }} />
                );
            })}
        </div>
    );
}

export default function ScheduleTab() {
    useEffect(() => { fetchSchedule(); }, []);
    const jobs = scheduleJobs.value;
    const events = scheduleEvents.value;

    return (
        <div style={{ padding: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ margin: 0 }}>Schedule</h2>
                <button onClick={triggerRebalance}
                        style={{ padding: '0.4rem 1rem', background: '#3b82f6',
                                 color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                    Rebalance Now
                </button>
            </div>

            <TimelineBar jobs={jobs} />

            {jobs.length === 0 ? (
                <p style={{ color: '#64748b', textAlign: 'center', padding: '2rem' }}>
                    No recurring jobs. Add one via CLI: <code>ollama-queue schedule add</code>
                </p>
            ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid #334155' }}>
                            <th style={{ textAlign: 'left', padding: '0.4rem' }}>Name</th>
                            <th style={{ textAlign: 'center' }}>Tag</th>
                            <th style={{ textAlign: 'center' }}>Interval</th>
                            <th style={{ textAlign: 'center' }}>Priority</th>
                            <th style={{ textAlign: 'center' }}>Next Run</th>
                            <th style={{ textAlign: 'center' }}>Enabled</th>
                        </tr>
                    </thead>
                    <tbody>
                        {jobs.map(rj => {
                            const cat = priorityCategory(rj.priority);
                            const color = CATEGORY_COLORS[cat];
                            return (
                                <tr key={rj.id} style={{ borderBottom: '1px solid #1e293b' }}>
                                    <td style={{ padding: '0.5rem', borderLeft: `3px solid ${color}` }}>
                                        {rj.name}
                                    </td>
                                    <td style={{ textAlign: 'center', color: '#94a3b8' }}>
                                        {rj.tag || '—'}
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        {formatInterval(rj.interval_seconds)}
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        <span style={{ background: color, color: '#fff',
                                                       padding: '0.1rem 0.5rem',
                                                       borderRadius: 4, fontSize: 12 }}>
                                            {cat} ({rj.priority})
                                        </span>
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        {formatCountdown(rj.next_run)}
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        <input type="checkbox" checked={!!rj.enabled}
                                               onChange={ev => toggleScheduleJob(rj.id, ev.target.checked)} />
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            )}

            <h3 style={{ marginTop: '2rem' }}>Rebalance Log</h3>
            {events.length === 0 ? (
                <p style={{ color: '#64748b', fontSize: 13 }}>No events yet.</p>
            ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid #334155' }}>
                            <th style={{ textAlign: 'left', padding: '0.3rem' }}>Time</th>
                            <th style={{ textAlign: 'left' }}>Event</th>
                            <th style={{ textAlign: 'left' }}>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        {events.slice(0, 20).map(ev => (
                            <tr key={ev.id} style={{ borderBottom: '1px solid #1e293b', fontSize: 12 }}>
                                <td style={{ padding: '0.3rem', color: '#94a3b8' }}>
                                    {new Date(ev.timestamp * 1000).toLocaleTimeString()}
                                </td>
                                <td><code>{ev.event_type}</code></td>
                                <td style={{ color: '#94a3b8' }}>{ev.details || '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
        </div>
    );
}
