// What it shows: Status of each configured Ollama backend — reachability, model count,
//   GPU (VRAM) pressure, and which model is currently loaded in VRAM.
// Decision it drives: Tells the user which machine is handling inference, whether any
//   backend is overloaded (VRAM > 80%) or unreachable, so they know where requests are
//   going and can spot capacity problems at a glance.

import { useEffect } from 'preact/hooks';
import { backendsData, fetchBackends } from '../stores';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

export default function BackendsPanel() {
    const backends = backendsData.value;

    // Self-managed 15s refresh — independent of the main 5s status poll so
    // backend latency (2× remote HTTP per backend) doesn't slow the hot path.
    useEffect(() => {
        fetchBackends();
        const id = setInterval(fetchBackends, 15000);
        return () => clearInterval(id);
    }, []);

    // Only show when multiple backends are configured — single-backend users
    // already see their machine's health in the Resource Gauges above.
    if (!backends || backends.length <= 1) return null;

    return (
        <div class="t-frame" data-label="Backends" data-chroma="lune">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                {backends.map((backend) => {
                    // Prefer GPU name (e.g. "RTX 4090") over hostname — more meaningful to the user.
                    // Fall back to hostname if gpu_name is null (non-GPU machine or unreachable queue).
                    let host = backend.url;
                    try { host = new URL(backend.url).hostname; } catch (_) { /* keep full url */ }
                    const label = backend.gpu_name || host;

                    const vramColor = backend.vram_pct > 80
                        ? 'var(--status-error)'
                        : backend.vram_pct > 60
                            ? 'var(--status-warning)'
                            : 'var(--accent)';

                    // Show first loaded model name (abbreviated) + overflow count
                    const loadedLabel = backend.loaded_models?.length > 0
                        ? `● ${backend.loaded_models[0].split(':')[0]}${backend.loaded_models.length > 1 ? ` +${backend.loaded_models.length - 1}` : ''}`
                        : null;

                    return (
                        <div
                            key={backend.url}
                            title={backend.url}
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '0.5rem',
                                padding: '0.375rem 0.5rem',
                                background: 'var(--bg-elevated)',
                                borderRadius: 'var(--radius-sm)',
                                fontSize: 'var(--type-label)',
                                fontFamily: 'var(--font-mono)',
                            }}
                        >
                            {/* Health indicator dot */}
                            <span style={{
                                width: 8,
                                height: 8,
                                borderRadius: '50%',
                                background: backend.healthy ? 'var(--status-ok)' : 'var(--status-error)',
                                flexShrink: 0,
                            }} />

                            {/* GPU name (falls back to hostname) */}
                            <span style={{
                                color: 'var(--text-primary)',
                                flex: '0 0 auto',
                                minWidth: '7rem',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                            }}>
                                {label}
                            </span>

                            {/* Model count */}
                            <span style={{ color: 'var(--text-tertiary)', flex: '0 0 auto' }}>
                                {backend.model_count}m
                            </span>

                            {backend.healthy ? (
                                <>
                                    {/* VRAM bar */}
                                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                                        <div style={{
                                            flex: 1, height: 4,
                                            background: 'var(--bg-surface)',
                                            borderRadius: 2,
                                            overflow: 'hidden',
                                        }}>
                                            <div style={{
                                                width: `${Math.min(backend.vram_pct, 100)}%`,
                                                height: '100%',
                                                background: vramColor,
                                                transition: 'width 0.4s',
                                            }} />
                                        </div>
                                        <span style={{
                                            color: vramColor,
                                            fontSize: 'var(--type-micro)',
                                            flexShrink: 0,
                                            minWidth: '3.5rem',
                                            textAlign: 'right',
                                        }}>
                                            {backend.vram_pct}% VRAM
                                        </span>
                                    </div>

                                    {/* Currently-loaded model (if any) */}
                                    {loadedLabel && (
                                        <span
                                            title={backend.loaded_models.join(', ')}
                                            style={{
                                                color: 'var(--status-ok)',
                                                fontSize: 'var(--type-micro)',
                                                flexShrink: 0,
                                            }}
                                        >
                                            {loadedLabel}
                                        </span>
                                    )}
                                </>
                            ) : (
                                <span style={{ color: 'var(--status-error)', flex: 1 }}>
                                    unreachable
                                </span>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
