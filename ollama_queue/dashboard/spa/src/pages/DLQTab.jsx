import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import {
    dlqEntries, fetchDLQ,
    retryDLQEntry, retryAllDLQ, dismissDLQEntry, clearDLQ,
} from '../store';

export default function DLQTab() {
    const [expanded, setExpanded] = useState(null);
    useEffect(() => { fetchDLQ(); }, []);
    const entries = dlqEntries.value;

    if (entries.length === 0) {
        return (
            <div style={{ padding: '3rem', textAlign: 'center', color: '#64748b' }}>
                <div style={{ fontSize: 48 }}>✓</div>
                <div style={{ marginTop: '0.5rem' }}>No failed jobs</div>
            </div>
        );
    }

    return (
        <div style={{ padding: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ margin: 0 }}>
                    Dead Letter Queue{' '}
                    <span style={{ fontSize: 14, color: '#ef4444' }}>({entries.length})</span>
                </h2>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button onClick={retryAllDLQ}
                            style={{ padding: '0.4rem 1rem', background: '#3b82f6',
                                     color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                        Retry All
                    </button>
                    <button onClick={clearDLQ}
                            style={{ padding: '0.4rem 1rem', background: '#374151',
                                     color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                        Clear Resolved
                    </button>
                </div>
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: '1rem' }}>
                <thead>
                    <tr style={{ borderBottom: '1px solid #334155' }}>
                        <th style={{ textAlign: 'left', padding: '0.4rem' }}>Command</th>
                        <th style={{ textAlign: 'center' }}>Source</th>
                        <th style={{ textAlign: 'center' }}>Tag</th>
                        <th style={{ textAlign: 'center' }}>Failure</th>
                        <th style={{ textAlign: 'center' }}>Retries</th>
                        <th style={{ textAlign: 'center' }}>Moved</th>
                        <th style={{ textAlign: 'center' }}>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {entries.map(entry => (
                        <DLQRow key={entry.id}
                                entry={entry}
                                isExpanded={expanded === entry.id}
                                onToggle={() => setExpanded(expanded === entry.id ? null : entry.id)} />
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function DLQRow({ entry, isExpanded, onToggle }) {
    // Using separate components avoids Fragment inside map, which is cleaner.
    return (
        <>
            <tr style={{ borderBottom: '1px solid #1e293b', cursor: 'pointer' }}
                onClick={onToggle}>
                <td style={{ padding: '0.5rem', fontFamily: 'monospace',
                             color: '#94a3b8', maxWidth: 200, overflow: 'hidden',
                             textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {entry.command}
                </td>
                <td style={{ textAlign: 'center' }}>{entry.source || '—'}</td>
                <td style={{ textAlign: 'center' }}>{entry.tag || '—'}</td>
                <td style={{ textAlign: 'center', color: '#ef4444' }}>{entry.failure_reason}</td>
                <td style={{ textAlign: 'center' }}>{entry.retry_count || 0}</td>
                <td style={{ textAlign: 'center', color: '#94a3b8', fontSize: 11 }}>
                    {entry.moved_at ? new Date(entry.moved_at * 1000).toLocaleString() : '—'}
                </td>
                <td style={{ textAlign: 'center' }}>
                    <button onClick={ev => { ev.stopPropagation(); retryDLQEntry(entry.id); }}
                            style={{ marginRight: 4, padding: '0.2rem 0.6rem',
                                     background: '#3b82f6', color: '#fff',
                                     border: 'none', borderRadius: 3, cursor: 'pointer' }}>
                        Retry
                    </button>
                    <button onClick={ev => { ev.stopPropagation(); dismissDLQEntry(entry.id); }}
                            style={{ padding: '0.2rem 0.6rem', background: '#374151',
                                     color: '#fff', border: 'none', borderRadius: 3, cursor: 'pointer' }}>
                        Dismiss
                    </button>
                </td>
            </tr>
            {isExpanded && (
                <tr>
                    <td colSpan={7} style={{ padding: '0.5rem 1rem',
                                             background: '#0f172a', fontFamily: 'monospace',
                                             fontSize: 11, color: '#94a3b8' }}>
                        <div><strong>stdout:</strong> {entry.stdout_tail || '(empty)'}</div>
                        <div><strong>stderr:</strong> {entry.stderr_tail || '(empty)'}</div>
                    </td>
                </tr>
            )}
        </>
    );
}
