import { h } from 'preact';

/**
 * What it shows: A 48-bar histogram of the next 24 hours split into 30-min slots.
 *   Each bar's opacity represents how many recurring jobs are scheduled to fire in that
 *   window — dark = congested, light = open. Hover a bar for the exact count and time.
 * Decision it drives: When is the queue lightest? This is the same data the backend uses
 *   when you click "Suggest slot" — the top-3 lowest-load windows become candidate cron times.
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
                    Load
                </span>
                <span class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                    24h
                </span>
            </div>

            {/* Bars */}
            <div style={{ display: 'flex', gap: '1px', height: '24px', alignItems: 'flex-end' }}>
                {slots.map((count, idx) => (
                    <div
                        key={idx}
                        title={`${slotLabel(idx)} — ${count} job${count !== 1 ? 's' : ''}`}
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
