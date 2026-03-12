import { h, Fragment } from 'preact';
import { useState } from 'preact/hooks';
import { useSignal } from '@preact/signals';
import { fetchJobRuns } from '../stores';

// NOTE: all .map() callbacks use descriptive names (job, slot, laneIdx) — never 'h'
// as that shadows the JSX factory esbuild injects.

// --- Pure helpers (exported for testing) ---

export const SOURCE_COLORS = {
    aria:     'var(--accent)',
    telegram: '#f97316',
    notion:   '#a78bfa',
};

export function sourceColor(source) {
    if (!source || source === 'none') return 'var(--text-tertiary)';
    const s = source.toLowerCase();
    if (s === 'aria' || s.startsWith('aria-')) return 'var(--accent)';
    if (s === 'telegram' || s.startsWith('telegram-')) return '#f97316';
    if (s === 'notion' || s.startsWith('notion-')) return '#a78bfa';
    return 'var(--text-tertiary)';
}

export function formatDuration(seconds) {
    if (seconds == null) return '~10m';
    const s = Math.floor(seconds);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return rem === 0 ? `${m}m` : `${m}m ${rem}s`;
}

export function assignLanes(jobs) {
    const sorted = [...jobs].sort((a, b) => a.next_run - b.next_run);
    const laneEnds = [];
    return sorted.map(job => {
        const start = job.next_run;
        const end = start + (job.estimated_duration || 600);
        let laneIdx = laneEnds.findIndex(laneEnd => laneEnd <= start);
        if (laneIdx === -1) laneIdx = laneEnds.length;
        laneEnds[laneIdx] = end;
        return { ...job, _lane: laneIdx, _end: end };
    });
}

