import { relativeTimeLog } from './helpers.js';

// What it shows: A reverse-chronological table of schedule mutations — rebalances,
//   job additions, toggles, and other schedule events with timestamps.
// Decision it drives: Lets the user trace what changed and when. Useful after a rebalance
//   to verify that run times shifted, or to audit who toggled a job.

export default function ScheduleHistory({ events }) {
    return (
        <section>
            <h3 style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                         fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                         textTransform: 'uppercase', letterSpacing: '0.05em',
                         margin: '0 0 0.5rem' }}>
                Schedule Change History
            </h3>
            {events.length === 0 ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-body)' }}>
                    No schedule changes yet. Changes appear here after you rebalance or add jobs.
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
                                        color: 'var(--text-secondary)', fontWeight: 600,
                                    }}>{col}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {events.slice(0, 20).map(evItem => (
                                <tr key={evItem.id}
                                    style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                    <td style={{ padding: '0.4rem 0.75rem', color: 'var(--text-tertiary)',
                                                 fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
                                        <span title={new Date(evItem.timestamp * 1000).toLocaleString()}>
                                            {relativeTimeLog(evItem.timestamp)}
                                        </span>
                                    </td>
                                    <td style={{ padding: '0.4rem 0.75rem' }}>
                                        <code class="data-mono"
                                              style={{ color: 'var(--accent)', fontSize: 'var(--type-label)' }}>
                                            {evItem.event_type}
                                        </code>
                                    </td>
                                    <td style={{ padding: '0.4rem 0.75rem', color: 'var(--text-secondary)' }}>
                                        {evItem.details || '\u2014'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    );
}
