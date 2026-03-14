// What it shows: Unified host + GPU infrastructure view — the three scheduler-gate
//   metrics (RAM/CPU/Swap) that determine if a new job can start, plus one row per
//   configured backend showing VRAM pressure, the loaded model, and whether that
//   backend is currently serving the active job.
// Decision it drives: "Where is the work happening and can the system sustain it?"
//   Replaces the separate System Resources frame and BackendsPanel on the Now tab.

import { useEffect } from 'preact/hooks';
import { backendsData, fetchBackends, currentJob } from '../stores';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

// Returns the three host gauge descriptors the daemon uses to gate job admission.
// VRAM is intentionally excluded — it belongs to the backend rows, not the host row.
// Pure — no signals, fully testable.
export function hostGauges(latestHealth, settings, cpuCount) {
    if (!latestHealth) return [];
    const s = settings || {};
    const cpu = (latestHealth.load_avg / (cpuCount || 1)) * 100;
    return [
        { label: 'RAM',  value: latestHealth.ram_pct  ?? 0, pause: s.ram_pause_pct  || 85, resume: s.ram_resume_pct  || 75 },
        { label: 'CPU',  value: cpu,                         pause: (s.load_pause_multiplier || 2) * 100, resume: (s.load_resume_multiplier || 1.5) * 100 },
        { label: 'Swap', value: latestHealth.swap_pct ?? 0,  pause: s.swap_pause_pct || 50, resume: s.swap_resume_pct || 40 },
    ];
}

// Returns display state for one backend row: GPU label, VRAM pressure color,
//   loaded model name, and whether this backend is currently serving the active job.
// Decision it drives: BackendRow uses this to render the phosphor highlight + ▶ badge
//   on the backend that has the running job's model warm in VRAM.
// Pure — no signals, no DOM, fully testable.
export function backendRowState(backend, activeModel) {
    let host = backend.url;
    try { host = new URL(backend.url).hostname; } catch (_) { /* keep full url */ }
    const label = (backend.gpu_name || host)
        .replace(/^nvidia\s+geforce\s+/i, '')
        .replace(/^nvidia\s+/i, '');

    const vramPct = backend.vram_pct ?? 0;
    const vramColor = vramPct > 90
        ? 'var(--status-error)'
        : vramPct > 80
            ? 'var(--status-warning)'
            : 'var(--sh-phosphor)';

    const loaded = backend.loaded_models || [];
    const loadedLabel = loaded.length > 0
        ? `${loaded[0].split(':')[0]}${loaded.length > 1 ? ` +${loaded.length - 1}` : ''}`
        : null;
    // Full list for tooltip — shows all loaded models on hover when truncated by "+N"
    const modelsTooltip = loaded.length > 0 ? loaded.join(', ') : null;

    const isServing = !!(activeModel && backend.healthy &&
        loaded.some(m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':')));

    return { label, vramPct, vramColor, loadedLabel, modelsTooltip, isServing, isHealthy: !!backend.healthy };
}

// Returns true when there are configured backends but none are reachable.
// Used to switch from per-row rendering to a single "all unreachable" message.
// Pure — no signals, fully testable.
export function computeAllUnhealthy(backends) {
    return backends.length > 0 && backends.every(b => !b.healthy);
}

