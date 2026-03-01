import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import {
    status, scheduleJobs, scheduleEvents, models,
    fetchSchedule, toggleScheduleJob, triggerRebalance, runScheduleJobNow,
    updateScheduleJob, fetchModels,
} from '../store';
import { GanttChart } from '../components/GanttChart';
import { ModelBadge } from '../components/ModelBadge';

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

function formatDuration(secs) {
    if (secs === null || secs === undefined || secs < 0) return '--';
    const s = Math.round(secs);
    if (s < 60) return `${s}s`;
    const mins = Math.floor(s / 60);
    const rem = s % 60;
    if (mins < 60) return `${mins}m ${rem}s`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
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

// Change 5: relative time for rebalance log
function relativeTimeLog(ts) {
    if (!ts) return '—';
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    const dateObj = new Date(ts * 1000);
    return `${dateObj.toLocaleDateString()} ${dateObj.toLocaleTimeString()}`;
}

// Change 6: debounce hook for live search
function useDebounce(value, delay) {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
        const timer = setTimeout(() => setDebounced(value), delay);
        return () => clearTimeout(timer);
    }, [value, delay]);
    return debounced;
}


export default function ScheduleTab() {
    // tick increments every second to force live countdown re-renders
    const [tick, setTick] = useState(0);
    const [runningIds, setRunningIds] = useState(new Set());
    const [runError, setRunError] = useState(null);
    const [editingInterval, setEditingInterval] = useState(null);   // { id, value }
    const [editingPriority, setEditingPriority] = useState(null);   // { id, value }
    const [editingModel, setEditingModel] = useState(null);         // { id, value }
    // Change 4: rebalance button state
    const [rebalancing, setRebalancing] = useState(false);
    const [rebalanceFlash, setRebalanceFlash] = useState(null);     // 'ok' | 'error' | null
    // Change 6: search state
    const [search, setSearch] = useState('');

    const refreshingRef = useRef(false);

    // Change 6: debounced search
    const debouncedSearch = useDebounce(search, 300);

    useEffect(() => {
        fetchSchedule();
        fetchModels();
        const tickInterval = setInterval(() => setTick(t => t + 1), 1000);
        const refreshInterval = setInterval(() => {
            if (!refreshingRef.current) {
                refreshingRef.current = true;
                fetchSchedule().finally(() => { refreshingRef.current = false; });
            }
        }, 10000);
        return () => {
            clearInterval(tickInterval);
            clearInterval(refreshInterval);
        };
    }, []);

    async function handleModelSave(rjId, modelName) {
        try {
            await updateScheduleJob(rjId, { model: modelName || null });
        } catch (e) {
            console.error('Model update failed:', e);
            setRunError('Failed to update model');
        } finally {
            setEditingModel(null);
        }
    }

    // Running job banner — reference tick to subscribe to 1s elapsed updates
    void tick;
    const _daemonState = status.value?.daemon?.state ?? '';
    const runningJob = (_daemonState === 'running' || _daemonState.startsWith('running('))
        ? status.value?.current_job
        : null;
    const runningElapsed = runningJob?.started_at
        ? Math.floor(Date.now() / 1000 - runningJob.started_at)
        : null;

    const jobs = scheduleJobs.value;
    const events = scheduleEvents.value;

    // Change 6: filtered jobs for display
    const visibleJobs = debouncedSearch
        ? jobs.filter(rj => rj.name.toLowerCase().includes(debouncedSearch.toLowerCase()))
        : jobs;

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

    // Change 7: confirmation for long-running jobs
    async function handleRunNow(rj) {
        if (rj.estimated_duration > 300) {
            const ok = window.confirm(`Run "${rj.name}" now? Estimated duration: ~${Math.round(rj.estimated_duration / 60)}m`);
            if (!ok) return;
        }
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

    // Change 4: rebalance with loading/success/error state
    async function handleRebalance() {
        setRebalancing(true);
        setRebalanceFlash(null);
        try {
            await triggerRebalance();
            setRebalanceFlash('ok');
            setTimeout(() => setRebalanceFlash(null), 2000);
            fetchSchedule();
        } catch (err) {
            console.error('Rebalance failed:', err);
            setRebalanceFlash('error');
            setTimeout(() => setRebalanceFlash(null), 4000);
        } finally {
            setRebalancing(false);
        }
    }

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                    Schedule
                </h2>
                {/* Change 4: rebalance button with loading/success/error feedback */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {rebalanceFlash === 'error' && (
                        <span style={{ color: 'var(--status-error)', fontSize: 'var(--type-label)',
                                       fontFamily: 'var(--font-mono)' }}>
                            Rebalance failed
                        </span>
                    )}
                    <button
                        class="t-btn t-btn-primary px-4 py-2 text-sm"
                        onClick={handleRebalance}
                        disabled={rebalancing}
                        style={{
                            opacity: rebalancing ? 0.6 : 1,
                            background: rebalanceFlash === 'ok' ? 'var(--status-success)' : undefined,
                            transition: 'background 0.3s ease',
                        }}>
                        {rebalancing ? '…' : rebalanceFlash === 'ok' ? '✓ Done' : 'Rebalance Now'}
                    </button>
                </div>
            </div>

            {runError && (
                <div style={{ padding: '0.5rem 0.75rem', background: 'var(--status-error)',
                              color: 'var(--accent-text)', borderRadius: 'var(--radius)',
                              fontSize: 'var(--type-body)', fontFamily: 'var(--font-mono)' }}>
                    {runError}
                </div>
            )}

            {runningJob && (
                <div class="t-frame" style={{
                    borderLeft: '3px solid var(--status-success)',
                    padding: '0.5rem 0.75rem',
                    display: 'flex', alignItems: 'center', gap: '0.75rem',
                    flexWrap: 'wrap',
                }}>
                    <span style={{ color: 'var(--status-success)', fontFamily: 'var(--font-mono)',
                                   fontWeight: 700, fontSize: 'var(--type-label)',
                                   textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
                        ● Running
                    </span>
                    <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)',
                                   fontSize: 'var(--type-body)' }}>
                        {runningJob.source || '—'}
                    </span>
                    {runningJob.model && (
                        <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)',
                                       fontSize: 'var(--type-label)' }}>
                            {runningJob.model}
                        </span>
                    )}
                    <span style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)',
                                   fontSize: 'var(--type-label)', marginLeft: 'auto',
                                   fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                        {formatDuration(runningElapsed)}
                        {runningJob.estimated_duration
                            ? ` / ~${formatDuration(runningJob.estimated_duration)}`
                            : ''}
                    </span>
                </div>
            )}

            <GanttChart jobs={jobs} tick={tick} windowHours={24} />

            {jobs.length === 0 ? (
                <div class="t-frame" style={{ textAlign: 'center', padding: '2rem',
                                              color: 'var(--text-tertiary)' }}>
                    No recurring jobs. Add one via CLI:{' '}
                    <code class="data-mono">ollama-queue schedule add</code>
                </div>
            ) : (
                <>
                    {/* Change 6: live search bar */}
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.5rem' }}>
                        <input
                            class="t-input"
                            type="text"
                            placeholder="Filter jobs…"
                            value={search}
                            onInput={ev => setSearch(ev.target.value)}
                            style={{ width: '200px', padding: '4px 8px', fontSize: 'var(--type-body)', fontFamily: 'var(--font-mono)' }}
                        />
                        {search && (
                            <button class="t-btn t-btn-secondary"
                                    style={{ padding: '4px 8px', fontSize: 'var(--type-label)' }}
                                    onClick={() => setSearch('')}>✕</button>
                        )}
                    </div>
                    {visibleJobs.length === 0 && debouncedSearch && (
                        <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-body)', textAlign: 'center', padding: '1rem 0' }}>
                            No jobs match "{debouncedSearch}"
                        </p>
                    )}
                    {/* Change 1: outer frame allows overflow-x; inner scroll div handles horizontal */}
                    <div class="t-frame" style={{ padding: 0, overflowX: 'auto' }}>
                        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                            <table style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse',
                                            fontSize: 'var(--type-body)' }}>
                                <thead>
                                    <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                                 background: 'var(--bg-surface-raised)' }}>
                                        {['Name', 'Model', 'VRAM', 'Tag', 'Schedule', 'Priority', 'Next Run', 'ETA', '★', 'Enabled', ''].map(col => (
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
                                                // Change 1: sticky Name column header
                                                ...(col === 'Name' ? {
                                                    position: 'sticky',
                                                    left: 0,
                                                    background: 'var(--bg-surface-raised)',
                                                    zIndex: 1,
                                                } : {}),
                                            }}>{col}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {/* Change 6: use visibleJobs instead of jobs */}
                                    {visibleJobs.map(rj => {
                                        const cat = priorityCategory(rj.priority);
                                        const color = CATEGORY_COLORS[cat];
                                        const overdue = rj.next_run < Date.now() / 1000;
                                        const isRunning = runningIds.has(rj.id);
                                        // Read tick to subscribe this row to per-second updates
                                        void tick;
                                        return (
                                            <tr key={rj.id}
                                                style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                                {/* Change 1: sticky Name td */}
                                                <td style={{
                                                    padding: '0.5rem 0.75rem',
                                                    borderLeft: `3px solid ${color}`,
                                                    position: 'sticky',
                                                    left: 0,
                                                    background: 'var(--bg-panel)',
                                                    zIndex: 1,
                                                }}>
                                                    <span style={{ color: 'var(--text-primary)',
                                                                   fontFamily: 'var(--font-mono)',
                                                                   fontSize: 'var(--type-body)' }}>
                                                        {rj.name}
                                                    </span>
                                                </td>
                                                <td style={{ textAlign: 'center', padding: '0.5rem' }}>
                                                    {editingModel && editingModel.id === rj.id ? (
                                                        <select
                                                            value={editingModel.value}
                                                            ref={el => el && el.focus()}
                                                            onChange={ev => handleModelSave(rj.id, ev.target.value)}
                                                            onBlur={() => setEditingModel(null)}
                                                            onKeyDown={ev => { if (ev.key === 'Escape') setEditingModel(null); }}
                                                            style={{
                                                                fontFamily: 'var(--font-mono)',
                                                                fontSize: 'var(--type-label)',
                                                                background: 'var(--bg-inset)',
                                                                color: 'var(--text-primary)',
                                                                border: '1px solid var(--accent)',
                                                                borderRadius: 'var(--radius)',
                                                                padding: '0.1rem 0.3rem',
                                                                maxWidth: '10rem',
                                                            }}>
                                                            <option value="">— none —</option>
                                                            {models.value.map(modelRow => (
                                                                <option key={modelRow.name} value={modelRow.name}>{modelRow.name}</option>
                                                            ))}
                                                            {rj.model && !models.value.find(modelRow => modelRow.name === rj.model) && (
                                                                <option value={rj.model}>{rj.model}</option>
                                                            )}
                                                        </select>
                                                    ) : (
                                                        <span
                                                            style={{ cursor: 'pointer', display: 'flex', flexDirection: 'column',
                                                                     alignItems: 'center', gap: '0.2rem' }}
                                                            title="Click to change model"
                                                            onClick={() => setEditingModel({ id: rj.id, value: rj.model || '' })}>
                                                            {rj.model ? (
                                                                <>
                                                                    <ModelBadge profile={rj.model_profile} typeTag={rj.model_type} />
                                                                    <div style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                                                                                  fontFamily: 'var(--font-mono)',
                                                                                  borderBottom: '1px dashed var(--text-tertiary)' }}>
                                                                        {rj.model.split(':')[0]}
                                                                    </div>
                                                                </>
                                                            ) : (
                                                                <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)',
                                                                               borderBottom: '1px dashed var(--text-tertiary)' }}>—</span>
                                                            )}
                                                        </span>
                                                    )}
                                                </td>
                                                <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
                                                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                                                    {rj.model_vram_mb ? `${(rj.model_vram_mb / 1024).toFixed(1)} GB` : '—'}
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
                                                {/* Changes 2 & 3: Next Run with tooltip + overdue badge */}
                                                <td style={{ textAlign: 'center',
                                                             fontFamily: 'var(--font-mono)',
                                                             color: overdue
                                                                 ? 'var(--status-error)'
                                                                 : 'var(--text-primary)',
                                                             fontVariantNumeric: 'tabular-nums',
                                                             minWidth: '7rem' }}>
                                                    <span title={new Date(rj.next_run * 1000).toLocaleString()}>
                                                        {formatCountdown(rj.next_run)}
                                                        {overdue && (() => {
                                                            const overdueSeconds = Date.now() / 1000 - rj.next_run;
                                                            const isSevere = rj.interval_seconds && overdueSeconds > rj.interval_seconds * 2;
                                                            return (
                                                                <span style={{
                                                                    marginLeft: 6,
                                                                    fontSize: 'var(--type-micro)',
                                                                    color: isSevere ? 'var(--status-error)' : '#f97316',
                                                                    background: isSevere ? 'rgba(239,68,68,0.12)' : 'rgba(249,115,22,0.12)',
                                                                    border: `1px solid ${isSevere ? 'rgba(239,68,68,0.4)' : 'rgba(249,115,22,0.4)'}`,
                                                                    borderRadius: 4,
                                                                    padding: '1px 5px',
                                                                }}>OVERDUE</span>
                                                            );
                                                        })()}
                                                    </span>
                                                </td>
                                                <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
                                                             fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                                                    {rj.estimated_duration
                                                        ? `~${Math.round(rj.estimated_duration / 60)}m`
                                                        : '—'}
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
                    </div>
                </>
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
                                {events.slice(0, 20).map(evItem => (
                                    <tr key={evItem.id}
                                        style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                        {/* Change 5: relative timestamps with absolute on hover */}
                                        <td style={{ padding: '0.4rem 0.75rem',
                                                     color: 'var(--text-tertiary)',
                                                     fontFamily: 'var(--font-mono)',
                                                     whiteSpace: 'nowrap' }}>
                                            <span title={new Date(evItem.timestamp * 1000).toLocaleString()}>
                                                {relativeTimeLog(evItem.timestamp)}
                                            </span>
                                        </td>
                                        <td style={{ padding: '0.4rem 0.75rem' }}>
                                            <code class="data-mono"
                                                  style={{ color: 'var(--accent)',
                                                           fontSize: 'var(--type-label)' }}>
                                                {evItem.event_type}
                                            </code>
                                        </td>
                                        <td style={{ padding: '0.4rem 0.75rem',
                                                     color: 'var(--text-secondary)' }}>
                                            {evItem.details || '—'}
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
