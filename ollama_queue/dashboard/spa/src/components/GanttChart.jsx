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
    if (!source) return 'var(--text-tertiary)';
    return SOURCE_COLORS[source.toLowerCase()] ?? 'var(--text-tertiary)';
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
    const parts = [
        `${job.name}`,
        `via ${job.source || 'unknown'} · ${modelStr}`,
        `est. ${formatDuration(job.estimated_duration)} · next: ${nextRunStr}`,
        `last run: ${lastRunStr}`,
    ];
    if (isConcurrent) parts.push('⟡ runs concurrently');
    return parts.join('\n');
}

export function buildDensityBuckets(jobs, now, windowSecs) {
    const bucketCount = 24;
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
    if (!lastRun) return { label: 'never', color: 'var(--text-tertiary)' };
    const elapsed = _now - lastRun;
    const interval = intervalSeconds || 3600;
    const drift = elapsed - interval;
    const threshold = interval * 0.05;
    if (drift <= threshold) return { label: 'on time', color: 'var(--status-healthy)' };
    return { label: 'late', color: 'var(--status-warning)' };
}

export function GanttChart({ jobs, tick, windowHours = 24 }) {
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

    return (
        <div style={{ position: 'relative', width: '100%' }}>
            {/* Load density strip — 24 hourly buckets, colored by job count */}
            {(() => {
                const visibleJobs = jobs.filter(job => job.next_run < windowEnd);
                const buckets = buildDensityBuckets(visibleJobs, now, windowSecs);
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
                        title="Job density per hour — darker = more jobs active"
                    >
                        {buckets.map((count, bucketIdx) => (
                            <div
                                key={bucketIdx}
                                style={{
                                    flex: 1,
                                    background: count === 0
                                        ? 'var(--bg-inset)'
                                        : count === 1
                                            ? 'rgba(99,179,237,0.25)'
                                            : count === 2
                                                ? 'rgba(99,179,237,0.55)'
                                                : 'rgba(99,179,237,0.9)',
                                    borderRight: bucketIdx < 23 ? '1px solid var(--border-subtle)' : 'none',
                                }}
                                title={count > 0 ? `${count} job${count > 1 ? 's' : ''} active` : undefined}
                            />
                        ))}
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

                {/* Job blocks */}
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
                                title="Two heavy models overlap — one will queue behind the other"
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
                                ⚠ conflict
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
                    const showChip = barWidth > 8;

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
                            {/* On-time status dot — only shown when bar is wide enough */}
                            {showChip && (() => {
                                const { label, color } = runStatus(job.last_run, job.interval_seconds);
                                const lastRunStr = job.last_run
                                    ? new Date(job.last_run * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                                    : 'never';
                                return (
                                    <span
                                        title={`Last run: ${lastRunStr} (${label})`}
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
        </div>
    );
}