export function buildTooltip(job, isConcurrent) {
    const nextRunStr = new Date(job.next_run * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const lastRunStr = job.last_run
        ? new Date(job.last_run * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : 'never';
    const modelStr = job.model || job.model_profile || 'ollama';
    const parts = [];
    // Disabled status shown first — most important state for a paused job
    if (!job.enabled) {
        parts.push(job.outcome_reason
            ? `⏸ disabled: ${job.outcome_reason}`
            : '⏸ disabled');
    }
    // Plain-English description first — the most useful context line
    if (job.description) parts.push(job.description);
    parts.push(
        `${job.name}`,
        `program: ${job.source || 'unknown'} · model: ${modelStr}`,
        `expected run time: ${formatDuration(job.estimated_duration)} · starts at: ${nextRunStr}`,
        `last ran: ${lastRunStr}`,
    );
    // Truncated command — lets the user verify what's actually executing
    if (job.command) {
        const cmd = job.command.length > 70 ? `${job.command.slice(0, 67)}…` : job.command;
        parts.push(`runs: ${cmd}`);
    }
    // Skip rate: high count = job regularly overruns its interval (next run fires before current finishes)
    if (job.skip_count_24h > 0) {
        parts.push(`↻ skipped ${job.skip_count_24h}× in last 24h — runs longer than its interval`);
    }
    if (isConcurrent) parts.push('⟡ runs at the same time as another job');
    return parts.join('\n');
}

// 30-min bucket duration matches the backend's 48-slot load_map contract.
// Derived from windowSecs so non-24h views stay coherent.
const DENSITY_BUCKET_SECS = 1800;

export function buildDensityBuckets(jobs, now, windowSecs) {
    const bucketCount = Math.round(windowSecs / DENSITY_BUCKET_SECS);
    const bucketSecs = windowSecs / bucketCount;
    const buckets = Array(bucketCount).fill(0);
    for (const job of jobs) {
        const jobStart = job.next_run;
        const jobEnd = jobStart + (job.estimated_duration || 600);
        for (let i = 0; i < bucketCount; i++) {
            const bucketStart = now + i * bucketSecs;
            const bucketEnd = bucketStart + bucketSecs;
            if (jobStart < bucketEnd && jobEnd > bucketStart) {
                buckets[i]++;
            }
        }
    }
    return buckets;
}

export function buildBucketJobIds(jobs, now, windowSecs, bucketCount) {
    const bucketSecs = windowSecs / bucketCount;
    return Array.from({ length: bucketCount }, (_, i) => {
        const bucketStart = now + i * bucketSecs;
        const bucketEnd = bucketStart + bucketSecs;
        const ids = new Set();
        for (const job of jobs) {
            const jobEnd = job.next_run + (job.estimated_duration || 600);
            if (job.next_run < bucketEnd && jobEnd > bucketStart) ids.add(job.id);
        }
        return ids;
    });
}

export function getConflictingPairs(jobs) {
    const pairs = [];
    for (let i = 0; i < jobs.length; i++) {
        for (let j = i + 1; j < jobs.length; j++) {
            const a = jobs[i], b = jobs[j];
            const aEnd = a.next_run + (a.estimated_duration || 600);
            const bEnd = b.next_run + (b.estimated_duration || 600);
            if (a.next_run < bEnd && b.next_run < aEnd) {
                pairs.push([a, b]);
            }
        }
    }
    return pairs;
}

export function findHeavyConflicts(jobs) {
    const heavy = jobs.filter(j => j.model_profile === 'heavy');
    const conflictIds = new Set();
    for (const [a, b] of getConflictingPairs(heavy)) {
        conflictIds.add(a.id);
        conflictIds.add(b.id);
    }
    return conflictIds;
}

// Timing-based schedule health (kept for backward compat and lateJobs in Plan page).
export function runStatus(lastRun, intervalSeconds, _now = Date.now() / 1000) {
    if (!lastRun) return { label: 'never run yet', color: 'var(--text-tertiary)' };
    const elapsed = _now - lastRun;
    const interval = intervalSeconds || 3600;
    const drift = elapsed - interval;
    const threshold = interval * 0.05;
    if (drift <= threshold) return { label: 'running on schedule', color: 'var(--status-healthy)' };
    return { label: 'running behind', color: 'var(--status-warning)' };
}

// Outcome dot: uses the actual exit code from the last run, not timing heuristics.
// Drives: the small dot on each Gantt bar shows real pass/fail at a glance.
export function lastRunOutcome(lastExitCode, lastRun) {
    if (!lastRun) return { label: 'never run', color: 'var(--text-tertiary)' };
    if (lastExitCode === 0) return { label: 'last run succeeded', color: 'var(--status-healthy)' };
    if (lastExitCode != null) return { label: `last run failed (exit ${lastExitCode})`, color: 'var(--status-error)' };
    return { label: 'last run outcome unknown', color: 'var(--text-secondary)' };
}

// Unload hold: seconds Ollama keeps the model warm in VRAM after a job completes.
// This is shown as a right wick on the candlestick bar.
const UNLOAD_HOLD_SECS = 30;

// Score at which a slot is considered pinned/blocked by the scheduler.
const LOAD_MAP_PIN_SCORE = 999;

// Rotate loadMapSlots (48-element, midnight-anchored) so index 0 = now.
// Matches backend _time_to_slot() which uses local wall-clock time.
export function alignLoadMapToNow(slots, nowUnixSec) {
    if (!slots || slots.length === 0) return [];
    const nowDate = new Date(nowUnixSec * 1000);
    const secondsInDay = nowDate.getHours() * 3600 + nowDate.getMinutes() * 60 + nowDate.getSeconds();
    const nowSlot = Math.floor(secondsInDay / DENSITY_BUCKET_SECS);
    const n = slots.length;
    return Array.from({ length: n }, (_, i) => slots[(nowSlot + i) % n]);
}

// Color a load_map score for the density strip.
// Pinned slots get amber; scored slots scale blue opacity; empty = inset.
export function loadMapSlotColor(score) {
    if (score >= LOAD_MAP_PIN_SCORE) return 'rgba(251,146,60,0.85)'; // amber — pinned/blocked
    if (score <= 0) return 'var(--bg-inset)';
    const intensity = Math.min(score / 10, 1); // score range 0–10 for non-pinned
    const opacity = 0.20 + intensity * 0.70;   // 0.20 → 0.90
    return `rgba(99,179,237,${opacity.toFixed(2)})`;
}

function _relativeTime(ts) {
    if (!ts) return '—';
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return `${diff}s`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
    return `${Math.floor(diff / 86400)}d`;
}

function _fmtInterval(seconds) {
    if (!seconds) return '—';
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
}

// What it shows: Full details for a tapped/clicked Gantt bar — name, description, program,
//   model, start time, last run, history dots (last 5 runs), and action buttons.
// Decision it drives: User can see everything about a job and trigger it without leaving
//   the schedule view. Works on touch screens where title tooltips don't work.
function BarDetailCard({ job, runs, runsLoading, onClose, onRunJob, onScrollToJob }) {
    const { label: runLabel, color: runColor } = lastRunOutcome(job.last_exit_code, job.last_run);
    const startStr = new Date(job.next_run * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const lastRunStr = job.last_run ? `${_relativeTime(job.last_run)} ago` : 'never';
    const modelStr = job.model || job.model_profile || 'default';
    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 640;

    const cardStyle = isMobile ? {
        position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 100,
        background: 'var(--bg-surface-raised)',
        borderTop: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius) var(--radius) 0 0',
        padding: '1rem',
        boxShadow: '0 -4px 24px rgba(0,0,0,0.4)',
        animation: 'slideUp 0.15s ease-out',
    } : {
        position: 'absolute', zIndex: 50,
        bottom: '110%', left: '50%', transform: 'translateX(-50%)',
        background: 'var(--bg-surface-raised)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius)',
        padding: '0.75rem 1rem',
        minWidth: 260, maxWidth: 320,
        boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
        whiteSpace: 'normal',
    };

    return (
        <div style={cardStyle} onClick={evt => evt.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.4rem' }}>
                <div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 'var(--type-body)', color: 'var(--text-primary)' }}>
                        {job.name}
                    </div>
                    {job.description && (
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', color: 'var(--text-secondary)', marginTop: '0.15rem', lineHeight: 1.4 }}>
                            {job.description}
                        </div>
                    )}
                </div>
                <span style={{ fontSize: 'var(--type-micro)', color: runColor, whiteSpace: 'nowrap', marginLeft: '0.5rem' }}>
                    {runLabel} ●
                </span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.2rem 1rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', marginBottom: '0.4rem' }}>
                {[
                    ['program', job.source || '—'],
                    ['model', modelStr],
                    ['starts', startStr],
                    ['runs', `~${formatDuration(job.estimated_duration)}`],
                    // Candlestick segment breakdown — shows load/run/unload phases when model warmup applies
                    ...(job.warmup_estimate > 0 ? [
                        ['load', `~${job.warmup_estimate}s`],
                        ['run', `~${formatDuration(Math.max(1, (job.estimated_duration || 600) - job.warmup_estimate))}`],
                        ['unload', '~30s'],
                    ] : []),
                    ['last ran', lastRunStr],
                    ['interval', _fmtInterval(job.interval_seconds)],
                    ...(job.skip_count_24h > 0 ? [['skips today', `↻ ${job.skip_count_24h}×`]] : []),
                ].map(([k, v]) => (
                    <Fragment key={k}>
                        <span style={{ color: 'var(--text-tertiary)' }}>{k}</span>
                        <span style={{ color: 'var(--text-primary)' }}>{v}</span>
                    </Fragment>
                ))}
            </div>
            {job.model_profile === 'heavy' && (
                <div style={{ fontSize: 'var(--type-micro)', color: 'var(--status-warning)', marginBottom: '0.35rem', fontFamily: 'var(--font-mono)' }}>
                    ⚠ large model — needs ≥16GB VRAM
                </div>
            )}
            {/* Disabled reason — explains why the job was auto-disabled and how to re-enable */}
            {!job.enabled && (
                <div style={{ fontSize: 'var(--type-micro)', color: 'var(--status-warning)', marginBottom: '0.35rem', fontFamily: 'var(--font-mono)', lineHeight: 1.4 }}>
                    ⏸ {job.outcome_reason || 'disabled'} — re-enable from the Schedule table
                </div>
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)' }}>
                <span>history</span>
                {runsLoading ? <span>…</span> : (runs && runs.length > 0) ? (
                    runs.slice(0, 5).map((run, idx) => (
                        <span key={idx} title={run.status} style={{ color: run.status === 'completed' ? 'var(--status-healthy)' : 'var(--status-error)' }}>
                            {run.status === 'completed' ? '✓' : '✗'}
                        </span>
                    ))
                ) : <span>no history yet</span>}
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <button style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', background: 'var(--accent)', color: 'var(--accent-text)', border: 'none', borderRadius: 'var(--radius)', padding: '3px 10px', cursor: 'pointer' }}
                    onClick={() => { onRunJob(job.id); onClose(); }}>
                    ▶ Run now
                </button>
                <button style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', background: 'none', color: 'var(--accent)', border: '1px solid var(--accent)', borderRadius: 'var(--radius)', padding: '3px 10px', cursor: 'pointer' }}
                    onClick={() => { onScrollToJob(job.id); onClose(); }}>
                    → job
                </button>
                <button style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', background: 'none', color: 'var(--text-tertiary)', border: 'none', cursor: 'pointer', padding: '3px 6px' }}
                    onClick={onClose}>✕</button>
            </div>
        </div>
    );
}

export function GanttChart({
    jobs, tick, windowHours = 24, loadMapSlots = [], suggestSlots = [],
    activeJobNames = new Set(),
    onRunJob = () => {}, onScrollToJob = () => {},
}) {
    void tick;
    const [selectedBucketIdx, setSelectedBucketIdx] = useState(null);
    const [selectedBarId, setSelectedBarId] = useState(null);
    const [barRuns, setBarRuns] = useState({});
    const [barRunsLoading, setBarRunsLoading] = useState(false);
    // zoomedAnchor: when set, the timeline shows a ±1h window around that timestamp.
    // Clicking a bar zooms in; clicking zoom-out button or the same bar returns to full view.
    const [zoomedAnchor, setZoomedAnchor] = useState(null);

    // What it shows: Rich hover tooltip for each Gantt bar — name, model, and estimated duration.
    // Decision it drives: User can quickly scan bar details without clicking to zoom.
    const tooltip = useSignal(null); // { x, y, job }

    const wallNow = Date.now() / 1000;
    // nowSeconds: integer Unix timestamp used for past/overrun bar classification.
    // Computed once per render — no polling interval needed; re-renders on the normal poll cycle.
    const nowSeconds = Math.floor(Date.now() / 1000);
    // When zoomed, windowStart shifts so the anchor bar is centered in a 2h view.
    const zoomWindowSecs = 2 * 3600;
    const windowSecs = zoomedAnchor ? zoomWindowSecs : windowHours * 3600;
    // now is used as the left edge of the timeline. In zoom mode, shift left so the
    // zoomed bar appears near center (anchor − 45min = 75% into a 2h window).
    const now = zoomedAnchor ? zoomedAnchor - zoomWindowSecs * 0.5 : wallNow;
    const windowEnd = now + windowSecs;

    // "Now" needle position — in normal mode, wallNow IS the left edge so left:0%.
    // In zoom mode the window is centered on the clicked bar, so wallNow may be anywhere
    // in the window (or even offscreen left when zooming a future bar).
    // needleLeftPct tracks the real wall-clock position; hide needle when outside the window.
    const needleLeftPct = ((wallNow - now) / windowSecs) * 100;
    const needleVisible = needleLeftPct >= -0.5 && needleLeftPct <= 100.5;

    // A job is visible if it overlaps the current window — starts before the right edge
    // AND its end (next_run + duration) is after the left edge (now).
    // This prevents past-due/disabled jobs from piling up at left:0% in zoom mode.
    const visibleJobs = jobs.filter(job => {
        const jobEnd = job.next_run + (job.estimated_duration || 600);
        return job.next_run < windowEnd && jobEnd > now;
    });

    const laneJobs = assignLanes(visibleJobs);
    const conflictIds = findHeavyConflicts(laneJobs);
    const laneCount = laneJobs.reduce((max, job) => Math.max(max, job._lane + 1), 1);
    const laneHeight = 44;
    const chartHeight = laneCount * laneHeight + 8;

    // Prefer load_map data (priority-weighted); fall back to raw job count.
    // Clip to bucketCount so windowHours != 24 doesn't over-render cells.
    const bucketCount = Math.round(windowSecs / DENSITY_BUCKET_SECS);
    const useLoadMap = loadMapSlots.length > 0;
    const densityBuckets = useLoadMap
        ? alignLoadMapToNow(loadMapSlots, now).slice(0, bucketCount)
        : buildDensityBuckets(visibleJobs, now, windowSecs);

    const bucketJobIds = buildBucketJobIds(
        visibleJobs,
        now, windowSecs, bucketCount
    );

    async function handleBarClick(job) {
        if (selectedBarId === job.id) {
            setSelectedBarId(null);
            setZoomedAnchor(null);
            return;
        }
        setSelectedBarId(job.id);
        // Zoom the timeline to a 2h window centered on this bar's scheduled start.
        setZoomedAnchor(job.next_run);
        if (!barRuns[job.id]) {
            setBarRunsLoading(true);
            try {
                const runs = await fetchJobRuns(job.id, 5);
                setBarRuns(prev => ({ ...prev, [job.id]: runs }));
            } catch (err) {
                console.error('fetchJobRuns failed:', err);
            } finally {
                setBarRunsLoading(false);
            }
        }
    }

    // Convert midnight-anchored absolute slot indices to now-aligned display indices.
    // Include seconds so slot boundary matches alignLoadMapToNow exactly.
    const _nowDate = new Date(now * 1000);
    const nowSlot = Math.floor(
        (_nowDate.getHours() * 3600 + _nowDate.getMinutes() * 60 + _nowDate.getSeconds()) / DENSITY_BUCKET_SECS
    );
    const suggestDisplayIndices = new Set(
        suggestSlots
            .map(s => (s.slot - nowSlot + 48) % 48)
            .filter(idx => idx < bucketCount)
    );

    return (
        <div style={{ position: 'relative', width: '100%' }}>
            {/* Zoom indicator — shown when user has clicked a bar to zoom the timeline to ±1h */}
            {zoomedAnchor && (
                <div style={{
                    display: 'flex', alignItems: 'center', gap: '0.5rem',
                    marginBottom: '0.25rem',
                    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                    color: 'var(--accent)',
                }}>
                    <span>⌖ zoomed: {new Date(zoomedAnchor * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ±1h</span>
                    <button
                        onClick={() => { setZoomedAnchor(null); setSelectedBarId(null); }}
                        style={{
                            background: 'none', border: '1px solid var(--accent)',
                            borderRadius: 'var(--radius)', cursor: 'pointer',
                            color: 'var(--accent)', fontFamily: 'var(--font-mono)',
                            fontSize: 'var(--type-micro)', padding: '1px 7px',
                        }}
                    >zoom out</button>
                </div>
            )}
            {/* Bucket selection label — shows time range and active job count for selected density bucket */}
            {selectedBucketIdx !== null && (() => {
                const bucketSecs = windowSecs / bucketCount;
                const bucketStart = now + selectedBucketIdx * bucketSecs;
                const bucketEnd = bucketStart + bucketSecs;
                const activeIds = bucketJobIds[selectedBucketIdx] || new Set();
                const startStr = new Date(bucketStart * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                const endStr = new Date(bucketEnd * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                return (
                    <div style={{
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                        color: 'var(--accent)', marginBottom: '0.2rem',
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    }}>
                        <span>{startStr} – {endStr} · {activeIds.size} job{activeIds.size !== 1 ? 's' : ''} active</span>
                        <button
                            onClick={() => setSelectedBucketIdx(null)}
                            style={{
                                background: 'none', border: 'none', cursor: 'pointer',
                                color: 'var(--text-tertiary)', fontSize: 'var(--type-micro)',
                                fontFamily: 'var(--font-mono)', padding: '0 4px',
                            }}
                        >✕ clear</button>
                    </div>
                );
            })()}

            {/* Load density strip — priority-weighted load_map or job-count fallback */}
            {(() => {
                const hasPinned = densityBuckets.some(s => s >= LOAD_MAP_PIN_SCORE);
                return (
                    <div
                        style={{
                            display: 'flex',
                            height: 10,
                            borderRadius: 'var(--radius)',
                            overflow: 'hidden',
                            marginBottom: '0.2rem',
                            border: '1px solid var(--border-subtle)',
                        }}
                        title={useLoadMap
                            ? `How busy is each 30-minute window — darker blue = more work scheduled${hasPinned ? '. Orange = a reserved slot the scheduler keeps free' : ''}`
                            : 'How many jobs overlap each 30-minute window — darker = more scheduled work piling up at that time'}
                    >
                        {densityBuckets.map((score, bucketIdx) => {
                            const isSuggested = suggestDisplayIndices.has(bucketIdx);
                            const isSelected = bucketIdx === selectedBucketIdx;
                            return (
                                <div
                                    key={bucketIdx}
                                    onClick={() => setSelectedBucketIdx(isSelected ? null : bucketIdx)}
                                    style={{
                                        flex: 1,
                                        position: 'relative',
                                        cursor: 'pointer',
                                        background: useLoadMap
                                            ? loadMapSlotColor(score)
                                            : (score === 0
                                                ? 'var(--bg-inset)'
                                                : score === 1
                                                    ? 'rgba(99,179,237,0.25)'
                                                    : score === 2
                                                        ? 'rgba(99,179,237,0.55)'
                                                        : 'rgba(99,179,237,0.9)'),
                                        borderRight: bucketIdx < densityBuckets.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                                        outline: isSelected ? '2px solid var(--accent)' : isSuggested ? '2px solid rgba(52,211,153,0.9)' : 'none',
                                        outlineOffset: '-2px',
                                    }}
                                    title={isSuggested
                                        ? `Good time to add a job — low traffic, suggested by the scheduler`
                                        : useLoadMap && score > 0
                                            ? (score >= LOAD_MAP_PIN_SCORE ? 'Locked slot — the scheduler keeps this window free and won\'t add new jobs here' : `Busy level: ${score} — higher = more work competing in this window`)
                                            : (score > 0 ? `${score} job${score > 1 ? 's are' : ' is'} active in this 30-minute window` : undefined)}
                                />
                            );
                        })}
                    </div>
                );
            })()}

            {/* Time axis labels */}
            <div style={{ display: 'flex', justifyContent: 'space-between',
                          fontSize: 'var(--type-label)', color: 'var(--text-tertiary)',
                          fontFamily: 'var(--font-mono)', marginBottom: '0.25rem' }}>
                {[0, 6, 12, 18, 24].map(offset => {
                    const t = new Date((now + offset * 3600) * 1000);
                    return (
                        <span key={offset}>
                            {offset === 0 ? 'now' : t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                    );
                })}
            </div>

            {/* Chart area */}
            <div
                onClick={() => setSelectedBarId(null)}
                style={{
                    position: 'relative',
                    height: chartHeight,
                    background: 'var(--bg-inset)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius)',
                    overflow: 'hidden',
                }}
            >
                {/* Lane dividers */}
                {Array.from({ length: laneCount }, (_, laneIdx) => (
                    <div key={laneIdx} style={{
                        position: 'absolute',
                        top: laneIdx * laneHeight,
                        left: 0, right: 0,
                        height: laneHeight,
                        borderBottom: laneIdx < laneCount - 1
                            ? '1px solid var(--border-subtle)' : 'none',
                    }} />
                ))}

                {/* "Now" cursor needle — position tracks real wall-clock within the window.
                    In zoom mode the window is anchored on a future bar so needle may be
                    to the left of center. Hidden entirely when the current time is offscreen. */}
                {needleVisible && (
                <div
                    aria-hidden="true"
                    style={{
                        position: 'absolute',
                        left: `${Math.max(0, needleLeftPct)}%`,
                        top: 0,
                        bottom: 0,
                        width: 2,
                        background: 'var(--accent)',
                        opacity: 0.7,
                        zIndex: 5,
                        pointerEvents: 'none',
                    }}
                >
                    {/* Downward triangle tick at top */}
                    <div style={{
                        position: 'absolute',
                        top: 0,
                        left: -4,
                        width: 0,
                        height: 0,
                        borderLeft: '4px solid transparent',
                        borderRight: '4px solid transparent',
                        borderTop: '5px solid var(--accent)',
                    }} />
                </div>
                )}

                {/* Job bars */}
                {/* Heavy conflict badges */}
                {conflictIds.size > 0 && (() => {
                    const heavy = laneJobs.filter(j => j.model_profile === 'heavy' && conflictIds.has(j.id));
                    const badges = [];
                    for (const [a, b] of getConflictingPairs(heavy)) {
                        const aEnd = a.next_run + (a.estimated_duration || 600);
                        const bEnd = b.next_run + (b.estimated_duration || 600);
                        const midStart = Math.max(a.next_run, b.next_run);
                        const midEnd = Math.min(aEnd, bEnd);
                        const midPoint = (midStart + midEnd) / 2;
                        const leftPct = ((midPoint - now) / windowSecs) * 100;
                        const lowerLane = Math.max(a._lane, b._lane);
                        badges.push(
                            <div
                                key={`conflict-${a.id}-${b.id}`}
                                title="Schedule conflict — these two large AI models overlap in time. One will have to wait for the other to finish."
                                style={{
                                    position: 'absolute',
                                    left: `${Math.max(1, Math.min(leftPct - 4, 88))}%`,
                                    top: lowerLane * laneHeight + laneHeight / 4,
                                    background: 'var(--status-error)',
                                    color: '#fff',
                                    fontSize: 'var(--type-micro)',
                                    fontFamily: 'var(--font-mono)',
                                    padding: '1px 5px',
                                    borderRadius: 3,
                                    pointerEvents: 'none',
                                    zIndex: 10,
                                    whiteSpace: 'nowrap',
                                }}
                            >
                                ⚠ overlap
                            </div>
                        );
                    }
                    return badges;
                })()}

                {laneJobs.map(job => {
                    const isDimmed = selectedBucketIdx !== null && !(bucketJobIds[selectedBucketIdx]?.has(job.id));
                    const startOffset = Math.max(0, job.next_run - now);
                    const color = sourceColor(job.source);
                    const isHeavy = job.model_profile === 'heavy';
                    const isConcurrent = job._lane > 0;
                    const modelLabel = job.model
                        ? job.model.split(':')[0]
                        : (job.model_profile || null);

                    // Candlestick segments — left wick (warmup), body (inference), right wick (unload hold).
                    // warmup: time to cold-load model weights; inference: actual compute; unload: VRAM hold time.
                    // Cap warmup at 40% of estimated_duration so the body never collapses to a sliver
                    // (edge case: heavy model with very few historical runs → short duration estimate).
                    const _rawWarmup = job.warmup_estimate || 0;
                    const _estDur = job.estimated_duration || 600;
                    const warmupSecs = Math.min(_rawWarmup, Math.floor(_estDur * 0.4));
                    const inferenceSecs = Math.max(1, _estDur - warmupSecs);
                    const unloadSecs = job.model ? UNLOAD_HOLD_SECS : 0;
                    const totalSecs = warmupSecs + inferenceSecs + unloadSecs;
                    const widthPct = Math.max(0.5, (totalSecs / windowSecs) * 100);
                    const leftPct = (startOffset / windowSecs) * 100;
                    const barWidth = Math.max(0.5, Math.min(widthPct, 100 - leftPct));
                    // Fraction of total bar width each segment occupies
                    const warmupFrac = warmupSecs / totalSecs;
                    const inferenceFrac = inferenceSecs / totalSecs;

                    const showChip = barWidth > 5;
                    const showSource = barWidth > 14 && job.source && job.source !== job.name;
                    const isSelected = selectedBarId === job.id;
                    // Disabled jobs render with reduced opacity and a hatched body pattern
                    // so they're visually distinct from enabled bars without cluttering the chart.
                    const isDisabled = !job.enabled;

                    // Time-aware bar state — uses nowSeconds computed once at render time (above).
                    // isPast: the job's estimated window (start + duration) has already passed — shown desaturated.
                    // isOverrun: window has passed AND the job is still listed as running — shown with threat-pulse effect.
                    const jobEstEnd = job.next_run + (job.estimated_duration || 600);
                    const isPast = nowSeconds > jobEstEnd;
                    const isOverrun = isPast && activeJobNames.has(job.name);

                    // Outcome dot uses real exit code, not timing drift.
                    const { label: outcomeLabel, color: outcomeColor } = lastRunOutcome(job.last_exit_code, job.last_run);
                    const lastRunStr = job.last_run
                        ? new Date(job.last_run * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                        : 'never';

                    // Stock-market horizontal candlestick layout:
                    //   left wick (thin line) = model cold-load time
                    //   body (full-height rect) = inference runtime
                    //   right wick (thin line) = VRAM hold after completion
                    // align-items:center on the outer div vertically centers the thin wicks
                    // against the full-height body — matching the classic candlestick silhouette.
                    const wickThickness = 3; // px, thin wick height

                    return (
                        <div
                            key={job.id}
                            title={buildTooltip(job, isConcurrent)}
                            onClick={evt => { evt.stopPropagation(); handleBarClick(job); }}
                            onMouseEnter={e => { tooltip.value = { x: e.clientX + 16, y: e.clientY, job }; }}
                            onMouseLeave={() => { tooltip.value = null; }}
                            data-sh-effect={isOverrun ? 'threat-pulse' : undefined}
                            style={{
                                position: 'absolute',
                                left: `${Math.min(leftPct, 99.5)}%`,
                                width: `${barWidth}%`,
                                top: job._lane * laneHeight + 4,
                                height: laneHeight - 8,
                                opacity: isDimmed ? 0.15 : (isDisabled ? 0.4 : 1),
                                // Past bars (estimated window fully elapsed) desaturate to signal
                                // stale schedule data — the job window has passed without evidence of completion.
                                filter: isPast ? 'saturate(0.2) opacity(0.6)' : undefined,
                                transition: 'opacity 0.2s ease',
                                outline: conflictIds.has(job.id) ? '2px solid var(--status-error)' : undefined,
                                outlineOffset: conflictIds.has(job.id) ? '-1px' : undefined,
                                overflow: 'visible',
                                display: 'flex',
                                alignItems: 'center',  // vertically centers thin wicks against the body
                                cursor: 'pointer',
                            }}
                        >
                            {/* Left wick: thin horizontal line = model cold-load time */}
                            {warmupSecs > 0 && (
                                <div style={{
                                    width: `${warmupFrac * 100}%`,
                                    height: wickThickness,
                                    background: color,
                                    opacity: 0.7,
                                    borderRadius: '2px 0 0 2px',
                                    borderLeft: isHeavy ? `2px solid var(--status-warning)` : undefined,
                                    flexShrink: 0,
                                }} />
                            )}
                            {/* Body: full-height rectangle = inference runtime — the main job work.
                                Disabled jobs get a dashed border outline to distinguish from enabled. */}
                            <div style={{
                                width: `${inferenceFrac * 100}%`,
                                height: '100%',
                                background: color,
                                opacity: isDisabled ? 0.6 : 0.85,
                                borderRadius: warmupSecs > 0
                                    ? (unloadSecs > 0 ? '0' : '0 var(--radius) var(--radius) 0')
                                    : (unloadSecs > 0 ? 'var(--radius) 0 0 var(--radius)' : 'var(--radius)'),
                                borderLeft: (warmupSecs === 0 && isHeavy) ? '3px solid var(--status-warning)' : undefined,
                                outline: isDisabled ? '1px dashed rgba(255,255,255,0.45)' : undefined,
                                outlineOffset: isDisabled ? '-2px' : undefined,
                                flexShrink: 0,
                                display: 'flex',
                                alignItems: 'center',
                                paddingLeft: '0.4rem',
                                gap: '0.3rem',
                                overflow: 'hidden',
                                position: 'relative',
                            }}>
                                <span style={{
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: 'var(--type-label)',
                                    color: 'var(--accent-text)',
                                    fontWeight: 600,
                                    whiteSpace: 'nowrap',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    flexShrink: 1,
                                }}>
                                    {isDisabled ? '⏸ ' : ''}{isConcurrent && '⟡ '}{job.name}
                                </span>
                                {showChip && modelLabel && (
                                    <span style={{
                                        fontFamily: 'var(--font-mono)',
                                        fontSize: 'var(--type-micro)',
                                        color: 'rgba(255,255,255,0.7)',
                                        background: 'rgba(0,0,0,0.25)',
                                        borderRadius: 3,
                                        padding: '1px 4px',
                                        whiteSpace: 'nowrap',
                                        flexShrink: 0,
                                    }}>
                                        {modelLabel}
                                    </span>
                                )}
                                {showSource && (
                                    <span style={{
                                        fontFamily: 'var(--font-mono)',
                                        fontSize: 'var(--type-micro)',
                                        color: 'rgba(255,255,255,0.55)',
                                        background: 'rgba(0,0,0,0.18)',
                                        borderRadius: 3,
                                        padding: '1px 4px',
                                        whiteSpace: 'nowrap',
                                        flexShrink: 0,
                                    }}>
                                        {job.source}
                                    </span>
                                )}
                                {/* Skip badge: ↻N shows when the job was skipped N times in the last 24h
                                    because it was still running when the next interval fired. */}
                                {showChip && (job.skip_count_24h || 0) > 0 && (
                                    <span
                                        title={`Skipped ${job.skip_count_24h} times in the last 24h — the job was still running when it was supposed to start again`}
                                        style={{
                                            fontFamily: 'var(--font-mono)',
                                            fontSize: 'var(--type-micro)',
                                            color: 'rgba(255,255,255,0.85)',
                                            background: 'rgba(249,115,22,0.6)',
                                            borderRadius: 3,
                                            padding: '1px 4px',
                                            whiteSpace: 'nowrap',
                                            flexShrink: 0,
                                        }}
                                    >
                                        ↻{job.skip_count_24h}
                                    </span>
                                )}
                                {/* Outcome dot: green=last succeeded, red=last failed, gray=unknown */}
                                {showChip && (
                                    <span
                                        title={`Last ran: ${lastRunStr} · ${outcomeLabel}`}
                                        style={{
                                            position: 'absolute',
                                            right: 4,
                                            top: '50%',
                                            transform: 'translateY(-50%)',
                                            width: 7,
                                            height: 7,
                                            borderRadius: '50%',
                                            background: outcomeColor,
                                            border: '1px solid rgba(0,0,0,0.3)',
                                            flexShrink: 0,
                                        }}
                                    />
                                )}
                            </div>
                            {/* Right wick: thin horizontal line = VRAM hold after job ends */}
                            {unloadSecs > 0 && (
                                <div style={{
                                    flex: 1,
                                    height: wickThickness,
                                    background: color,
                                    opacity: 0.5,
                                    borderRadius: '0 2px 2px 0',
                                    flexShrink: 0,
                                }} />
                            )}
                            {isSelected && (
                                <BarDetailCard
                                    job={job}
                                    runs={barRuns[job.id] || null}
                                    runsLoading={barRunsLoading}
                                    onClose={() => { setSelectedBarId(null); setZoomedAnchor(null); }}
                                    onRunJob={onRunJob}
                                    onScrollToJob={onScrollToJob}
                                />
                            )}
                        </div>
                    );
                })}
            </div>

        {/* Legend — anchors the visual encoding so bars are readable without prior knowledge */}
        <div style={{
            display: 'flex', flexWrap: 'wrap', gap: '0.3rem 0.9rem',
            marginTop: '0.5rem', alignItems: 'center',
            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
            color: 'var(--text-tertiary)',
        }}>
            <span style={{ fontWeight: 600, color: 'var(--text-tertiary)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                color:
            </span>
            {[
                { color: 'var(--accent)',        label: 'aria',     symbol: '◆' },
                { color: '#f97316',              label: 'telegram', symbol: '●' },
                { color: '#a78bfa',              label: 'notion',   symbol: '▲' },
                { color: 'var(--text-tertiary)', label: 'other',    symbol: '·' },
            ].map(({ color, label, symbol }) => (
                <span key={label} style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                    <span style={{ color }}>{symbol}</span>
                    {label}
                </span>
            ))}
            <span style={{ color: 'var(--border-subtle)', userSelect: 'none' }}>│</span>
            {/* Candlestick encoding legend — mimics the actual bar shape: thin-wick body thin-wick */}
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <span style={{ display: 'flex', alignItems: 'center', width: 36, height: 10 }}>
                    <span style={{ flex: 1, height: 2, background: 'rgba(99,179,237,0.7)', borderRadius: '2px 0 0 2px' }} />
                    <span style={{ width: 16, height: '100%', background: 'rgba(99,179,237,0.85)', borderRadius: 1 }} />
                    <span style={{ flex: 1, height: 2, background: 'rgba(99,179,237,0.5)', borderRadius: '0 2px 2px 0' }} />
                </span>
                load · run · unload
            </span>
            <span style={{ color: 'var(--border-subtle)', userSelect: 'none' }}>│</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <span style={{
                    display: 'inline-block', width: 7, height: 7,
                    borderRadius: '50%', background: 'var(--status-healthy)', flexShrink: 0,
                }} />
                last ok
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <span style={{
                    display: 'inline-block', width: 7, height: 7,
                    borderRadius: '50%', background: 'var(--status-error)', flexShrink: 0,
                }} />
                last failed
            </span>
            <span style={{ color: 'var(--border-subtle)', userSelect: 'none' }}>│</span>
            <span>click bar to zoom · click again to reset</span>
        </div>

        {/* Hover tooltip — appears near cursor with job name, model, and estimated duration.
            pointer-events:none so it never blocks hover on underlying bars. */}
        {tooltip.value && (
            <div style={`position:fixed;left:${tooltip.value.x}px;top:${tooltip.value.y}px;z-index:100;background:var(--bg-surface);border:1px solid var(--border-primary);border-radius:var(--radius);padding:10px 12px;font-size:var(--type-label);color:var(--text-secondary);pointer-events:none;box-shadow:var(--card-shadow-hover);`}>
                <div style="color:var(--text-primary);margin-bottom:4px;font-weight:600;">{tooltip.value.job.name || tooltip.value.job.source}</div>
                <div>{tooltip.value.job.model || '—'}</div>
                {tooltip.value.job.estimated_duration && <div style="color:var(--text-tertiary);">~{formatDuration(tooltip.value.job.estimated_duration)}</div>}
            </div>
        )}
    </div>
    );
}
