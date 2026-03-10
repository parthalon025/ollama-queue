import { h } from 'preact';
import { status } from '../store';

// What it shows: Real-time health of the system — CPU load, memory usage, GPU VRAM,
//   and current daemon state.
// Decision it drives: Tells the user whether the system is healthy enough to run
//   more jobs, and explains why jobs might be deferred or paused.

function Gauge({ label, value, unit, threshold, warn }) {
    const pct = typeof value === 'number' ? value : 0;
    const isHigh = warn && pct >= (threshold || 80);
    return (
        <div class={`sys-gauge ${isHigh ? 'sys-gauge--warn' : ''}`}>
            <div class="sys-gauge__bar">
                <div class="sys-gauge__fill" style={{ width: `${Math.min(100, pct)}%` }} />
            </div>
            <span class="sys-gauge__label">{label}</span>
            <span class="sys-gauge__value">{pct.toFixed(0)}{unit}</span>
        </div>
    );
}

export function SystemHealth() {
    const s = status.value;
    if (!s || !s.daemon) return null;

    const health = s.daemon;
    const ram = health.ram_pct || 0;
    const vram = health.vram_pct || 0;
    const load = health.load_avg || 0;
    const swap = health.swap_pct || 0;

    return (
        <div class="sys-health">
            <h3 class="sys-health__title">System Health</h3>
            <div class="sys-health__gauges">
                <Gauge label="RAM" value={ram} unit="%" threshold={85} warn />
                <Gauge label="VRAM" value={vram} unit="%" threshold={90} warn />
                <Gauge label="Load" value={load} unit="" threshold={8} warn />
                <Gauge label="Swap" value={swap} unit="%" threshold={50} warn />
            </div>
        </div>
    );
}
