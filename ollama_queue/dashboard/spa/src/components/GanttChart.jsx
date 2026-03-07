import { h } from 'preact';

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

export function runStatus(lastRun, intervalSeconds, _now = Date.now() / 1000) {
    if (!lastRun) return { label: 'never run yet', color: 'var(--text-tertiary)' };
    const elapsed = _now - lastRun;
    const interval = intervalSeconds || 3600;
    const drift = elapsed - interval;
    const threshold = interval * 0.05;
    if (drift <= threshold) return { label: 'running on schedule', color: 'var(--status-healthy)' };
    return { label: 'running behind', color: 'var(--status-warning)' };
}

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

export function GanttChart({ jobs, tick, windowHours = 24, loadMapSlots = [], suggestSlots = [] }) {
    void tick;
    const now = Date.now() / 1000;
    const windowSecs = windowHours * 3600;
    const windowEnd = now + windowSecs;

    const laneJobs = assignLanes(
        jobs.filter(job => job.next_run < windowEnd)
    );
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
        : buildDensityBuckets(jobs.filter(job => job.next_run < windowEnd), now, windowSecs);

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
                            return (
                                <div
                                    key={bucketIdx}
                                    style={{
                                        flex: 1,
                                        position: 'relative',
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
                                        outline: isSuggested ? '2px solid rgba(52,211,153,0.9)' : 'none',
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
            <div style={{
                position: 'relative',
                height: chartHeight,
                background: 'var(--bg-inset)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius)',
                overflow: 'hidden',
            }}>
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

                {/* "Now" cursor needle */}
                <div
                    aria-hidden="true"
                    style={{
                        position: 'absolute',
                        left: 0,
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
                    const startOffset = Math.max(0, job.next_run - now);
                    const duration = job.estimated_duration || 600;
                    const leftPct = (startOffset / windowSecs) * 100;
                    const widthPct = Math.max(0.5, (duration / windowSecs) * 100);
                    const color = sourceColor(job.source);
                    const isHeavy = job.model_profile === 'heavy';
                    const isConcurrent = job._lane > 0;
                    const modelLabel = job.model
                        ? job.model.split(':')[0]
                        : (job.model_profile || null);
                    const barWidth = Math.max(0.5, Math.min(widthPct, 100 - leftPct));
                    // Lower threshold so model shows on smaller bars too
                    const showChip = barWidth > 5;
                    // Source chip only when bar is wide enough to hold both name + chips
                    const showSource = barWidth > 14 && job.source && job.source !== job.name;

                    return (
                        <div
                            key={job.id}
                            title={buildTooltip(job, isConcurrent)}
                            style={{
                                position: 'absolute',
                                left: `${Math.min(leftPct, 99.5)}%`,
                                width: `${barWidth}%`,
                                top: job._lane * laneHeight + 4,
                                height: laneHeight - 8,
                                background: color,
                                opacity: 0.85,
                                borderRadius: 'var(--radius)',
                                borderLeft: isHeavy ? '3px solid var(--status-warning)' : undefined,
                                outline: conflictIds.has(job.id) ? '2px solid var(--status-error)' : undefined,
                                outlineOffset: conflictIds.has(job.id) ? '-2px' : undefined,
                                overflow: 'hidden',
                                display: 'flex',
                                alignItems: 'center',
                                paddingLeft: isHeavy ? '0.3rem' : '0.4rem',
                                gap: '0.3rem',
                                cursor: 'default',
                            }}
                        >
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
                                {isConcurrent && '⟡ '}{job.name}
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
                            {/* Source program chip — only when bar is wide enough and source differs from name */}
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
                            {/* On-time status dot — only shown when bar is wide enough */}
                            {showChip && (() => {
                                const { label, color } = runStatus(job.last_run, job.interval_seconds);
                                const lastRunStr = job.last_run
                                    ? new Date(job.last_run * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                                    : 'never';
                                return (
                                    <span
                                        title={`Last ran: ${lastRunStr} · ${label}`}
                                        style={{
                                            position: 'absolute',
                                            right: 4,
                                            top: '50%',
                                            transform: 'translateY(-50%)',
                                            width: 7,
                                            height: 7,
                                            borderRadius: '50%',
                                            background: color,
                                            border: '1px solid rgba(0,0,0,0.3)',
                                            flexShrink: 0,
                                        }}
                                    />
                                );
                            })()}
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
                { color: 'var(--accent)',      label: 'aria'     },
                { color: '#f97316',            label: 'telegram' },
                { color: '#a78bfa',            label: 'notion'   },
                { color: 'var(--text-tertiary)', label: 'other'  },
            ].map(({ color, label }) => (
                <span key={label} style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                    <span style={{
                        display: 'inline-block', width: 10, height: 10,
                        borderRadius: 2, background: color, opacity: 0.85, flexShrink: 0,
                    }} />
                    {label}
                </span>
            ))}
            <span style={{ color: 'var(--border-subtle)', userSelect: 'none' }}>│</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <span style={{
                    display: 'inline-block', width: 10, height: 10,
                    borderRadius: 2, background: 'var(--text-tertiary)',
                    borderLeft: '3px solid var(--status-warning)', flexShrink: 0,
                }} />
                large model
            </span>
            <span style={{ color: 'var(--border-subtle)', userSelect: 'none' }}>│</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <span style={{
                    display: 'inline-block', width: 7, height: 7,
                    borderRadius: '50%', background: 'var(--status-healthy)', flexShrink: 0,
                }} />
                on schedule
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <span style={{
                    display: 'inline-block', width: 7, height: 7,
                    borderRadius: '50%', background: 'var(--status-warning)', flexShrink: 0,
                }} />
                running late
            </span>
            <span style={{ color: 'var(--border-subtle)', userSelect: 'none' }}>│</span>
            <span>bar width = expected run time · hover for details</span>
        </div>
    </div>
    );
}
