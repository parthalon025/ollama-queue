import { h, Fragment } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import {
    status, scheduleJobs, scheduleEvents, models, loadMap,
    fetchSchedule, fetchLoadMap, toggleScheduleJob, triggerRebalance, runScheduleJobNow,
    updateScheduleJob, fetchModels, batchToggleJobs, batchRunJobs,
    fetchJobRuns, deleteScheduleJob,
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
    if (!seconds) return '\u2014';
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
}

// Parse shorthand like "4h", "30m", "1d", "7d", "90s", or plain seconds
function parseInterval(str) {
    if (!str) return null;
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

// Traffic intensity ρ = sum(estimated_duration) / 86400.
// Research threshold: keep ρ < 0.80 (Kingman's formula diverges as ρ → 1).
// Includes ALL jobs (enabled + disabled) — represents maximum scheduled load.
// Heavy-model fallback: 1800s; others: 600s (10m default for LLM tasks).
function computeRho(jobList) {
    if (jobList.length === 0) return 0;
    const totalSecs = jobList.reduce((sum, j) => {
        const fallback = j.model_profile === 'heavy' ? 1800 : 600;
        return sum + (j.estimated_duration || fallback);
    }, 0);
    return totalSecs / 86400;
}

function rhoStatus(rho) {
    if (rho < 0.60) return { label: 'safe', color: 'var(--status-healthy)' };
    if (rho < 0.80) return { label: 'moderate', color: 'var(--status-warning)' };
    return { label: 'dense', color: 'var(--status-error)' };
}

// Priority design token colors (theme-aware)
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

function relativeTimeLog(ts) {
    if (!ts) return '\u2014';
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    const dateObj = new Date(ts * 1000);
    return `${dateObj.toLocaleDateString()} ${dateObj.toLocaleTimeString()}`;
}

function useDebounce(value, delay) {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
        const timer = setTimeout(() => setDebounced(value), delay);
        return () => clearTimeout(timer);
    }, [value, delay]);
    return debounced;
}

// --- Grouping ---

const TAG_ORDER = ['aria', 'telegram', 'lessons', 'notion', 'embeddings'];

function groupJobsByTag(jobList) {
    const groups = {};
    for (const job of jobList) {
        const tag = job.tag || 'other';
        if (!groups[tag]) groups[tag] = [];
        groups[tag].push(job);
    }
    const ordered = TAG_ORDER.filter(tag => groups[tag]).map(tag => ({ tag, jobs: groups[tag] }));
    const extra = Object.keys(groups)
        .filter(tag => !TAG_ORDER.includes(tag) && tag !== 'other')
        .sort();
    for (const tag of extra) ordered.push({ tag, jobs: groups[tag] });
    if (groups['other']) ordered.push({ tag: 'other', jobs: groups['other'] });
    return ordered;
}

function groupNextDue(groupJobs) {
    let min = Infinity;
    for (const rj of groupJobs) {
        if (rj.enabled && rj.next_run < min) min = rj.next_run;
    }
    return min === Infinity ? null : min;
}

// --- Table layout ---

const COLUMNS = ['Name', 'Model', 'VRAM', 'Schedule', 'Priority', 'Next Run', 'ETA', 'Check', 'Runs', '\u2605', 'Enabled', ''];
const COL_COUNT = COLUMNS.length;

const STATUS_COLORS = {
    completed: 'var(--status-success)',
    failed: 'var(--status-error)',
    killed: 'var(--status-error)',
    pending: 'var(--text-tertiary)',
    running: 'var(--accent)',
};

// Shared styles for detail panel form
const labelStyle = {
    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
    color: 'var(--text-tertiary)', fontWeight: 600,
    textTransform: 'uppercase', letterSpacing: '0.03em',
    marginBottom: '0.2rem', display: 'block',
};

const inputStyle = {
    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
    background: 'var(--bg-surface-raised)', color: 'var(--text-primary)',
    border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
    padding: '0.3rem 0.5rem', width: '100%',
};


