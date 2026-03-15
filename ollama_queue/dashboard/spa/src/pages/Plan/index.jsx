import { Fragment } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import {
    status, scheduleJobs, scheduleEvents, models, loadMap,
    fetchSchedule, fetchLoadMap, toggleScheduleJob, triggerRebalance, runScheduleJobNow,
    updateScheduleJob, fetchModels, batchToggleJobs, batchRunJobs,
    fetchJobRuns, deleteScheduleJob, fetchSuggestTime, enableJobByName,
    generateJobDescription,
} from '../../stores';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import { GanttChart, runStatus } from '../../components/GanttChart';
import { scheduledEvalRuns, fetchScheduledEvalRuns } from '../../stores/eval.js';
import { currentTab } from '../../stores/health.js';
import { ModelBadge } from '../../components/ModelBadge';
import LoadMapStrip from '../../components/LoadMapStrip.jsx';
import AddRecurringJobModal from '../../components/AddRecurringJobModal.jsx';
import ScheduleHistory from './ScheduleHistory.jsx';
import { ShPageBanner } from 'superhot-ui/preact';
import { TAB_CONFIG } from '../../config/tabs.js';
import {
    formatCountdown, formatInterval, parseInterval, formatDuration,
    computeRho, rhoStatus, priorityCategory, groupJobsByTag, groupNextDue,
    CATEGORY_COLORS, COLUMN_DEFS, COL_COUNT, STATUS_COLORS,
    labelStyle, inputStyle, isMobileScreen, priorityBorderWidth,
} from './helpers.js';

// What it shows: The scheduling view — the Gantt timeline of upcoming jobs, the 24h load-map
//   density strip showing which half-hour slots are already busy, and the full list of
//   recurring jobs grouped by tag with enable/disable/run-now/edit controls.
// Decision it drives: When should I add a new recurring job so it doesn't pile on top of
//   existing ones? Which recurring jobs are enabled or disabled? Is the schedule evenly
//   spread across the day, or are all jobs firing at the same time?

function useDebounce(value, delay) {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
        const timer = setTimeout(() => setDebounced(value), delay);
        return () => clearTimeout(timer);
    }, [value, delay]);
    return debounced;
}

