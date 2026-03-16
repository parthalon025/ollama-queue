// What it shows: Status of each configured Ollama backend — reachability, model count,
//   GPU (VRAM) pressure, and which model is currently loaded in VRAM.
// Decision it drives: Tells the user which machine is handling inference, whether any
//   backend is overloaded (VRAM > 80%) or unreachable, so they know where requests are
//   going and can spot capacity problems at a glance.

import { useEffect } from 'preact/hooks';
import { backendsData, fetchBackends, currentJob, updateBackendInferenceMode } from '../stores';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

export default function BackendsPanel() {
    const backends = backendsData.value;
    // What it shows: the model currently executing (or null if idle).
    // Decision it drives: which backend row gets the "serving" badge.
    const activeModel = currentJob.value?.model ?? null;

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

    // If every backend is unreachable, surface that clearly instead of rendering
    // a panel full of red dots — helps the user know the routing layer itself is down.
    const allUnhealthy = backends.length > 0 && backends.every(b => !b.healthy);
    if (allUnhealthy) {
        return (
            <div class="t-frame" data-label="Backends" data-chroma="lune">
                <span style={{ color: 'var(--status-error)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}>
                    All backends unreachable — routing unavailable
                </span>
            </div>
        );
    }

    return (
        <div class="t-frame" data-label="Backends" data-chroma="lune">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                {backends.map((backend) => {
                    // Prefer GPU name (e.g. "RTX 4090") over hostname — more meaningful to the user.
                    // Fall back to hostname if gpu_name is null (non-GPU machine or unreachable queue).
                    let host = backend.url;
                    try { host = new URL(backend.url).hostname; } catch (_) { /* keep full url */ }
                    const label = backend.gpu_name || host;

                    // Thresholds match HostCard.jsx and CLAUDE.md: >90% error, >80% warning
                    const vramColor = backend.vram_pct > 90
                        ? 'var(--status-error)'
                        : backend.vram_pct > 80
                            ? 'var(--status-warning)'
                            : 'var(--accent)';

                    // Show first loaded model name (abbreviated) + overflow count
                    const loadedLabel = backend.loaded_models?.length > 0
                        ? `● ${backend.loaded_models[0].split(':')[0]}${backend.loaded_models.length > 1 ? ` +${backend.loaded_models.length - 1}` : ''}`
                        : null;

                    // "Serving" badge: this backend has the active model loaded in VRAM.
                    // Heuristic — model could be warm on multiple backends, but the one
                    // whose loaded_models includes the current job's model is the likely server.
                    const isServing = activeModel && backend.healthy &&
                        (backend.loaded_models || []).some(m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':'));

                    return (
                        <div
                            key={backend.url}
                            title={backend.url}
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '0.5rem',
                                padding: '0.375rem 0.5rem',
                                background: isServing ? 'var(--bg-elevated)' : 'var(--bg-elevated)',
                                borderRadius: 'var(--radius-sm)',
                                fontSize: 'var(--type-label)',
                                fontFamily: 'var(--font-mono)',
                                outline: isServing ? '1px solid var(--status-healthy)' : 'none',
                            }}
                        >
                            {/* Health indicator dot */}
                            <span style={{
                                width: 8,
                                height: 8,
                                borderRadius: '50%',
                                background: backend.healthy ? 'var(--status-healthy)' : 'var(--status-error)',
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

                            {/* "serving" badge: shows when this backend is executing the current job */}
                            {isServing && (
                                <span style={{
                                    color: 'var(--status-healthy)',
                                    fontSize: 'var(--type-micro)',
                                    fontFamily: 'var(--font-mono)',
                                    letterSpacing: '0.04em',
                                    flexShrink: 0,
                                }}>
                                    serving
                                </span>
                            )}

                            {/* Inference mode toggle — GPU only vs GPU+CPU overflow.
                              Clicking sends PUT /api/backends/{url}/inference-mode.
                              Decision it drives: route this backend only when model fits in VRAM. */}
                            <button
                                onClick={() => {
                                    const next = backend.inference_mode === 'gpu_only' ? 'cpu_shared' : 'gpu_only';
                                    updateBackendInferenceMode(backend.url, next).catch(err => console.error('inference mode:', err));
                                }}
                                title={backend.inference_mode === 'gpu_only'
                                    ? 'GPU only — model must fit in VRAM. Click to allow CPU overflow.'
                                    : 'GPU+CPU — Ollama may overflow to CPU RAM. Click to restrict to GPU only.'}
                                style={{
                                    background: 'none',
                                    border: '1px solid',
                                    borderColor: backend.inference_mode === 'gpu_only' ? 'var(--accent)' : 'var(--border-subtle)',
                                    borderRadius: 'var(--radius-sm)',
                                    cursor: 'pointer',
                                    padding: '0 0.3rem',
                                    color: backend.inference_mode === 'gpu_only' ? 'var(--accent)' : 'var(--text-tertiary)',
                                    fontSize: 'var(--type-micro)',
                                    fontFamily: 'var(--font-mono)',
                                    flexShrink: 0,
                                    lineHeight: '1.4',
                                }}
                            >
                                {backend.inference_mode === 'gpu_only' ? 'GPU' : 'GPU+CPU'}
                            </button>

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
                                                color: 'var(--status-healthy)',
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
