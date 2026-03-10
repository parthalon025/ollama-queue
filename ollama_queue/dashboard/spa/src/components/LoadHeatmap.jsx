import { h } from 'preact';
import { useState } from 'preact/hooks';
import { heatmapData, dlqSchedulePreview } from '../store';

// What it shows: A 24-hour × 7-day grid where each cell's brightness represents how many
//   jobs ran during that hour. DLQ-rescheduled jobs appear as dot markers on their target slot.
// Decision it drives: When is the system busiest? Are there quiet windows where rescheduled
//   DLQ jobs could run without contention? Helps identify patterns like "always overloaded at 3 AM."

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

export default function LoadHeatmap() {
    const raw = heatmapData.value;
    const dlqPreview = dlqSchedulePreview.value;
    const [hover, setHover] = useState(null);

    if (!raw || !Array.isArray(raw) || raw.length === 0) return null;

    // Build grid: day (0-6) × hour (0-23)
    // Raw heatmap data is array of { day_of_week, hour, count }
    const grid = Array.from({ length: 7 }, () => new Array(24).fill(0));
    let maxCount = 1;

    for (const entry of raw) {
        const day = entry.day_of_week != null ? entry.day_of_week : null;
        const hour = entry.hour != null ? entry.hour : null;
        const count = entry.count || 0;
        if (day != null && hour != null && day >= 0 && day < 7 && hour >= 0 && hour < 24) {
            grid[day][hour] = count;
            if (count > maxCount) maxCount = count;
        }
    }

    // DLQ scheduled markers: set of "day-hour" keys
    const dlqSlots = new Set();
    if (dlqPreview && dlqPreview.entries) {
        for (const entry of dlqPreview.entries) {
            if (entry.rescheduled_for) {
                const dt = new Date(entry.rescheduled_for * 1000);
                const day = (dt.getDay() + 6) % 7; // JS Sunday=0 → our Monday=0
                dlqSlots.add(`${day}-${dt.getHours()}`);
            }
        }
    }

    return (
        <div class="t-frame" data-label="Job Activity by Hour and Day">
            <div style="overflow-x: auto;">
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: '40px repeat(24, 1fr)',
                    gridTemplateRows: `repeat(7, 24px)`,
                    gap: '2px',
                    minWidth: '400px',
                }}>
                    {DAYS.map((dayLabel, dayIdx) => (
                        [
                            <span
                                key={`label-${dayIdx}`}
                                style={{
                                    fontSize: 'var(--type-micro)',
                                    color: 'var(--text-tertiary)',
                                    fontFamily: 'var(--font-mono)',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'flex-end',
                                    paddingRight: '4px',
                                }}
                            >
                                {dayLabel}
                            </span>,
                            ...HOURS.map(hour => {
                                const count = grid[dayIdx][hour];
                                const lightness = 15 + (1 - count / maxCount) * 70;
                                const hasDLQ = dlqSlots.has(`${dayIdx}-${hour}`);
                                const isHovered = hover && hover.day === dayIdx && hover.hour === hour;

                                return (
                                    <div
                                        key={`${dayIdx}-${hour}`}
                                        onMouseEnter={() => setHover({ day: dayIdx, hour, count })}
                                        onMouseLeave={() => setHover(null)}
                                        style={{
                                            background: `oklch(${lightness}% 0.02 270)`,
                                            borderRadius: '2px',
                                            position: 'relative',
                                            outline: isHovered ? '1px solid var(--accent)' : 'none',
                                            cursor: 'default',
                                        }}
                                    >
                                        {hasDLQ && (
                                            <div style={{
                                                position: 'absolute',
                                                top: 2, right: 2,
                                                width: 5, height: 5,
                                                borderRadius: '50%',
                                                background: 'var(--status-warn)',
                                            }} />
                                        )}
                                    </div>
                                );
                            }),
                        ]
                    )).flat()}
                </div>

                {/* Hour labels */}
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: '40px repeat(24, 1fr)',
                    gap: '2px',
                    marginTop: '2px',
                }}>
                    <span />
                    {HOURS.map(hour => (
                        <span
                            key={hour}
                            style={{
                                fontSize: '8px',
                                color: 'var(--text-tertiary)',
                                fontFamily: 'var(--font-mono)',
                                textAlign: 'center',
                            }}
                        >
                            {hour % 6 === 0 ? `${String(hour).padStart(2, '0')}` : ''}
                        </span>
                    ))}
                </div>
            </div>

            {/* Hover tooltip */}
            {hover && (
                <div style={{
                    marginTop: '0.4rem',
                    fontSize: 'var(--type-label)',
                    color: 'var(--text-secondary)',
                    fontFamily: 'var(--font-mono)',
                }}>
                    {DAYS[hover.day]} {String(hover.hour).padStart(2, '0')}:00 — {hover.count} job{hover.count !== 1 ? 's' : ''}
                </div>
            )}
        </div>
    );
}
