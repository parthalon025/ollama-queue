import { h } from 'preact';
import { dlqSchedulePreview, deferredJobs } from '../store';

/**
 * What it shows: A 48-bar histogram of the next 24 hours split into 30-min slots.
 *   Each bar's opacity represents how many recurring jobs are scheduled to fire in that
 *   window — dark = congested, light = open. DLQ-rescheduled and deferred jobs are shown
 *   as colored dot markers above their target slot. Hover a bar for slot details.
 * Decision it drives: When is the queue lightest? This is the same data the backend uses
 *   when you click "Suggest slot" — the top-3 lowest-load windows become candidate cron times.
 *   Markers show where auto-rescheduled and deferred jobs are landing.
 *
 * Opacity encoding (Treisman preattentive): dark = busy, light = free.
 *
 * Props:
 *   data: { slots: number[], slot_minutes: 30, count: 48 } | null
 */
export default function LoadMapStrip({ data }) {
    if (!data || !data.slots || data.slots.length === 0) return null;

    const slots = data.slots;
    const maxLoad = Math.max(...slots, 1);

    // Build marker sets: which slot indices have DLQ-rescheduled or deferred jobs
    const dlqSlotMarkers = new Set();
    const deferredSlotMarkers = new Set();

    const preview = dlqSchedulePreview.value;
    if (preview && preview.entries) {
        for (const entry of preview.entries) {
            if (entry.rescheduled_for) {
                const slotIdx = timestampToSlotIdx(entry.rescheduled_for);
                if (slotIdx >= 0 && slotIdx < 48) dlqSlotMarkers.add(slotIdx);
            }
        }
    }

    const deferred = deferredJobs.value;
    if (deferred && deferred.length > 0) {
        for (const entry of deferred) {
            if (entry.scheduled_for) {
                const slotIdx = timestampToSlotIdx(entry.scheduled_for);
                if (slotIdx >= 0 && slotIdx < 48) deferredSlotMarkers.add(slotIdx);
            }
        }
    }

    function slotOpacity(count) {
        return 0.12 + (count / maxLoad) * 0.88;
    }

    function slotLabel(idx) {
        const hour = Math.floor(idx / 2);
        const half = idx % 2 === 0 ? '00' : '30';
        return `${String(hour).padStart(2, '0')}:${half}`;
    }

    const ticks = [
        { slot: 0,  label: '00:00' },
        { slot: 12, label: '06:00' },
        { slot: 24, label: '12:00' },
        { slot: 36, label: '18:00' },
        { slot: 47, label: '24:00' },
    ];

    return (
        <div style={{ marginBottom: '0.5rem' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                <span class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Queue Activity
                </span>
                <span class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                    next 24h
                </span>
            </div>

            {/* Marker row — DLQ and deferred job indicators above bars */}
            {(dlqSlotMarkers.size > 0 || deferredSlotMarkers.size > 0) && (
                <div style={{ display: 'flex', gap: '1px', height: '8px', marginBottom: '2px' }}>
                    {slots.map((_, idx) => {
                        const hasDLQ = dlqSlotMarkers.has(idx);
                        const hasDeferred = deferredSlotMarkers.has(idx);
                        return (
                            <div key={idx} style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '1px' }}>
                                {hasDLQ && <div style={{ width: 4, height: 4, borderRadius: '50%', background: 'var(--status-warn)' }} title="DLQ rescheduled" />}
                                {hasDeferred && <div style={{ width: 4, height: 4, borderRadius: '50%', background: 'var(--accent)' }} title="Deferred" />}
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Bars */}
            <div style={{ display: 'flex', gap: '1px', height: '24px', alignItems: 'flex-end' }}>
                {slots.map((count, idx) => (
                    <div
                        key={idx}
                        title={`${slotLabel(idx)} — ${count} job${count !== 1 ? 's' : ''} scheduled${dlqSlotMarkers.has(idx) ? ' + DLQ rescheduled' : ''}${deferredSlotMarkers.has(idx) ? ' + deferred' : ''}${count === 0 && !dlqSlotMarkers.has(idx) && !deferredSlotMarkers.has(idx) ? ' (quiet time)' : ''}`}
                        style={{
                            flex: 1,
                            height: '100%',
                            background: 'var(--accent)',
                            opacity: slotOpacity(count),
                            borderRadius: '1px',
                        }}
                    />
                ))}
            </div>

            {/* Tick labels */}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '2px' }}>
                {ticks.map(tick => (
                    <span key={tick.label} class="data-mono" style={{ fontSize: '9px', color: 'var(--text-tertiary)', lineHeight: 1 }}>
                        {tick.label}
                    </span>
                ))}
            </div>
        </div>
    );
}

function timestampToSlotIdx(ts) {
    const dt = new Date(ts * 1000);
    return dt.getHours() * 2 + (dt.getMinutes() >= 30 ? 1 : 0);
}