export default function InfrastructurePanel({ latestHealth, settings, cpuCount }) {
    const backends = backendsData.value || [];
    const activeModel = currentJob.value?.model ?? null;

    // Self-managed 15s refresh — independent of the main 5s status poll so
    // remote backend latency doesn't slow the hot path.
    useEffect(() => {
        fetchBackends();
        const id = setInterval(fetchBackends, 15000);
        return () => clearInterval(id);
    }, []);

    const gauges = hostGauges(latestHealth, settings, cpuCount);
    const allUnhealthy = computeAllUnhealthy(backends);

    return (
        <div class="t-frame" data-label="Infrastructure" data-chroma="lune">
            {/* Host row — RAM, CPU, Swap: the three metrics that gate job admission */}
            {gauges.length > 0 && (
                <div style={{ marginBottom: backends.length > 0 ? '0.5rem' : 0 }}>
                    <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); display: block; margin-bottom: 0.25rem;">
                        host
                    </span>
                    <div class="flex gap-3 flex-wrap">
                        {gauges.map(gauge => <HostGaugeBar key={gauge.label} gauge={gauge} />)}
                    </div>
                </div>
            )}

            {/* Divider between host metrics and backend rows */}
            {gauges.length > 0 && backends.length > 0 && (
                <div style={{ height: 1, background: 'var(--border-subtle)', margin: '0.5rem 0' }} />
            )}

            {/* Backend rows — one per configured Ollama backend */}
            {backends.length > 0 && (
                allUnhealthy ? (
                    <span style={{ color: 'var(--status-error)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}>
                        All backends unreachable — routing unavailable
                    </span>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                        {backends.map(backend => (
                            <BackendRow
                                key={backend.url}
                                row={backendRowState(backend, activeModel)}
                                url={backend.url}
                            />
                        ))}
                    </div>
                )
            )}
        </div>
    );
}

// Renders a single host metric bar with gradient fill + pause threshold marker.
// Replicates the gauge bar pattern from ResourceGauges (same gradient + mask technique).
function HostGaugeBar({ gauge }) {
    const { label, value, pause, resume } = gauge;
    const pct = Math.min(100, Math.max(0, value));
    const pauseNorm = Math.min(pause, 100);
    const resumeNorm = pause > 100 ? (resume / pause) * 100 : Math.min(resume, 100);
    const gradientBg = `linear-gradient(to right, var(--accent) 0%, var(--status-warning) ${resumeNorm.toFixed(1)}%, var(--status-error) ${pauseNorm.toFixed(1)}%)`;

    return (
        <div class="flex items-center gap-1" style="min-width: 80px; flex: 1;">
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); width: 32px; text-align: right;">
                {label}
            </span>
            <div style="flex: 1; height: 6px; background: var(--bg-inset); border-radius: 3px; position: relative; overflow: hidden;">
                <div style={{ position: 'absolute', inset: '0', background: gradientBg }} />
                <div style={{ position: 'absolute', left: `${pct}%`, top: 0, bottom: 0, right: 0, background: 'var(--bg-inset)', transition: 'left 0.3s ease' }} />
                <div style={{ position: 'absolute', left: `${pauseNorm}%`, top: 0, bottom: 0, width: '1px', borderLeft: '1px dashed var(--text-tertiary)', opacity: 0.5, zIndex: 1 }} />
            </div>
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-secondary); width: 28px;">
                {Math.round(pct)}%
            </span>
        </div>
    );
}

// Renders one backend row: health dot + GPU name + VRAM bar + loaded model + serving badge.
function BackendRow({ row, url }) {
    const { label, vramPct, vramColor, loadedLabel, modelsTooltip, isServing, isHealthy } = row;

    return (
        <div
            title={url}
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.375rem 0.5rem',
                background: 'var(--bg-elevated)',
                borderRadius: 'var(--radius-sm)',
                fontSize: 'var(--type-label)',
                fontFamily: 'var(--font-mono)',
                outline: isServing ? '1px solid var(--sh-phosphor)' : 'none',
                opacity: isHealthy ? 1 : 0.5,
            }}
        >
            {/* Health indicator dot */}
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: isHealthy ? 'var(--status-ok)' : 'var(--status-error)', flexShrink: 0 }} />

            {/* GPU label */}
            <span style={{ color: 'var(--text-primary)', flex: '0 0 auto', minWidth: '6rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {label}
            </span>

            {isHealthy ? (
                <>
                    {/* VRAM bar + percentage */}
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                        <div style={{ flex: 1, height: 4, background: 'var(--bg-surface)', borderRadius: 2, overflow: 'hidden' }}>
                            <div style={{ width: `${Math.min(vramPct, 100)}%`, height: '100%', background: vramColor, transition: 'width 0.4s' }} />
                        </div>
                        <span style={{ color: vramColor, fontSize: 'var(--type-micro)', flexShrink: 0, minWidth: '3rem', textAlign: 'right' }}>
                            {vramPct}%
                        </span>
                    </div>

                    {/* Currently loaded model — title shows full list when truncated by "+N" */}
                    {loadedLabel && (
                        <span title={modelsTooltip} style={{ color: isServing ? 'var(--sh-phosphor)' : 'var(--status-ok)', fontSize: 'var(--type-micro)', flexShrink: 0 }}>
                            · {loadedLabel}
                        </span>
                    )}

                    {/* Serving indicator — only when this backend has the active job's model */}
                    {isServing && (
                        <span style={{ color: 'var(--sh-phosphor)', fontSize: 'var(--type-micro)', flexShrink: 0, letterSpacing: '0.04em' }}>
                            ▶
                        </span>
                    )}
                </>
            ) : (
                <span style={{ color: 'var(--status-error)', flex: 1 }}>unreachable</span>
            )}
        </div>
    );
}
