import { h } from 'preact';

// What it shows: A single-line aggregate health indicator combining daemon state,
//   DLQ failure count, and resource pressure (RAM/VRAM/load) into one green/amber/red signal.
// Decision it drives: Tells the user at a glance whether the queue system is healthy or
//   has issues that need attention — without requiring them to visit individual tabs.

/**
 * SystemHealthChip — single-line aggregate health indicator.
 * Combines daemon state + DLQ count + resource pressure.
 * Props: daemonState, dlqCount, ram, vram, load, swap, settings, connectionStatus
 */
export default function SystemHealthChip({ daemonState, dlqCount, ram, vram, load, swap, settings, connectionStatus }) {
    const s = settings || {};
    const isPaused = (daemonState || '').startsWith('paused');
    const isError = daemonState === 'error';
    const isDisconnected = connectionStatus === 'disconnected';

    const resourceCritical = (ram ?? 0) >= (s.pause_ram_pct || 85) ||
                             (vram ?? 0) >= (s.pause_vram_pct || 90) ||
                             (load ?? 0) >= (s.pause_load_avg || 8);
    const resourceWarning = !resourceCritical && ((ram ?? 0) >= 70 || (vram ?? 0) >= 75);

    let label, color;

    if (isDisconnected || isError || isPaused || resourceCritical || (dlqCount || 0) > 3) {
        // Count distinct issue types for label
        const count = [isDisconnected || isError || isPaused, resourceCritical, (dlqCount || 0) > 3]
            .filter(Boolean).length;
        label = count === 1 ? '1 Issue' : `${count} Issues`;
        color = 'var(--status-error)';
    } else if (resourceWarning || (dlqCount || 0) > 0) {
        const count = [resourceWarning, (dlqCount || 0) > 0].filter(Boolean).length;
        label = count === 1 ? '1 Warning' : `${count} Warnings`;
        color = 'var(--status-warning)';
    } else {
        label = 'Healthy';
        color = 'var(--status-healthy)';
    }

    return (
        <div style={`display:flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:var(--type-micro);color:${color};padding:6px 8px;`}>
            <span style={`width:6px;height:6px;border-radius:50%;background:${color};flex-shrink:0;`} />
            {label}
        </div>
    );
}
