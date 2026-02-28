import { h } from 'preact';

// NOTE: all .map() callbacks use descriptive names (job, slot, lane) — never 'h'
// as that shadows the JSX factory esbuild injects.

const PROFILE_COLORS = {
    embed:  'var(--status-ok)',
    ollama: 'var(--accent)',
    heavy:  'var(--status-warning)',
};

function assignLanes(jobs) {
    const sorted = [...jobs].sort((a, b) => a.next_run - b.next_run);
    const laneEnds = [];  // tracks end time of last job in each lane
    return sorted.map(job => {
        const start = job.next_run;
        const end = start + (job.estimated_duration || 600);
        let laneIdx = laneEnds.findIndex(laneEnd => laneEnd <= start);
        if (laneIdx === -1) laneIdx = laneEnds.length;
        laneEnds[laneIdx] = end;
        return { ...job, _lane: laneIdx, _end: end };
    });
}

export function GanttChart({ jobs, tick, windowHours = 24 }) {
    // tick forces re-render every second for live countdowns
    void tick;
    const now = Date.now() / 1000;
    const windowSecs = windowHours * 3600;
    const windowEnd = now + windowSecs;

    const laneJobs = assignLanes(
        jobs.filter(job => job.next_run < windowEnd)
    );
    const laneCount = laneJobs.reduce((max, job) => Math.max(max, job._lane + 1), 1);
    const laneHeight = 44;
    const chartHeight = laneCount * laneHeight + 8;

    return (
        <div style={{ position: 'relative', width: '100%' }}>
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
                {laneJobs.map(job => {
                    const startOffset = Math.max(0, job.next_run - now);
                    const duration = job.estimated_duration || 600;
                    const leftPct = (startOffset / windowSecs) * 100;
                    const widthPct = Math.max(0.5, (duration / windowSecs) * 100);
                    const color = PROFILE_COLORS[job.model_profile] || PROFILE_COLORS.ollama;
                    const isConcurrent = job._lane > 0;
                    return (
                        <div
                            key={job.id}
                            title={`${job.name}${isConcurrent ? ' ⟡ concurrent' : ''} — ${Math.round(duration / 60)}min`}
                            style={{
                                position: 'absolute',
                                left: `${Math.min(leftPct, 99.5)}%`,
                                width: `${Math.min(widthPct, 100 - leftPct)}%`,
                                top: job._lane * laneHeight + 4,
                                height: laneHeight - 8,
                                background: color,
                                opacity: 0.85,
                                borderRadius: 'var(--radius)',
                                overflow: 'hidden',
                                display: 'flex',
                                alignItems: 'center',
                                paddingLeft: '0.4rem',
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
                            }}>
                                {isConcurrent && '⟡ '}{job.name}
                            </span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
