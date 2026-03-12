import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import { glitchText } from 'superhot-ui';

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

    const chipRef = useRef(null);
    const prevDisconnected = useRef(isDisconnected);

    // Glitch burst: fire once when transitioning INTO disconnected state.
    // The visual jolt signals "connection just dropped" — distinct from the static warning color.
    useEffect(() => {
        const was = prevDisconnected.current;
        prevDisconnected.current = isDisconnected;
        if (isDisconnected && !was && chipRef.current) {
            glitchText(chipRef.current, { intensity: 'high' });
        }
    }, [isDisconnected]);

    const ramCrit  = s.pause_ram_pct  || 85;
    const vramCrit = s.pause_vram_pct || 90;
    const resourceCritical = (ram ?? 0) >= ramCrit ||
                             (vram ?? 0) >= vramCrit ||
                             (load ?? 0) >= (s.pause_load_avg || 8);
    const RAM_WARN_RATIO  = 0.82;  // warning at ~82% of critical threshold
    const VRAM_WARN_RATIO = 0.83;  // warning at ~83% of critical threshold
    const resourceWarning = !resourceCritical &&
        ((ram ?? 0) >= ramCrit * RAM_WARN_RATIO || (vram ?? 0) >= vramCrit * VRAM_WARN_RATIO);

    let label, color;

    if (isDisconnected || isError || isPaused || resourceCritical || (dlqCount || 0) > 3) {
        // When disconnected, only count 1 issue — stale resource/DLQ readings are not independent signals
        const count = isDisconnected ? 1
            : [isError || isPaused, resourceCritical, (dlqCount || 0) > 3].filter(Boolean).length;
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
        <div ref={chipRef} style={`display:flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:var(--type-micro);color:${color};padding:6px 8px;`}>
            <span style={`width:6px;height:6px;border-radius:50%;background:${color};flex-shrink:0;`} />
            {label}
        </div>
    );
}
