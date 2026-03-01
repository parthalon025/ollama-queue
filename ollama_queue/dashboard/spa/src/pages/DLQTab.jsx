import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import {
    dlqEntries, fetchDLQ,
    retryDLQEntry, retryAllDLQ, dismissDLQEntry, clearDLQ,
} from '../store';

export default function DLQTab() {
    const [expanded, setExpanded] = useState(null);
    const [retryingAll, setRetryingAll] = useState(false);
    useEffect(() => { fetchDLQ(); }, []);
    const entries = dlqEntries.value;

    async function handleRetryAll() {
        if (!window.confirm(`Retry all ${entries.length} failed jobs?`)) return;
        setRetryingAll(true);
        try {
            await retryAllDLQ();
            await fetchDLQ();
        } finally {
            setRetryingAll(false);
        }
    }

    if (entries.length === 0) {
        return (
            <div class="flex flex-col gap-4 animate-page-enter"
                 style={{ padding: '3rem', textAlign: 'center' }}>
                <div style={{ fontSize: 48, color: 'var(--status-healthy)' }}>✓</div>
                <div style={{ color: 'var(--text-tertiary)',
                              fontFamily: 'var(--font-mono)',
                              fontSize: 'var(--type-body)' }}>
                    No failed jobs
                </div>
            </div>
        );
    }

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                    Dead Letter Queue{' '}
                    <span class="data-mono"
                          style={{ fontSize: 'var(--type-body)',
                                   color: 'var(--status-error)' }}>
                        ({entries.length})
                    </span>
                </h2>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button class="t-btn t-btn-primary px-4 py-2 text-sm"
                            onClick={handleRetryAll}
                            disabled={retryingAll}
                            style={{ opacity: retryingAll ? 0.6 : 1 }}>
                        {retryingAll ? '⟳ Retrying…' : `Retry All (${entries.length})`}
                    </button>
                    <button class="t-btn t-btn-secondary px-4 py-2 text-sm"
                            onClick={clearDLQ}>
                        Clear Resolved
                    </button>
                </div>
            </div>

            <div class="t-frame" style={{ padding: 0, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse',
                                fontSize: 'var(--type-label)' }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                     background: 'var(--bg-surface-raised)' }}>
                            {['Command', 'Source', 'Tag', 'Failure', 'Retries', 'Moved', 'Actions'].map(col => (
                                <th key={col} style={{
                                    textAlign: col === 'Command' ? 'left' : 'center',
                                    padding: '0.5rem 0.75rem',
                                    color: 'var(--text-secondary)',
                                    fontWeight: 600,
                                    textTransform: 'uppercase',
                                    letterSpacing: '0.05em',
                                    fontFamily: 'var(--font-mono)',
                                }}>{col}</th>
                            ))}
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
        </div>
    );
}

function DLQRow({ entry, isExpanded, onToggle }) {
    return (
        <>
            <tr style={{ borderBottom: '1px solid var(--border-subtle)', cursor: 'pointer' }}
                onClick={onToggle}>
                <td style={{ padding: '0.5rem 0.75rem',
                             fontFamily: 'var(--font-mono)',
                             color: 'var(--text-secondary)',
                             maxWidth: 220, overflow: 'hidden',
                             textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={entry.command}>
                    {entry.command}
                </td>
                <td style={{ textAlign: 'center', color: 'var(--text-primary)' }}>
                    {entry.source || '—'}
                </td>
                <td style={{ textAlign: 'center', color: 'var(--text-secondary)',
                             fontFamily: 'var(--font-mono)' }}>
                    {entry.tag || '—'}
                </td>
                <td style={{ textAlign: 'center', color: 'var(--status-error)',
                             fontFamily: 'var(--font-mono)' }}>
                    {entry.failure_reason}
                </td>
                <td style={{ textAlign: 'center', color: 'var(--text-primary)',
                             fontFamily: 'var(--font-mono)' }}>
                    {entry.retry_count || 0}
                </td>
                <td style={{ textAlign: 'center', color: 'var(--text-tertiary)',
                             fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)' }}>
                    {entry.moved_at ? new Date(entry.moved_at * 1000).toLocaleString() : '—'}
                </td>
                <td style={{ textAlign: 'center' }}>
                    <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                        <button class="t-btn t-btn-primary"
                                style={{ padding: '0.2rem 0.6rem', fontSize: 'var(--type-label)' }}
                                onClick={ev => { ev.stopPropagation(); retryDLQEntry(entry.id); }}>
                            Retry
                        </button>
                        <button class="t-btn t-btn-secondary"
                                style={{ padding: '0.2rem 0.6rem', fontSize: 'var(--type-label)' }}
                                onClick={ev => { ev.stopPropagation(); dismissDLQEntry(entry.id); }}>
                            Dismiss
                        </button>
                    </div>
                </td>
            </tr>
            {isExpanded && (
                <tr>
                    <td colSpan={7}
                        style={{ padding: '0.75rem 1rem',
                                 background: 'var(--bg-inset)',
                                 borderBottom: '1px solid var(--border-subtle)' }}>
                        <div style={{ fontFamily: 'var(--font-mono)',
                                      fontSize: 'var(--type-label)',
                                      color: 'var(--text-secondary)',
                                      display: 'flex', flexDirection: 'column', gap: 4 }}>
                            <div>
                                <span style={{ color: 'var(--text-tertiary)',
                                               textTransform: 'uppercase',
                                               letterSpacing: '0.05em',
                                               fontSize: 'var(--type-micro)' }}>
                                    command
                                </span>
                                <pre style={{ margin: '2px 0 0', color: 'var(--text-primary)',
                                              whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                    {entry.command}
                                </pre>
                            </div>
                            <div>
                                <span style={{ color: 'var(--text-tertiary)',
                                               textTransform: 'uppercase',
                                               letterSpacing: '0.05em',
                                               fontSize: 'var(--type-micro)' }}>
                                    stdout
                                </span>
                                <pre style={{ margin: '2px 0 0', color: 'var(--text-secondary)',
                                              whiteSpace: 'pre-wrap' }}>
                                    {entry.stdout_tail || '(empty)'}
                                </pre>
                            </div>
                            <div>
                                <span style={{ color: 'var(--text-tertiary)',
                                               textTransform: 'uppercase',
                                               letterSpacing: '0.05em',
                                               fontSize: 'var(--type-micro)' }}>
                                    stderr
                                </span>
                                <pre style={{ margin: '2px 0 0', color: 'var(--status-error)',
                                              whiteSpace: 'pre-wrap' }}>
                                    {entry.stderr_tail || '(empty)'}
                                </pre>
                            </div>
                        </div>
                    </td>
                </tr>
            )}
        </>
    );
}