export default function Plan() {
    const [tick, setTick] = useState(0);
    const [search, setSearch] = useState('');
    const [ganttExpanded, setGanttExpanded] = useState(false);

    // Action feedback hooks — one per distinct action type, all declared before any early returns
    const [deleteFb, deleteAct] = useActionFeedback();
    const [runNowFb, runNowAct] = useActionFeedback();
    const [pinFb, pinAct] = useActionFeedback();
    const [batchRunFb, batchRunAct] = useActionFeedback();
    const [rebalanceFb, rebalanceAct] = useActionFeedback();
    const [reenableFb, reenableAct] = useActionFeedback();
    const [saveFb, saveAct] = useActionFeedback();
    const [generateFb, generateAct] = useActionFeedback();
    const [batchToggleFb, batchToggleAct] = useActionFeedback();

    // Group collapse state (persisted in localStorage)
    const [collapsedGroups, setCollapsedGroups] = useState(() => {
        try { return JSON.parse(localStorage.getItem('schedule-collapsed') || '[]'); }
        catch { return []; }
    });

    // Detail panel
    const [expandedJobId, setExpandedJobId] = useState(null);
    const [jobRuns, setJobRuns] = useState({});
    const [editForm, setEditForm] = useState(null);
    const [batchRunningTags, setBatchRunningTags] = useState(new Set());
    const [suggestSlots, setSuggestSlots] = useState(null); // null=never fetched, []=fetched empty, [...]= results
    const [suggestLoading, setSuggestLoading] = useState(false);
    // Which job (by id) is currently generating its AI description
    const [generatingDescId, setGeneratingDescId] = useState(null);
    // Two-click delete guard: tracks which job's delete button is in "confirm?" state
    const [pendingDeleteId, setPendingDeleteId] = useState(null);

    const refreshingRef = useRef(false);
    // What it tracks: whether the user is actively interacting with the Gantt chart
    //   (hovering for slot detail, dragging the zoom slider). When true, the 10s
    //   load-map refresh is suppressed so bucket bars don't shift under the cursor.
    // Decision it drives: prevents the load-map from jumping mid-interaction while
    //   the user is targeting a specific time slot (Finding #23).
    const ganttInteractingRef = useRef(false);
    const jobRowRefs = useRef({});
    const debouncedSearch = useDebounce(search, 300);

    useEffect(() => {
        fetchSchedule();
        fetchLoadMap();
        fetchModels();
        fetchScheduledEvalRuns();
        const tickInterval = setInterval(() => setTick(t => t + 1), 1000);
        const refreshInterval = setInterval(() => {
            // Skip the load-map refresh while the user is interacting with the Gantt —
            // shifting buckets mid-hover disrupts slot targeting. Schedule data still
            // refreshes on the next cycle once interaction ends.
            if (!refreshingRef.current && !ganttInteractingRef.current) {
                refreshingRef.current = true;
                Promise.all([fetchSchedule(), fetchLoadMap(), fetchScheduledEvalRuns()])
                    .finally(() => { refreshingRef.current = false; });
            }
        }, 10000);
        return () => {
            clearInterval(tickInterval);
            clearInterval(refreshInterval);
        };
    }, []);

    useEffect(() => {
        if (!ganttExpanded) return;
        function onKey(evt) { if (evt.key === 'Escape') setGanttExpanded(false); }
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [ganttExpanded]);

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
                description: rj.description || '',
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
        if (!editForm || saveFb.phase === 'loading') return;
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
        if (editForm.description !== (rj.description || '')) updates.description = editForm.description || null;

        if (Object.keys(updates).length === 0) {
            setExpandedJobId(null);
            setEditForm(null);
            return;
        }
        await saveAct(
            'Saving\u2026',
            async () => {
                await updateScheduleJob(editForm.id, updates);
                setExpandedJobId(null);
                setEditForm(null);
            },
            'Saved'
        );
    }

    // Ask the backend to auto-generate a plain-English description for this job using Ollama.
    // The backend starts a background thread and returns immediately; description arrives via
    // the next 10s schedule refresh (or sooner if the model responds quickly).
    async function handleGenerateDescription(rjId) {
        setGeneratingDescId(rjId);
        await generateAct(
            'Generating description\u2026',
            async () => {
                await generateJobDescription(rjId);
                await fetchSchedule(); // refresh now; background thread may already be done
            },
            'Queued \u2014 description arriving shortly'
        );
        setGeneratingDescId(null);
    }

    // First click sets pendingDeleteId; second click (on confirm button) executes the delete.
    // Matches the two-click inline delete pattern used in VariantRow (no window.confirm).
    function handleDeleteRequest(rjId) {
        setPendingDeleteId(rjId);
    }

    async function handleDeleteConfirm(rjId) {
        setPendingDeleteId(null);
        await deleteAct(
            'Deleting\u2026',
            async () => {
                await deleteScheduleJob(rjId);
                setExpandedJobId(null);
                setEditForm(null);
            },
            'Deleted'
        );
    }

    function handleDeleteCancel() {
        setPendingDeleteId(null);
    }

    async function handleRunNow(rj) {
        if (rj.estimated_duration > 300) {
            const ok = window.confirm(`Run "${rj.name}" now? Estimated duration: ~${Math.round(rj.estimated_duration / 60)}m`);
            if (!ok) return;
        }
        await runNowAct(
            `Triggering ${rj.name}\u2026`,
            async () => {
                await runScheduleJobNow(rj.id);
            },
            `${rj.name} triggered`
        );
    }

    async function handlePinToggle(rj) {
        await pinAct(
            rj.pinned ? 'Unpinning\u2026' : 'Pinning\u2026',
            async () => {
                await updateScheduleJob(rj.id, { pinned: !rj.pinned });
            },
            rj.pinned ? 'Unpinned' : 'Pinned'
        );
    }

    async function handleBatchRun(tag) {
        setBatchRunningTags(prev => new Set([...prev, tag]));
        await batchRunAct(
            `Running all ${tag} jobs\u2026`,
            async () => {
                await batchRunJobs(tag);
            },
            `All ${tag} jobs triggered`
        );
        setBatchRunningTags(prev => {
            const next = new Set(prev);
            next.delete(tag);
            return next;
        });
    }

    async function handleBatchToggle(tag, enabled) {
        await batchToggleAct(
            enabled ? `Enabling ${tag}\u2026` : `Disabling ${tag}\u2026`,
            async () => {
                await batchToggleJobs(tag, enabled);
                await fetchSchedule();
            },
            enabled ? `${tag} jobs enabled` : `${tag} jobs disabled`
        );
    }

    async function handleRebalance() {
        await rebalanceAct(
            'Rebalancing\u2026',
            async () => {
                await triggerRebalance();
                await fetchSchedule();
            },
            'Schedule rebalanced'
        );
    }

    async function handleReenableJob(name) {
        await reenableAct(
            `Re-enabling ${name}\u2026`,
            async () => {
                await enableJobByName(name);
                await fetchSchedule();
            },
            `${name} re-enabled`
        );
    }

    function handleScrollToJob(rjId) {
        const el = jobRowRefs.current[rjId];
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            el.style.outline = '2px solid var(--accent)';
            setTimeout(() => { if (el) el.style.outline = ''; }, 1500);
        }
        if (expandedJobId !== rjId) toggleJobDetail(rjId);
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

    // What it shows: Scheduled eval runs as Gantt bars alongside regular recurring jobs.
    // Decision it drives: User can see at a glance when an eval will run so they can avoid
    //   scheduling conflicting heavy jobs at the same time.
    const evalBlocks = (scheduledEvalRuns.value || [])
        .filter(run => run.scheduled_for != null)
        .map(run => ({
        id: `eval-${run.run_id}`,
        name: `Eval: ${(run.variant_ids || []).slice(0, 3).join(',')}`,
        source: 'eval',
        next_run: run.scheduled_for,
        estimated_duration: run.estimated_duration || 600,
        enabled: true,
        // Eval blocks are read-only pseudo-jobs — no recurring-job fields needed.
        _isEval: true,
        onClick: () => { currentTab.value = 'eval'; },
    }));
    const ganttJobs = [...jobs, ...evalBlocks];

    const visibleJobs = debouncedSearch
        ? jobs.filter(rj => rj.name.toLowerCase().includes(debouncedSearch.toLowerCase()))
        : jobs;

    const groups = groupJobsByTag(visibleJobs);

    const lateJobs = jobs.filter(rj =>
        rj.enabled && runStatus(rj.last_run, rj.interval_seconds).label === 'running behind'
    );

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
                            <div>
                                <button
                                    class="t-btn t-btn-secondary"
                                    style={{ fontSize: 'var(--type-label)', padding: '0.15rem 0.5rem',
                                             opacity: (isBatchRunning || batchRunFb.phase === 'loading') ? 0.5 : 1 }}
                                    disabled={isBatchRunning || batchRunFb.phase === 'loading'}
                                    onClick={() => handleBatchRun(tag)}>
                                    {(isBatchRunning || batchRunFb.phase === 'loading') ? '\u2026' : '\u25B6 Run All'}
                                </button>
                                {batchRunFb.msg && <div class={`action-fb action-fb--${batchRunFb.phase}`}>{batchRunFb.msg}</div>}
                            </div>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem',
                                            fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)',
                                            color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                <input type="checkbox" checked={allEnabled}
                                       style={{ accentColor: 'var(--accent)', width: 14, height: 14 }}
                                       onChange={() => handleBatchToggle(tag, !allEnabled)} />
                                All
                            </label>
                            {batchToggleFb.msg && <div class={`action-fb action-fb--${batchToggleFb.phase}`}>{batchToggleFb.msg}</div>}
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
        const isExpanded = expandedJobId === rj.id;

        return (
            <tr key={rj.id}
                ref={el => { if (el) jobRowRefs.current[rj.id] = el; else delete jobRowRefs.current[rj.id]; }}
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
                    borderLeft: `${priorityBorderWidth(rj.priority)} solid ${color}`,
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
                    <div>
                        <button
                            title={rj.pinned ? 'Locked \u2014 click to unlock this time slot' : 'Lock this time slot so the scheduler won\'t move it when you rebalance'}
                            disabled={pinFb.phase === 'loading'}
                            onClick={() => handlePinToggle(rj)}
                            style={{
                                background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.1rem',
                                color: rj.pinned ? 'var(--status-warning)' : 'var(--text-tertiary)',
                                opacity: (rj.pinned || pinFb.phase === 'loading') ? 1 : 0.4,
                            }}>
                            {'\u2605'}
                        </button>
                        {pinFb.msg && <div class={`action-fb action-fb--${pinFb.phase}`}>{pinFb.msg}</div>}
                    </div>
                </td>
                <td style={{ textAlign: 'center' }}>
                    {rj.outcome_reason && !rj.enabled ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', alignItems: 'center' }}>
                            <span class="t-status t-status-warning" style={{ fontSize: '9px', whiteSpace: 'normal', maxWidth: '8rem' }}>
                                {rj.outcome_reason}
                            </span>
                            <button
                                disabled={reenableFb.phase === 'loading'}
                                onClick={() => handleReenableJob(rj.name)}
                                style={{
                                    fontFamily: 'var(--font-mono)', fontSize: '9px',
                                    background: 'transparent', border: '1px solid var(--status-warning)',
                                    color: 'var(--status-warning)', padding: '0.1rem 0.3rem',
                                    borderRadius: 'var(--radius)', cursor: 'pointer',
                                    opacity: reenableFb.phase === 'loading' ? 0.5 : 1,
                                }}
                            >
                                {reenableFb.phase === 'loading' ? '\u2026' : 'Re-enable'}
                            </button>
                            {reenableFb.msg && <div class={`action-fb action-fb--${reenableFb.phase}`}>{reenableFb.msg}</div>}
                        </div>
                    ) : (
                        <input type="checkbox" checked={!!rj.enabled}
                               style={{ accentColor: 'var(--accent)', width: 16, height: 16 }}
                               onChange={ev => toggleScheduleJob(rj.id, ev.target.checked)} />
                    )}
                </td>
                <td style={{ textAlign: 'center', padding: '0.25rem 0.5rem' }}>
                    <div>
                        <button
                            class="t-btn t-btn-secondary"
                            style={{ fontSize: 'var(--type-label)', padding: '0.2rem 0.6rem',
                                     opacity: runNowFb.phase === 'loading' ? 0.5 : 1 }}
                            disabled={runNowFb.phase === 'loading'}
                            onClick={() => handleRunNow(rj)}>
                            {runNowFb.phase === 'loading' ? '\u2026' : '\u25B6'}
                        </button>
                        {runNowFb.msg && <div class={`action-fb action-fb--${runNowFb.phase}`}>{runNowFb.msg}</div>}
                    </div>
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
                        {/* What it does — plain-English description, auto-generated by local AI or manually edited */}
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.35rem' }}>
                                <label style={labelStyle}>What it does</label>
                                <button
                                    class="t-btn"
                                    style={{
                                        fontSize: 'var(--type-micro)', padding: '0.1rem 0.5rem',
                                        background: 'none', border: '1px solid var(--border-subtle)',
                                        color: 'var(--text-tertiary)', borderRadius: '3px',
                                        cursor: generatingDescId === rj.id ? 'default' : 'pointer',
                                        lineHeight: 1.4, fontFamily: 'var(--font-mono)',
                                        opacity: generatingDescId === rj.id ? 0.5 : 1,
                                    }}
                                    title="Ask a local AI to write a plain-English description of what this job does (~10 seconds)"
                                    onClick={() => handleGenerateDescription(rj.id)}
                                    disabled={generatingDescId === rj.id}
                                >
                                    {generatingDescId === rj.id ? '\u2026' : '\u21BB'}
                                </button>
                                {generatingDescId === rj.id && (
                                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                                                   color: 'var(--text-tertiary)' }}>
                                        generating\u2026
                                    </span>
                                )}
                                {generateFb.msg && !generatingDescId && (
                                    <div class={`action-fb action-fb--${generateFb.phase}`}>{generateFb.msg}</div>
                                )}
                            </div>
                            <textarea
                                class="t-input"
                                value={editForm.description}
                                onInput={ev => setEditForm(prev => ({ ...prev, description: ev.target.value }))}
                                placeholder={generatingDescId === rj.id
                                    ? 'Asking AI\u2026'
                                    : 'Click \u21BB to auto-generate, or type a description here'}
                                rows={3}
                                style={{
                                    width: '100%', resize: 'vertical',
                                    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
                                    color: 'var(--text-primary)', lineHeight: 1.6,
                                    background: 'var(--bg-surface-raised)', boxSizing: 'border-box',
                                    padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)',
                                    border: '1px solid var(--border-subtle)',
                                }}
                            />
                        </div>

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
                                    {editForm.cron_expression ? 'Cron schedule' : 'Repeats every'}
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
                                <label style={labelStyle}>Priority (1=highest, 10=lowest)</label>
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
                                <label style={labelStyle}>Max run time</label>
                                <input class="t-input" type="text" value={editForm.timeout}
                                       onInput={ev => setEditForm(prev => ({ ...prev, timeout: ev.target.value }))}
                                       placeholder="e.g. 10m, 1h"
                                       style={inputStyle} />
                            </div>
                            <div>
                                <label style={labelStyle}>Retry attempts if it fails</label>
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
                                    Lock this time slot
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
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                            <div>
                                <button class="t-btn t-btn-primary"
                                        style={{ padding: '0.3rem 1rem', fontSize: 'var(--type-body)',
                                                 opacity: saveFb.phase === 'loading' ? 0.6 : 1 }}
                                        disabled={saveFb.phase === 'loading'}
                                        onClick={handleDetailSave}>
                                    {saveFb.phase === 'loading' ? 'Saving\u2026' : 'Save'}
                                </button>
                                {saveFb.msg && <div class={`action-fb action-fb--${saveFb.phase}`}>{saveFb.msg}</div>}
                            </div>
                            <button class="t-btn t-btn-secondary"
                                    style={{ padding: '0.3rem 0.75rem', fontSize: 'var(--type-body)' }}
                                    onClick={() => { setExpandedJobId(null); setEditForm(null); }}>
                                Cancel
                            </button>
                            <div style={{ flex: 1 }} />
                            <div>
                                <button class="t-btn t-btn-secondary"
                                        style={{ padding: '0.3rem 0.75rem', fontSize: 'var(--type-body)',
                                                 opacity: runNowFb.phase === 'loading' ? 0.5 : 1 }}
                                        disabled={runNowFb.phase === 'loading'}
                                        onClick={() => handleRunNow(rj)}>
                                    {runNowFb.phase === 'loading' ? '\u2026' : '\u25B6 Run Now'}
                                </button>
                                {runNowFb.msg && <div class={`action-fb action-fb--${runNowFb.phase}`}>{runNowFb.msg}</div>}
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                                {pendingDeleteId !== rjId ? (
                                    <button class="t-btn"
                                            style={{
                                                padding: '0.3rem 0.75rem', fontSize: 'var(--type-body)',
                                                color: 'var(--status-error)', border: '1px solid var(--status-error)',
                                                background: 'transparent', opacity: deleteFb.phase === 'loading' ? 0.6 : 1,
                                            }}
                                            disabled={deleteFb.phase === 'loading'}
                                            onClick={() => handleDeleteRequest(rjId)}>
                                        {deleteFb.phase === 'loading' ? 'Deleting\u2026' : 'Delete'}
                                    </button>
                                ) : (
                                    <>
                                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)' }}>
                                            Delete "{jobs.find(j => j.id === rjId)?.name}"?
                                        </span>
                                        <button class="t-btn"
                                                style={{
                                                    padding: '0.2rem 0.6rem', fontSize: 'var(--type-label)',
                                                    color: 'var(--status-error)', borderColor: 'var(--status-error)',
                                                }}
                                                disabled={deleteFb.phase === 'loading'}
                                                onClick={() => handleDeleteConfirm(rjId)}>
                                            {deleteFb.phase === 'loading' ? 'Deleting\u2026' : 'Yes, delete'}
                                        </button>
                                        <button class="t-btn t-btn-secondary"
                                                style={{ padding: '0.2rem 0.6rem', fontSize: 'var(--type-label)' }}
                                                onClick={handleDeleteCancel}>
                                            Cancel
                                        </button>
                                    </>
                                )}
                                {deleteFb.msg && <div class={`action-fb action-fb--${deleteFb.phase}`}>{deleteFb.msg}</div>}
                            </div>
                        </div>
                    </div>
                </td>
            </tr>
        );
    }

    // --- Main render ---

    return (
        <div class="flex flex-col gap-4 animate-page-enter" data-mood="wonder">
            <PageBanner title="Schedule" subtitle="recurring jobs and upcoming run times" />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <AddRecurringJobModal onAdded={() => { fetchSchedule(); fetchLoadMap(); }} />
                    <div>
                        <button
                            class="t-btn t-btn-primary px-4 py-2 text-sm"
                            onClick={handleRebalance}
                            disabled={rebalanceFb.phase === 'loading'}
                            style={{
                                opacity: rebalanceFb.phase === 'loading' ? 0.6 : 1,
                                background: rebalanceFb.phase === 'success' ? 'var(--status-success)' : undefined,
                                transition: 'background 0.3s ease',
                            }}>
                            {rebalanceFb.phase === 'loading' ? '\u2026' : 'Spread run times'}
                        </button>
                        {rebalanceFb.msg && <div class={`action-fb action-fb--${rebalanceFb.phase}`}>{rebalanceFb.msg}</div>}
                    </div>
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


            {runningJob && (
                <div class="t-frame" style={{
                    borderLeft: '3px solid var(--status-success)',
                    padding: '0.5rem 0.75rem',
                    display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap',
                }}>
                    <span style={{ color: 'var(--status-success)', fontFamily: 'var(--font-mono)',
                                   fontWeight: 700, fontSize: 'var(--type-label)',
                                   textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
                        {'\u25CF'} Running now
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

            {/* ρ traffic intensity — visual bar shows daily load vs 0.80 warn threshold */}
            {/* What it shows: How full the day's schedule is (0=empty, 1=non-stop). */}
            {/* Decision: Keep below 0.80 — above that queue wait times grow sharply (Kingman's formula). */}
            {jobs.length > 0 && (() => {
                const rho = computeRho(jobs);
                const { label, color } = rhoStatus(rho);
                const fillPct = Math.min(rho, 1) * 100;
                return (
                    <div style={{ marginBottom: '0.4rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.3rem' }}>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                                Daily load
                            </span>
                            <div style={{
                                position: 'relative', flex: 1, height: 8,
                                background: 'var(--bg-inset)',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: 'var(--radius)',
                                minWidth: 80,
                            }}>
                                <div style={{
                                    position: 'absolute', left: 0, top: 0, bottom: 0,
                                    width: `${fillPct}%`,
                                    background: color,
                                    borderRadius: 'var(--radius)',
                                    transition: 'width 0.4s ease, background 0.3s ease',
                                }} />
                                <div
                                    aria-hidden="true"
                                    title="Warning threshold — keep below 0.80 to avoid job pile-up"
                                    style={{
                                        position: 'absolute', left: '80%', top: -3, bottom: -3,
                                        width: 1, borderLeft: '1px dashed var(--status-warning)', zIndex: 2,
                                    }}
                                />
                                <span style={{
                                    position: 'absolute', left: '80%', top: -16,
                                    transform: 'translateX(-50%)',
                                    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                                    color: 'var(--status-warning)', whiteSpace: 'nowrap', pointerEvents: 'none',
                                }}>0.80</span>
                            </div>
                            <span
                                style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', fontWeight: 700, color, whiteSpace: 'nowrap' }}
                                title={`How packed is your daily schedule? 0.0 = nothing scheduled, 1.0 = queue running non-stop. Keep below 0.80 to avoid jobs piling up and waiting for each other. Current: ${rho.toFixed(2)}`}
                                aria-label={`Traffic intensity: ${rho.toFixed(2)}, status: ${label}`}
                            >
                                {rho.toFixed(2)} — {label}
                            </span>
                            <button
                                class="t-btn t-btn--ghost"
                                style={{ fontSize: 'var(--type-label)', padding: '1px 8px', whiteSpace: 'nowrap' }}
                                disabled={suggestLoading}
                                onClick={async () => {
                                    if (suggestSlots !== null) { setSuggestSlots(null); return; }
                                    setSuggestLoading(true);
                                    try {
                                        const data = await fetchSuggestTime(5, 3);
                                        setSuggestSlots(data.suggestions || []);
                                    } catch (e) {
                                        console.error('fetchSuggestTime failed:', e);
                                    } finally {
                                        setSuggestLoading(false);
                                    }
                                }}
                                title="Find the best time windows to add a new recurring job — highlights the quietest slots on the chart above"
                            >
                                {suggestLoading ? '\u2026' : suggestSlots === null ? 'Find best slot' : suggestSlots.length === 0 ? 'No open slots found' : 'Clear suggestions'}
                            </button>
                        </div>
                    </div>
                );
            })()}

            {/* Health summary strip — one-line status count of the entire schedule.
                What it shows: active · failing · disabled · overdue job counts at a glance.
                Decision: spot a systemic problem (e.g., 8 disabled jobs) before scrolling the table. */}
            {jobs.length > 0 && (() => {
                const activeCount = jobs.filter(rj => rj.enabled).length;
                const failingCount = jobs.filter(rj => rj.enabled && rj.last_exit_code != null && rj.last_exit_code !== 0).length;
                const disabledCount = jobs.filter(rj => !rj.enabled).length;
                const overdueCount = jobs.filter(rj => rj.enabled && rj.next_run < Date.now() / 1000).length;
                const skipCount = jobs.reduce((sum, rj) => sum + (rj.skip_count_24h || 0), 0);
                return (
                    <div style={{
                        display: 'flex', flexWrap: 'wrap', gap: '0.25rem 1rem',
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                        color: 'var(--text-tertiary)', alignItems: 'center',
                        padding: '0.2rem 0',
                    }}>
                        <span title="Recurring jobs that will run on schedule">
                            <span style={{ color: 'var(--status-healthy)', fontWeight: 600 }}>{activeCount}</span> active
                        </span>
                        {failingCount > 0 && (
                            <span title="Enabled jobs whose last run exited non-zero">
                                <span style={{ color: 'var(--status-error)', fontWeight: 600 }}>{failingCount}</span> failing
                            </span>
                        )}
                        {disabledCount > 0 && (
                            <span title="Jobs that have been disabled (manually or automatically)">
                                <span style={{ color: 'var(--status-warning)', fontWeight: 600 }}>{disabledCount}</span> disabled
                            </span>
                        )}
                        {overdueCount > 0 && (
                            <span title="Enabled jobs whose next_run timestamp has passed">
                                <span style={{ color: '#f97316', fontWeight: 600 }}>{overdueCount}</span> overdue
                            </span>
                        )}
                        {skipCount > 0 && (
                            <span title="Total number of times any job was skipped in the last 24h because a previous run hadn't finished">
                                <span style={{ color: '#f97316', fontWeight: 600 }}>↻ {skipCount}</span> skip{skipCount !== 1 ? 's' : ''} today
                            </span>
                        )}
                    </div>
                );
            })()}

            {/* Load map density strip — 48-slot daily load visualization.
                onMouseEnter/Leave set ganttInteractingRef so the 10s background
                refresh doesn't shift bars while the user is hovering for slot details. */}
            <div
                onMouseEnter={() => { ganttInteractingRef.current = true; }}
                onMouseLeave={() => { ganttInteractingRef.current = false; }}
            >
                <LoadMapStrip data={loadMap.value} />
            </div>

            {/* Gantt timeline — tap/click bars for details; expands to full screen on mobile */}
            <div class="t-frame" data-label="Next 24 hours"
                onMouseEnter={() => { ganttInteractingRef.current = true; }}
                onMouseLeave={() => { ganttInteractingRef.current = false; }}
            >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                    <p style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', lineHeight: 1.5, flex: 1 }}>
                        Each bar is a scheduled job. Bar width shows how long it&apos;s expected to run.
                        Color shows which program runs it. Tap or hover any bar for details.
                    </p>
                    <button
                        title="Expand to full screen — on mobile this shows a 6-hour window with wider bars"
                        onClick={() => setGanttExpanded(true)}
                        style={{
                            background: 'none', border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius)', cursor: 'pointer',
                            color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)',
                            fontSize: 'var(--type-label)', padding: '2px 7px', marginLeft: '0.5rem', flexShrink: 0,
                        }}
                    >{'\u2922'}</button>
                </div>
                <GanttChart
                    jobs={ganttJobs}
                    tick={tick}
                    windowHours={24}
                    loadMapSlots={loadMap.value?.slots || []}
                    suggestSlots={suggestSlots || []}
                    onRunJob={id => { const rj = jobs.find(j => j.id === id); if (rj) handleRunNow(rj); }}
                    onScrollToJob={handleScrollToJob}
                />
            </div>

            {ganttExpanded && (
                <div
                    style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'var(--bg-base)', overflowY: 'auto', padding: '1rem' }}
                    onClick={evt => { if (evt.target === evt.currentTarget) setGanttExpanded(false); }}
                    onMouseEnter={() => { ganttInteractingRef.current = true; }}
                    onMouseLeave={() => { ganttInteractingRef.current = false; }}
                >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                            Schedule {isMobileScreen() ? '(next 6h)' : '(next 24h)'}
                        </span>
                        <button
                            onClick={() => setGanttExpanded(false)}
                            style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)', cursor: 'pointer', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', padding: '3px 10px' }}
                        >{'\u2715'} close</button>
                    </div>
                    <GanttChart
                        jobs={ganttJobs}
                        tick={tick}
                        windowHours={isMobileScreen() ? 6 : 24}
                        loadMapSlots={loadMap.value?.slots || []}
                        suggestSlots={suggestSlots || []}
                        onRunJob={id => { const rj = jobs.find(j => j.id === id); if (rj) handleRunNow(rj); }}
                        onScrollToJob={id => { setGanttExpanded(false); handleScrollToJob(id); }}
                    />
                </div>
            )}

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
                    {lateJobs.length > 0 && (
                        <div style={{
                            display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap',
                            padding: '0.4rem 0.75rem',
                            background: 'rgba(251,146,60,0.08)',
                            border: '1px solid var(--status-warning)',
                            borderRadius: 'var(--radius)',
                            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                            marginBottom: '0.25rem',
                        }}>
                            <span style={{ color: 'var(--status-warning)', fontWeight: 700, whiteSpace: 'nowrap' }}>
                                {'\u26A0'} {lateJobs.length} job{lateJobs.length > 1 ? 's' : ''} running behind schedule {'\u2014'}
                            </span>
                            {lateJobs.map((rj, idx) => (
                                <span key={rj.id}>
                                    <button
                                        onClick={() => handleScrollToJob(rj.id)}
                                        style={{
                                            background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                                            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                            color: 'var(--accent)', textDecoration: 'underline',
                                        }}
                                    >{rj.name}</button>
                                    {idx < lateJobs.length - 1 ? ', ' : ''}
                                </span>
                            ))}
                        </div>
                    )}
                    <div class="t-frame" style={{ padding: 0, overflowX: 'auto' }}>
                        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                            <table style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse',
                                            fontSize: 'var(--type-body)' }}>
                                <thead>
                                    <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                                 background: 'var(--bg-surface-raised)' }}>
                                        {COLUMN_DEFS.map(({ label, title }) => (
                                            <th key={label || 'actions'} title={title} style={{
                                                textAlign: label === 'Name' ? 'left' : 'center',
                                                padding: '0.5rem 0.75rem',
                                                fontSize: 'var(--type-label)',
                                                color: 'var(--text-secondary)',
                                                fontWeight: 600,
                                                textTransform: 'uppercase',
                                                letterSpacing: '0.05em',
                                                fontFamily: 'var(--font-mono)',
                                                whiteSpace: 'nowrap',
                                                cursor: title ? 'help' : undefined,
                                                ...(label === 'Name' ? {
                                                    position: 'sticky', left: 0,
                                                    background: 'var(--bg-surface-raised)', zIndex: 1,
                                                } : {}),
                                            }}>{label}</th>
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

            <ScheduleHistory events={events} />
        </div>
    );
}