export default function Plan() {
    const [tick, setTick] = useState(0);
    const [runningIds, setRunningIds] = useState(new Set());
    const [runError, setRunError] = useState(null);
    const [rebalancing, setRebalancing] = useState(false);
    const [rebalanceFlash, setRebalanceFlash] = useState(null);
    const [search, setSearch] = useState('');

    // Group collapse state (persisted in localStorage)
    const [collapsedGroups, setCollapsedGroups] = useState(() => {
        try { return JSON.parse(localStorage.getItem('schedule-collapsed') || '[]'); }
        catch { return []; }
    });

    // Detail panel
    const [expandedJobId, setExpandedJobId] = useState(null);
    const [jobRuns, setJobRuns] = useState({});
    const [editForm, setEditForm] = useState(null);
    const [saving, setSaving] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [batchRunningTags, setBatchRunningTags] = useState(new Set());

    const refreshingRef = useRef(false);
    const rebalanceTimerRef = useRef(null);
    const debouncedSearch = useDebounce(search, 300);

    useEffect(() => {
        fetchSchedule();
        fetchLoadMap();
        fetchModels();
        const tickInterval = setInterval(() => setTick(t => t + 1), 1000);
        const refreshInterval = setInterval(() => {
            if (!refreshingRef.current) {
                refreshingRef.current = true;
                Promise.all([fetchSchedule(), fetchLoadMap()])
                    .finally(() => { refreshingRef.current = false; });
            }
        }, 10000);
        return () => {
            clearInterval(tickInterval);
            clearInterval(refreshInterval);
            if (rebalanceTimerRef.current) clearTimeout(rebalanceTimerRef.current);
        };
    }, []);

    // --- Handlers ---

    function toggleGroup(tag) {
        setCollapsedGroups(prev => {
            const next = prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag];
            localStorage.setItem('schedule-collapsed', JSON.stringify(next));
            return next;
        });
    }

    async function toggleJobDetail(rjId) {
        if (expandedJobId === rjId) {
            setExpandedJobId(null);
            setEditForm(null);
            return;
        }
        setExpandedJobId(rjId);
        const rj = jobs.find(j => j.id === rjId);
        if (rj) {
            setEditForm({
                id: rjId,
                interval: formatInterval(rj.interval_seconds),
                cron_expression: rj.cron_expression || '',
                priority: String(rj.priority),
                model: rj.model || '',
                timeout: formatInterval(rj.timeout),
                max_retries: String(rj.max_retries || 0),
                pinned: !!rj.pinned,
                enabled: !!rj.enabled,
            });
        }
        try {
            const runs = await fetchJobRuns(rjId);
            setJobRuns(prev => ({ ...prev, [rjId]: runs }));
        } catch (err) {
            console.error('Failed to fetch runs:', err);
        }
    }

    async function handleDetailSave() {
        if (!editForm || saving) return;
        const rj = jobs.find(j => j.id === editForm.id);
        if (!rj) return;
        const updates = {};

        if (!editForm.cron_expression) {
            const secs = parseInterval(editForm.interval);
            if (secs && secs !== rj.interval_seconds) updates.interval_seconds = secs;
        }
        const pri = parseInt(editForm.priority, 10);
        if (!isNaN(pri) && pri >= 1 && pri <= 10 && pri !== rj.priority) updates.priority = pri;
        if (editForm.model !== (rj.model || '')) updates.model = editForm.model || null;
        const timeout = parseInterval(editForm.timeout);
        if (timeout && timeout !== rj.timeout) updates.timeout = timeout;
        const retries = parseInt(editForm.max_retries, 10);
        if (!isNaN(retries) && retries >= 0 && retries !== (rj.max_retries || 0)) updates.max_retries = retries;
        if (editForm.pinned !== !!rj.pinned) updates.pinned = editForm.pinned;
        if (editForm.enabled !== !!rj.enabled) updates.enabled = editForm.enabled;

        if (Object.keys(updates).length === 0) {
            setExpandedJobId(null);
            setEditForm(null);
            return;
        }
        setSaving(true);
        try {
            await updateScheduleJob(editForm.id, updates);
            setExpandedJobId(null);
            setEditForm(null);
        } catch (err) {
            setRunError(`Failed to save: ${err.message}`);
        } finally {
            setSaving(false);
        }
    }

    async function handleDelete(rjId) {
        const rj = jobs.find(j => j.id === rjId);
        if (!window.confirm(`Delete recurring job "${rj?.name}"? This cannot be undone.`)) return;
        setDeleting(true);
        try {
            await deleteScheduleJob(rjId);
            setExpandedJobId(null);
            setEditForm(null);
        } catch (err) {
            setRunError(`Failed to delete: ${err.message}`);
        } finally {
            setDeleting(false);
        }
    }

    async function handleRunNow(rj) {
        if (rj.estimated_duration > 300) {
            const ok = window.confirm(`Run "${rj.name}" now? Estimated duration: ~${Math.round(rj.estimated_duration / 60)}m`);
            if (!ok) return;
        }
        setRunningIds(prev => new Set([...prev, rj.id]));
        setRunError(null);
        try {
            await runScheduleJobNow(rj.id);
        } catch (err) {
            console.error('Run Now failed:', err);
            setRunError(`Failed to run "${rj.name}"`);
        } finally {
            setRunningIds(prev => {
                const next = new Set(prev);
                next.delete(rj.id);
                return next;
            });
        }
    }

    async function handlePinToggle(rj) {
        try {
            await updateScheduleJob(rj.id, { pinned: !rj.pinned });
        } catch (err) {
            setRunError(`Failed to toggle pin for "${rj.name}"`);
        }
    }

    async function handleBatchRun(tag) {
        setBatchRunningTags(prev => new Set([...prev, tag]));
        try {
            await batchRunJobs(tag);
        } catch (err) {
            setRunError(`Batch run failed for ${tag}: ${err.message}`);
        } finally {
            setBatchRunningTags(prev => {
                const next = new Set(prev);
                next.delete(tag);
                return next;
            });
        }
    }

    async function handleBatchToggle(tag, enabled) {
        try {
            await batchToggleJobs(tag, enabled);
        } catch (err) {
            setRunError(`Batch toggle failed for ${tag}: ${err.message}`);
        }
    }

    async function handleRebalance() {
        setRebalancing(true);
        setRebalanceFlash(null);
        try {
            await triggerRebalance();
            setRebalanceFlash('ok');
            if (rebalanceTimerRef.current) clearTimeout(rebalanceTimerRef.current);
            rebalanceTimerRef.current = setTimeout(() => setRebalanceFlash(null), 2000);
        } catch (err) {
            console.error('Rebalance failed:', err);
            setRebalanceFlash('error');
            if (rebalanceTimerRef.current) clearTimeout(rebalanceTimerRef.current);
            rebalanceTimerRef.current = setTimeout(() => setRebalanceFlash(null), 4000);
        } finally {
            setRebalancing(false);
        }
    }

    // --- Derived data ---

    // Reference tick for per-second countdown updates
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

    const visibleJobs = debouncedSearch
        ? jobs.filter(rj => rj.name.toLowerCase().includes(debouncedSearch.toLowerCase()))
        : jobs;

    const groups = groupJobsByTag(visibleJobs);

    // --- Render helpers ---

    function renderGroupHeader(group) {
        const { tag, jobs: groupJobs } = group;
        const collapsed = collapsedGroups.includes(tag);
        const nextDue = groupNextDue(groupJobs);
        const allEnabled = groupJobs.every(rj => rj.enabled);
        const isBatchRunning = batchRunningTags.has(tag);

        return (
            <tr key={`group-${tag}`}
                style={{
                    background: 'var(--bg-surface-raised)',
                    borderBottom: '2px solid var(--border-subtle)',
                    cursor: 'pointer',
                    userSelect: 'none',
                }}
                onClick={() => toggleGroup(tag)}>
                <td colSpan={COL_COUNT} style={{ padding: '0.6rem 0.75rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
                                           color: 'var(--text-tertiary)', width: '1rem', textAlign: 'center' }}>
                                {collapsed ? '\u25B6' : '\u25BC'}
                            </span>
                            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                                           fontSize: 'var(--type-body)', color: 'var(--text-primary)',
                                           textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                {tag}
                            </span>
                            <span style={{
                                fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                color: 'var(--text-secondary)',
                                background: 'var(--bg-inset)', padding: '0.1rem 0.4rem',
                                borderRadius: 'var(--radius)',
                            }}>
                                {groupJobs.length} {groupJobs.length === 1 ? 'job' : 'jobs'}
                            </span>
                            {nextDue && (
                                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                               color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums' }}>
                                    next: {formatCountdown(nextDue)}
                                </span>
                            )}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
                             onClick={ev => ev.stopPropagation()}>
                            <button
                                class="t-btn t-btn-secondary"
                                style={{ fontSize: 'var(--type-label)', padding: '0.15rem 0.5rem',
                                         opacity: isBatchRunning ? 0.5 : 1 }}
                                disabled={isBatchRunning}
                                onClick={() => handleBatchRun(tag)}>
                                {isBatchRunning ? '\u2026' : '\u25B6 Run All'}
                            </button>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem',
                                            fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)',
                                            color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                <input type="checkbox" checked={allEnabled}
                                       style={{ accentColor: 'var(--accent)', width: 14, height: 14 }}
                                       onChange={() => handleBatchToggle(tag, !allEnabled)} />
                                All
                            </label>
                        </div>
                    </div>
                </td>
            </tr>
        );
    }

    function renderJobRow(rj) {
        const cat = priorityCategory(rj.priority);
        const color = CATEGORY_COLORS[cat];
        const overdue = rj.next_run < Date.now() / 1000;
        const isRunning = runningIds.has(rj.id);
        const isExpanded = expandedJobId === rj.id;

        return (
            <tr key={rj.id}
                style={{
                    borderBottom: isExpanded ? 'none' : '1px solid var(--border-subtle)',
                    cursor: 'pointer',
                    background: isExpanded ? 'var(--bg-inset)' : undefined,
                }}
                onClick={ev => {
                    const tagName = ev.target.tagName;
                    if (tagName === 'INPUT' || tagName === 'BUTTON' || tagName === 'SELECT') return;
                    toggleJobDetail(rj.id);
                }}>
                <td style={{
                    padding: '0.5rem 0.75rem',
                    borderLeft: `3px solid ${color}`,
                    position: 'sticky', left: 0,
                    background: isExpanded ? 'var(--bg-inset)' : 'var(--bg-surface-raised)',
                    zIndex: 1,
                }}>
                    <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)',
                                   fontSize: 'var(--type-body)' }}>
                        {rj.name}
                    </span>
                </td>
                <td style={{ textAlign: 'center', padding: '0.5rem' }}>
                    {rj.model ? (
                        <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.2rem' }}>
                            <ModelBadge profile={rj.model_profile} typeTag={rj.model_type} />
                            <div style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                                          fontFamily: 'var(--font-mono)' }}>
                                {rj.model.split(':')[0]}
                            </div>
                        </span>
                    ) : (
                        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>{'\u2014'}</span>
                    )}
                </td>
                <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                    {rj.model_vram_mb ? `${(rj.model_vram_mb / 1024).toFixed(1)} GB` : '\u2014'}
                </td>
                <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                    {rj.cron_expression ? (
                        <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--type-label)' }}>
                            {rj.cron_expression}
                        </span>
                    ) : (
                        <span>{formatInterval(rj.interval_seconds)}</span>
                    )}
                </td>
                <td style={{ textAlign: 'center' }}>
                    <span style={{
                        background: color, color: 'var(--accent-text)',
                        padding: '0.1rem 0.5rem', borderRadius: 'var(--radius)',
                        fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)', fontWeight: 600,
                    }}>
                        {cat} ({rj.priority})
                    </span>
                </td>
                <td style={{
                    textAlign: 'center', fontFamily: 'var(--font-mono)',
                    color: overdue ? 'var(--status-error)' : 'var(--text-primary)',
                    fontVariantNumeric: 'tabular-nums', minWidth: '7rem',
                }}>
                    <span title={new Date(rj.next_run * 1000).toLocaleString()}>
                        {formatCountdown(rj.next_run)}
                        {overdue && (() => {
                            const overdueSeconds = Date.now() / 1000 - rj.next_run;
                            const isSevere = rj.interval_seconds && overdueSeconds > rj.interval_seconds * 2;
                            return (
                                <span style={{
                                    marginLeft: 6, fontSize: 'var(--type-micro)',
                                    color: isSevere ? 'var(--status-error)' : '#f97316',
                                    background: isSevere ? 'rgba(239,68,68,0.12)' : 'rgba(249,115,22,0.12)',
                                    border: `1px solid ${isSevere ? 'rgba(239,68,68,0.4)' : 'rgba(249,115,22,0.4)'}`,
                                    borderRadius: 4, padding: '1px 5px',
                                }}>OVERDUE</span>
                            );
                        })()}
                    </span>
                </td>
                <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
                             fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                    {rj.estimated_duration ? `~${Math.round(rj.estimated_duration / 60)}m` : '\u2014'}
                </td>
                <td style={{ textAlign: 'center', fontSize: 'var(--type-label)',
                             color: 'var(--status-success)' }}>
                    {rj.check_command ? '\u2713' : ''}
                </td>
                <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                    {rj.max_runs != null ? `${rj.max_runs} left` : ''}
                </td>
                <td style={{ textAlign: 'center' }}>
                    <button
                        title={rj.pinned ? 'Pinned \u2014 click to unpin' : 'Click to pin this time slot'}
                        onClick={() => handlePinToggle(rj)}
                        style={{
                            background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.1rem',
                            color: rj.pinned ? 'var(--status-warning)' : 'var(--text-tertiary)',
                            opacity: rj.pinned ? 1 : 0.4,
                        }}>
                        {'\u2605'}
                    </button>
                </td>
                <td style={{ textAlign: 'center' }}>
                    <input type="checkbox" checked={!!rj.enabled}
                           style={{ accentColor: 'var(--accent)', width: 16, height: 16 }}
                           onChange={ev => toggleScheduleJob(rj.id, ev.target.checked)} />
                </td>
                <td style={{ textAlign: 'center', padding: '0.25rem 0.5rem' }}>
                    <button
                        class="t-btn t-btn-secondary"
                        style={{ fontSize: 'var(--type-label)', padding: '0.2rem 0.6rem',
                                 opacity: isRunning ? 0.5 : 1 }}
                        disabled={isRunning}
                        onClick={() => handleRunNow(rj)}>
                        {isRunning ? '\u2026' : '\u25B6'}
                    </button>
                </td>
            </tr>
        );
    }

    function renderDetailPanel(rjId) {
        if (!editForm || editForm.id !== rjId) return null;
        const rj = jobs.find(j => j.id === rjId);
        if (!rj) return null;
        const runs = jobRuns[rjId] || [];

        return (
            <tr key={`detail-${rjId}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                <td colSpan={COL_COUNT} style={{
                    padding: '1rem', background: 'var(--bg-inset)',
                    borderLeft: '3px solid var(--accent)',
                }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                        {/* Command */}
                        <div>
                            <label style={labelStyle}>Command</label>
                            <pre style={{
                                fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                color: 'var(--text-primary)', background: 'var(--bg-surface-raised)',
                                padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)',
                                margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                                border: '1px solid var(--border-subtle)',
                            }}>
                                {rj.command}
                            </pre>
                        </div>

                        {/* Edit form — 2-column grid */}
                        <div style={{
                            display: 'grid', gridTemplateColumns: '1fr 1fr',
                            gap: '0.75rem 1.5rem',
                        }}>
                            <div>
                                <label style={labelStyle}>
                                    {editForm.cron_expression ? 'Cron' : 'Interval'}
                                </label>
                                {editForm.cron_expression ? (
                                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
                                                   color: 'var(--text-secondary)' }}>
                                        {editForm.cron_expression}
                                    </span>
                                ) : (
                                    <input class="t-input" type="text" value={editForm.interval}
                                           onInput={ev => setEditForm(prev => ({ ...prev, interval: ev.target.value }))}
                                           placeholder="e.g. 4h, 30m, 1d"
                                           style={inputStyle} />
                                )}
                            </div>
                            <div>
                                <label style={labelStyle}>Priority (1-10)</label>
                                <input class="t-input" type="number" min="1" max="10"
                                       value={editForm.priority}
                                       onInput={ev => setEditForm(prev => ({ ...prev, priority: ev.target.value }))}
                                       style={inputStyle} />
                            </div>
                            <div>
                                <label style={labelStyle}>Model</label>
                                <select class="t-input" value={editForm.model}
                                        onChange={ev => setEditForm(prev => ({ ...prev, model: ev.target.value }))}
                                        style={{ ...inputStyle, width: '100%' }}>
                                    <option value="">{'\u2014'} none {'\u2014'}</option>
                                    {models.value.map(modelRow => (
                                        <option key={modelRow.name} value={modelRow.name}>{modelRow.name}</option>
                                    ))}
                                    {rj.model && !models.value.find(modelRow => modelRow.name === rj.model) && (
                                        <option value={rj.model}>{rj.model}</option>
                                    )}
                                </select>
                            </div>
                            <div>
                                <label style={labelStyle}>Timeout</label>
                                <input class="t-input" type="text" value={editForm.timeout}
                                       onInput={ev => setEditForm(prev => ({ ...prev, timeout: ev.target.value }))}
                                       placeholder="e.g. 10m, 1h"
                                       style={inputStyle} />
                            </div>
                            <div>
                                <label style={labelStyle}>Max Retries</label>
                                <input class="t-input" type="number" min="0" max="10"
                                       value={editForm.max_retries}
                                       onInput={ev => setEditForm(prev => ({ ...prev, max_retries: ev.target.value }))}
                                       style={inputStyle} />
                            </div>
                            <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'center', paddingTop: '1.2rem' }}>
                                <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem',
                                                fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                                color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                    <input type="checkbox" checked={editForm.pinned}
                                           style={{ accentColor: 'var(--status-warning)', width: 14, height: 14 }}
                                           onChange={ev => setEditForm(prev => ({ ...prev, pinned: ev.target.checked }))} />
                                    Pinned
                                </label>
                                <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem',
                                                fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                                color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                    <input type="checkbox" checked={editForm.enabled}
                                           style={{ accentColor: 'var(--accent)', width: 14, height: 14 }}
                                           onChange={ev => setEditForm(prev => ({ ...prev, enabled: ev.target.checked }))} />
                                    Enabled
                                </label>
                            </div>
                        </div>

                        {/* Recent runs */}
                        {runs.length > 0 && (
                            <div>
                                <label style={{ ...labelStyle, marginBottom: '0.3rem', display: 'block' }}>
                                    Recent Runs
                                </label>
                                <table style={{ width: '100%', borderCollapse: 'collapse',
                                                fontSize: 'var(--type-label)' }}>
                                    <thead>
                                        <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                            {['Status', 'Started', 'Duration', 'Exit'].map(col => (
                                                <th key={col} style={{
                                                    textAlign: 'left', padding: '0.3rem 0.5rem',
                                                    color: 'var(--text-tertiary)', fontWeight: 600,
                                                    fontFamily: 'var(--font-mono)',
                                                }}>{col}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {runs.map(run => (
                                            <tr key={run.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                                <td style={{ padding: '0.3rem 0.5rem' }}>
                                                    <span style={{
                                                        color: STATUS_COLORS[run.status] || 'var(--text-tertiary)',
                                                        fontFamily: 'var(--font-mono)', fontWeight: 600,
                                                    }}>
                                                        {run.status}
                                                    </span>
                                                </td>
                                                <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'var(--font-mono)',
                                                             color: 'var(--text-secondary)' }}>
                                                    {run.started_at
                                                        ? new Date(run.started_at * 1000).toLocaleString()
                                                        : '\u2014'}
                                                </td>
                                                <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'var(--font-mono)',
                                                             color: 'var(--text-secondary)' }}>
                                                    {run.duration != null ? formatDuration(run.duration) : '\u2014'}
                                                </td>
                                                <td style={{ padding: '0.3rem 0.5rem', fontFamily: 'var(--font-mono)',
                                                             color: 'var(--text-tertiary)' }}>
                                                    {run.exit_code != null ? run.exit_code : '\u2014'}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}

                        {/* Actions */}
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                            <button class="t-btn t-btn-primary"
                                    style={{ padding: '0.3rem 1rem', fontSize: 'var(--type-body)',
                                             opacity: saving ? 0.6 : 1 }}
                                    disabled={saving}
                                    onClick={handleDetailSave}>
                                {saving ? 'Saving\u2026' : 'Save'}
                            </button>
                            <button class="t-btn t-btn-secondary"
                                    style={{ padding: '0.3rem 0.75rem', fontSize: 'var(--type-body)' }}
                                    onClick={() => { setExpandedJobId(null); setEditForm(null); }}>
                                Cancel
                            </button>
                            <div style={{ flex: 1 }} />
                            <button class="t-btn t-btn-secondary"
                                    style={{ padding: '0.3rem 0.75rem', fontSize: 'var(--type-body)' }}
                                    onClick={() => handleRunNow(rj)}>
                                {'\u25B6'} Run Now
                            </button>
                            <button class="t-btn"
                                    style={{
                                        padding: '0.3rem 0.75rem', fontSize: 'var(--type-body)',
                                        color: 'var(--status-error)', border: '1px solid var(--status-error)',
                                        background: 'transparent', opacity: deleting ? 0.6 : 1,
                                    }}
                                    disabled={deleting}
                                    onClick={() => handleDelete(rjId)}>
                                {deleting ? 'Deleting\u2026' : 'Delete'}
                            </button>
                        </div>
                    </div>
                </td>
            </tr>
        );
    }

    // --- Main render ---

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                    Schedule
                </h2>
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
                        {rebalancing ? '\u2026' : rebalanceFlash === 'ok' ? '\u2713 Done' : 'Spread run times'}
                    </button>
                    <span
                        title="Adjusts next-run times so jobs don't pile up in the same hour. Run once after adding or changing jobs. Does not change intervals or priorities."
                        style={{
                            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                            color: 'var(--text-tertiary)', cursor: 'help', userSelect: 'none',
                        }}>
                        {'\u24D8'}
                    </span>
                </div>
            </div>

            {runError && (
                <div style={{ padding: '0.5rem 0.75rem', background: 'var(--status-error)',
                              color: 'var(--accent-text)', borderRadius: 'var(--radius)',
                              fontSize: 'var(--type-body)', fontFamily: 'var(--font-mono)',
                              display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>{runError}</span>
                    <button onClick={() => setRunError(null)}
                            style={{ background: 'none', border: 'none', color: 'var(--accent-text)',
                                     cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)' }}>
                        {'\u2715'}
                    </button>
                </div>
            )}

            {runningJob && (
                <div class="t-frame" style={{
                    borderLeft: '3px solid var(--status-success)',
                    padding: '0.5rem 0.75rem',
                    display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap',
                }}>
                    <span style={{ color: 'var(--status-success)', fontFamily: 'var(--font-mono)',
                                   fontWeight: 700, fontSize: 'var(--type-label)',
                                   textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
                        {'\u25CF'} Running
                    </span>
                    <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)',
                                   fontSize: 'var(--type-body)' }}>
                        {runningJob.source || '\u2014'}
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

            {/* ρ traffic intensity indicator */}
            {jobs.length > 0 && (() => {
                const rho = computeRho(jobs);
                const { label, color } = rhoStatus(rho);
                return (
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: '0.5rem',
                        marginBottom: '0.4rem',
                    }}>
                        <span style={{
                            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                            color: 'var(--text-tertiary)',
                        }}>
                            24h load
                        </span>
                        <span style={{
                            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                            fontWeight: 700, color,
                            background: 'var(--bg-surface-raised)',
                            border: `1px solid ${color}`,
                            borderRadius: 'var(--radius)',
                            padding: '1px 6px',
                            letterSpacing: '0.02em',
                        }}
                            title={`Traffic intensity: ${rho.toFixed(2)}. Keep below 0.80 to avoid job queueing delays. Dense schedules (≥0.80) may cause jobs to wait for slots.`}
                            aria-label={`Traffic intensity: ${rho.toFixed(2)}, status: ${label}`}
                        >
                            {'\u03c1'} {rho.toFixed(2)} {label}
                        </span>
                    </div>
                );
            })()}

            <GanttChart jobs={jobs} tick={tick} windowHours={24} loadMapSlots={loadMap.value} />

            {jobs.length === 0 ? (
                <div class="t-frame" style={{ textAlign: 'center', padding: '2rem',
                                              color: 'var(--text-tertiary)' }}>
                    No recurring jobs. Add one via CLI:{' '}
                    <code class="data-mono">ollama-queue schedule add</code>
                </div>
            ) : (
                <>
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.5rem' }}>
                        <input
                            class="t-input"
                            type="text"
                            placeholder="Filter jobs\u2026"
                            value={search}
                            onInput={ev => setSearch(ev.target.value)}
                            style={{ width: '200px', padding: '4px 8px', fontSize: 'var(--type-body)',
                                     fontFamily: 'var(--font-mono)' }}
                        />
                        {search && (
                            <button class="t-btn t-btn-secondary"
                                    style={{ padding: '4px 8px', fontSize: 'var(--type-label)' }}
                                    onClick={() => setSearch('')}>{'\u2715'}</button>
                        )}
                    </div>
                    {visibleJobs.length === 0 && debouncedSearch && (
                        <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-body)',
                                    textAlign: 'center', padding: '1rem 0' }}>
                            No jobs match "{debouncedSearch}"
                        </p>
                    )}
                    <div class="t-frame" style={{ padding: 0, overflowX: 'auto' }}>
                        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                            <table style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse',
                                            fontSize: 'var(--type-body)' }}>
                                <thead>
                                    <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                                 background: 'var(--bg-surface-raised)' }}>
                                        {COLUMNS.map(col => (
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
                                                ...(col === 'Name' ? {
                                                    position: 'sticky', left: 0,
                                                    background: 'var(--bg-surface-raised)', zIndex: 1,
                                                } : {}),
                                            }}>{col}</th>
                                        ))}
                                    </tr>
                                </thead>
                                {groups.map(group => {
                                    const collapsed = collapsedGroups.includes(group.tag);
                                    return (
                                        <tbody key={group.tag}>
                                            {renderGroupHeader(group)}
                                            {!collapsed && group.jobs.map(rj => (
                                                <Fragment key={rj.id}>
                                                    {renderJobRow(rj)}
                                                    {expandedJobId === rj.id && renderDetailPanel(rj.id)}
                                                </Fragment>
                                            ))}
                                        </tbody>
                                    );
                                })}
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
        </div>
    );
}
