// What it shows: One GPU backend — its state (running/eval/warm/idle/offline),
//   loaded model, VRAM pressure, and host resource gauges (RAM/CPU/Swap for local hosts).
// Decision it drives: "What is each host doing and is it healthy enough to take more work?"
//   Replaces the old CurrentJob + InfrastructurePanel split — backend is the top-level unit.

import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import { useSignal } from '@preact/signals';
import { applyMantra, removeMantra } from 'superhot-ui';
import { ShStatusBadge, ShThreatPulse, ShFrozen, ShGlitch, ShShatter, ShTimeChart } from 'superhot-ui/preact';
import { cancelEvalRun } from '../stores/eval.js';
import { API } from '../stores';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import { formatDuration } from '../utils/time.js';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

// ── Pure helper functions (exported for unit testing) ─────────────────────────

/**
 * Derives the display state for one backend card.
 * Pure — no signals, no DOM, fully testable.
 * @returns {{ state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor }}
 */
export function deriveHostState(backend, currentJob, activeEval) {
    // GPU label: strip NVIDIA prefixes, fall back to URL hostname
    let host = backend.url;
    try { host = new URL(backend.url).hostname; } catch (_) { /* keep full url */ }
    const gpuLabel = (backend.gpu_name || host)
        .replace(/^nvidia\s+geforce\s+/i, '')
        .replace(/^nvidia\s+/i, '');

    // VRAM pressure
    const vramPct = backend.vram_pct ?? 0;
    const vramColor = vramPct > 90
        ? 'var(--status-error)'
        : vramPct > 80
            ? 'var(--status-warning)'
            : 'var(--sh-phosphor)';

    // Loaded model display
    const loaded = backend.loaded_models || [];
    const loadedLabel = loaded.length > 0
        ? `${loaded[0].split(':')[0]}${loaded.length > 1 ? ` +${loaded.length - 1}` : ''}`
        : null;
    const modelsTooltip = loaded.length > 0 ? loaded.join(', ') : null;

    // State priority: offline > running > eval > warm > idle
    let state, mood, statusBadgeStatus;
    if (!backend.healthy) {
        state = 'offline';
        mood = 'dread';
        statusBadgeStatus = 'error';
    } else if (currentJob && matchesBackend(backend, currentJob.model)) {
        state = 'running';
        mood = 'dawn';
        statusBadgeStatus = 'active';
    } else if (
        activeEval &&
        (activeEval.gen_backend_url === backend.url ||
         activeEval.judge_backend_url === backend.url)
    ) {
        state = 'eval';
        mood = null;
        statusBadgeStatus = 'waiting';
    } else if (loaded.length > 0) {
        state = 'warm';
        mood = null;
        statusBadgeStatus = 'ok';
    } else {
        state = 'idle';
        mood = null;
        statusBadgeStatus = 'ok';
    }

    const isServing = state === 'running';
    return { state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor };
}

/**
 * Returns true when the backend URL is local (127.0.0.1 or localhost).
 * Local backends show RAM/CPU/Swap from latestHealth; remote backends do not.
 * Pure — no side effects, fully testable.
 */
export function isLocalBackend(url) {
    return url.includes('127.0.0.1') || url.includes('localhost');
}

/**
 * Returns true when the given model matches any loaded model on the backend.
 * Uses prefix logic: qwen2.5:7b matches qwen2.5:latest and vice versa.
 * Guards against null/undefined loaded_models.
 * Pure — no signals, no DOM, fully testable.
 */
export function matchesBackend(backend, model) {
    if (!model) return false;
    const loaded = backend.loaded_models || [];
    return loaded.some(m => m === model || m.startsWith(model.split(':')[0] + ':'));
}

/**
 * Returns the three host gauge descriptors used by the daemon's job-admission gate.
 * Identical logic to the former InfrastructurePanel.hostGauges — moved here.
 * Pure — no signals, fully testable.
 * @returns {Array<{ label, value, pause, resume }>}
 */
export function hostGauges(latestHealth, settings, cpuCount) {
    if (!latestHealth) return [];
    const s = settings || {};
    const cpu = (latestHealth.load_avg / (cpuCount || 1)) * 100;
    return [
        { label: 'RAM',  value: latestHealth.ram_pct  ?? 0, pause: s.ram_pause_pct  != null ? s.ram_pause_pct  : 85, resume: s.ram_resume_pct  != null ? s.ram_resume_pct  : 75 },
        { label: 'CPU',  value: cpu,                         pause: (s.load_pause_multiplier  != null ? s.load_pause_multiplier  : 2) * 100, resume: (s.load_resume_multiplier != null ? s.load_resume_multiplier : 1.5) * 100 },
        { label: 'Swap', value: latestHealth.swap_pct ?? 0,  pause: s.swap_pause_pct != null ? s.swap_pause_pct : 50, resume: s.swap_resume_pct != null ? s.swap_resume_pct : 40 },
    ];
}

/**
 * Returns true when there are configured backends but none are reachable.
 * Moved from InfrastructurePanel.jsx — same logic.
 * Pure — no signals, fully testable.
 */
export function computeAllUnhealthy(backends) {
    return backends.length > 0 && backends.every(b => !b.healthy);
}

/**
 * What it shows: One GPU backend — running job, eval session, loaded model, VRAM pressure,
 *   and (for local hosts) RAM/CPU/Swap gauges. Each card represents a distinct Ollama backend.
 * Decision it drives: "Which host is doing what and can it take more work?"
 *   Running state = phosphor glow. Offline = threat pulse. Expanding reveals log/eval detail.
 */
export default function HostCard({
    backend,
    currentJob,
    activeEval,
    evalActiveRun,
    latestHealth,
    settings,
    cpuCount,
    healthHistory,  // full health log array (newest-first) — used for VRAM% time chart
}) {
    // Hooks before any conditional return (Rules of Hooks)
    const cardRef = useRef(null);
    const logLines = useSignal([]);
    const expanded = useSignal(false);
    const prevHealthy = useRef(backend.healthy);
    const glitchActive = useSignal(false);
    const [cancelFb, cancelAct] = useActionFeedback();

    const derived = deriveHostState(backend, currentJob, activeEval);
    const { state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor } = derived;
    const isLocal = isLocalBackend(backend.url);
    const gauges = isLocal ? hostGauges(latestHealth, settings, cpuCount) : [];
    const isRunning = state === 'running';
    const isStalled = isRunning && !!currentJob?.stall_detected_at;

    // Elapsed time and progress for running state
    let elapsed = null;
    let estimated = null;
    let progressPct = 0;
    if (isRunning && currentJob?.started_at) {
        const now = Date.now() / 1000;
        elapsed = now - currentJob.started_at;
        estimated = currentJob.estimated_duration || null;
        if (estimated && estimated > 0) progressPct = (elapsed / estimated) * 100;
    }
    const isOverrun = estimated && progressPct > 100;

    // Mantra: stamp "RUNNING" watermark on card while this backend is active
    useEffect(() => {
        if (!cardRef.current) return;
        if (isRunning) {
            applyMantra(cardRef.current, 'RUNNING');
        } else {
            removeMantra(cardRef.current);
        }
        return () => { if (cardRef.current) removeMantra(cardRef.current); };
    }, [isRunning]);

    // ShGlitch edge trigger: glitch once when healthy transitions true → false
    // NOT level-triggered — do not use active={!backend.healthy} (continuous glitch)
    useEffect(() => {
        if (prevHealthy.current === true && !backend.healthy) {
            glitchActive.value = true;
        } else {
            glitchActive.value = false;
        }
        prevHealthy.current = backend.healthy;
    }, [backend.healthy]);

    // Live log tail: polls /api/jobs/{id}/log only when expanded + running
    useEffect(() => {
        if (!expanded.value || !isRunning || !currentJob?.id) {
            logLines.value = [];
            return;
        }
        let cancelled = false;
        async function fetchLog() {
            try {
                const r = await fetch(`${API}/jobs/${currentJob.id}/log?tail=5`);
                if (!cancelled && r.ok) {
                    const data = await r.json();
                    logLines.value = data.lines || [];
                }
            } catch (_) { /* best-effort */ }
        }
        fetchLog();
        const iv = setInterval(fetchLog, 5000);
        return () => { cancelled = true; clearInterval(iv); };
    }, [expanded.value, isRunning, currentJob?.id]);

    const vramBarPct = Math.min(vramPct, 100);

    // GPU activity: VRAM % over last 24h — oldest-first for ShTimeChart.
    // Derived from healthHistory (health_log) for local backends only; remote backends
    // only expose a current snapshot (backend.vram_pct), not a time series.
    const vramChartData = isLocal && healthHistory?.length > 0
        ? healthHistory
              .filter(entry => entry.timestamp != null && entry.vram_pct != null)
              .map(entry => ({ t: entry.timestamp, v: entry.vram_pct }))
              .reverse()  // healthData is newest-first; ShTimeChart expects oldest-first
        : [];

    const card = (
        <div ref={cardRef} class="t-frame" data-label={gpuLabel}>
            {/* What it shows: Compact summary row — status badge + GPU name + VRAM + loaded model */}
            <div class="flex items-center gap-2 flex-wrap">
                <ShGlitch active={glitchActive.value} intensity="medium">
                    <ShStatusBadge status={statusBadgeStatus} />
                </ShGlitch>
                <span class="data-mono" style="color: var(--text-primary); font-size: var(--type-body);">
                    {gpuLabel}
                </span>

                {/* VRAM bar + percentage */}
                {backend.healthy && (
                    <div class="flex items-center gap-1" style="flex: 1; min-width: 80px;">
                        <div style="flex: 1; height: 4px; background: var(--bg-surface); border-radius: 2px; overflow: hidden;">
                            <div style={{
                                width: `${vramBarPct}%`,
                                height: '100%',
                                background: vramColor,
                                transition: 'width 0.4s',
                            }} />
                        </div>
                        <span class="data-mono" style={{ color: vramColor, fontSize: 'var(--type-micro)', flexShrink: 0, minWidth: '3rem', textAlign: 'right' }}>
                            {vramPct}%
                        </span>
                    </div>
                )}

                {/* Loaded model chip — title shows full list when truncated by "+N" */}
                {loadedLabel && (
                    <span
                        class="data-mono"
                        title={modelsTooltip}
                        style={{ color: isServing ? 'var(--sh-phosphor)' : 'var(--status-healthy)', fontSize: 'var(--type-micro)', flexShrink: 0 }}
                    >
                        {loadedLabel}
                    </span>
                )}

                {/* State pill */}
                {isServing && (
                    <span class="data-mono" style="color: var(--sh-phosphor); font-size: var(--type-micro); letter-spacing: 0.04em;">
                        ▶
                    </span>
                )}
            </div>

            {/* State-specific detail row */}
            {isRunning && currentJob && (
                // What it shows: Job source, elapsed time (ShFrozen freezes on stall), progress bar
                <div class="flex flex-col gap-1" style="margin-top: 0.375rem;">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="data-mono" style="font-size: var(--type-body); color: var(--text-primary);">
                            {currentJob.source}
                        </span>
                        <ShFrozen timestamp={currentJob.started_at * 1000} />
                        {estimated && (
                            <span class="data-mono" style="font-size: var(--type-label); color: var(--text-tertiary);">
                                / ~{formatDuration(estimated)}
                            </span>
                        )}
                        {isOverrun && (
                            <span style="font-size: var(--type-micro); color: var(--status-warning); background: var(--status-warning-subtle); padding: 1px 5px; border-radius: 3px;">
                                over estimate
                            </span>
                        )}
                    </div>
                    {estimated && (
                        <div style="height: 3px; background: var(--bg-inset); border-radius: 2px; overflow: hidden;">
                            <div style={{
                                width: isOverrun ? '100%' : `${progressPct}%`,
                                height: '100%',
                                background: isOverrun ? 'var(--status-warning)' : 'var(--accent)',
                                transition: 'width 1s linear',
                            }} />
                        </div>
                    )}
                </div>
            )}

            {state === 'eval' && activeEval && (
                // What it shows: Eval session in progress — phase and progress
                <div class="flex items-center gap-2 flex-wrap" style="margin-top: 0.25rem;">
                    <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
                        eval #{activeEval.id} · {activeEval.status}
                    </span>
                </div>
            )}

            {state === 'warm' && (
                <div class="data-mono" style="font-size: var(--type-label); color: var(--text-tertiary); margin-top: 0.25rem;">
                    model loaded · idle
                </div>
            )}

            {state === 'offline' && (
                <span style="color: var(--status-error); font-size: var(--type-label); font-family: var(--font-mono);">
                    unreachable
                </span>
            )}

            {/* Gauges — local host only */}
            {gauges.length > 0 ? (
                // What it shows: RAM/CPU/Swap — the three daemon job-admission gate metrics
                <div class="flex gap-3 flex-wrap" style="margin-top: 0.375rem;">
                    {gauges.map(gauge => <HostGaugeBar key={gauge.label} gauge={gauge} />)}
                </div>
            ) : (
                !isLocal && (
                    <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); display: block; margin-top: 0.25rem;">
                        remote host — system metrics not available
                    </span>
                )
            )}

            {/* Expand toggle — always visible so users can drill into any state */}
            <button
                class="t-btn"
                style="margin-top: 0.375rem; font-size: var(--type-micro);"
                onClick={() => { expanded.value = !expanded.value; }}
            >
                {expanded.value ? '▴ details' : '▾ details'}
            </button>

            {/* Expanded: log tail + stall panel (running only) */}
            {expanded.value && isRunning && (
                // What it shows: Last 5 stdout lines — confirms job is producing output
                <div style="margin-top: 0.5rem;">
                    <div style="font-family: var(--font-mono); font-size: var(--type-micro); background: var(--bg-terminal, var(--bg-inset)); padding: 8px; border-radius: var(--radius); max-height: 120px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; color: var(--text-secondary);">
                        {logLines.value.length > 0
                            ? logLines.value.map((line, i) => <div key={i}>{line}</div>)
                            : <span style="color: var(--text-tertiary);">No output yet</span>
                        }
                    </div>
                    {isStalled && (
                        <details style="margin-top: 0.5rem;">
                            <summary style="cursor: pointer; font-size: var(--type-label); color: var(--status-warning); list-style: none;">
                                ⚠ frozen — what should I do?
                            </summary>
                            <div style="padding: 8px; background: var(--bg-surface); border: 1px solid var(--border-primary); border-radius: var(--radius); font-size: var(--type-label); color: var(--text-secondary);">
                                <ol style="margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 4px;">
                                    <li>Wait 2 more minutes — some models are slow to start</li>
                                    <li>Cancel and retry — click × in the queue below</li>
                                    <li>Check Ollama: run <code style="font-family: var(--font-mono);">ollama ps</code></li>
                                    <li>Restart daemon from Settings if Ollama itself is stuck</li>
                                </ol>
                            </div>
                        </details>
                    )}
                </div>
            )}

            {/* Expanded: GPU activity — VRAM % over last 24h (local backends only) */}
            {/* What it shows: How hard the GPU has been working over time, measured as
                VRAM pressure. Peaks correspond to large model loads or concurrent requests.
                Decision it drives: Is this GPU chronically near capacity? Should jobs be
                routed elsewhere? */}
            {expanded.value && vramChartData.length > 0 && (
                <div style="margin-top: 0.5rem;">
                    <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); display: block; margin-bottom: 4px;">
                        GPU ACTIVITY (24H)
                    </span>
                    <ShTimeChart
                        data={vramChartData}
                        label="VRAM %"
                        color="var(--sh-phosphor)"
                    />
                </div>
            )}

            {/* Expanded: system health gauges full-width (local backends only) */}
            {/* What it shows: RAM/CPU/Swap at the full-width expanded view — same data as
                the compact gauge row, but with more space and the pause threshold visible.
                Decision it drives: Is this host about to hit the job-admission pause gate? */}
            {expanded.value && gauges.length > 0 && (
                <div style="margin-top: 0.5rem;">
                    <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); display: block; margin-bottom: 4px;">
                        SYSTEM HEALTH
                    </span>
                    <div class="flex flex-col gap-2">
                        {gauges.map(gauge => <HostGaugeBar key={gauge.label} gauge={gauge} />)}
                    </div>
                </div>
            )}

            {/* Expanded: eval progress + cancel (eval state only) */}
            {expanded.value && state === 'eval' && evalActiveRun && (
                // What it shows: Per-variant progress bars + cancel button for the active eval run
                <div style="margin-top: 0.5rem; display: flex; flex-direction: column; gap: 0.375rem;">
                    {evalActiveRun.progress_pct != null && (
                        <div>
                            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary);">
                                {evalActiveRun.phase} · {Math.round(evalActiveRun.progress_pct)}%
                            </span>
                            <div style="height: 3px; background: var(--bg-inset); border-radius: 2px; overflow: hidden; margin-top: 2px;">
                                <div style={{ width: `${Math.min(evalActiveRun.progress_pct, 100)}%`, height: '100%', background: 'var(--accent)' }} />
                            </div>
                        </div>
                    )}
                    {/* Cancel eval — ShShatter animates the button out on dismiss */}
                    {activeEval?.id && (
                        <div>
                            <ShShatter
                                onDismiss={() => cancelAct(
                                    'Cancelling…',
                                    () => cancelEvalRun(activeEval.id),
                                    () => 'Cancelled'
                                )}
                            >
                                <button class="t-btn" style="font-size: var(--type-micro);">✕ cancel eval</button>
                            </ShShatter>
                            {cancelFb.msg && (
                                <span class={`action-fb action-fb--${cancelFb.phase}`}>{cancelFb.msg}</span>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );

    // Offline: wrap in ShThreatPulse + dread mood wrapper
    if (state === 'offline') {
        return (
            <div data-mood="dread">
                <ShThreatPulse active={true} persistent={true}>
                    {card}
                </ShThreatPulse>
            </div>
        );
    }

    // Running (or any other mood): wrap in mood div for CSS cascade
    if (mood) {
        return <div data-mood={mood}>{card}</div>;
    }

    return card;
}

// ── Sub-components ─────────────────────────────────────────────────────────────

// Renders a single host metric bar with gradient fill + pause threshold marker.
// Same gradient + mask technique as the former InfrastructurePanel.HostGaugeBar.
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
                <div style={{ position: 'absolute', left: `${pauseNorm}%`, top: 0, bottom: 0, width: '1px', borderLeft: '1px dashed var(--text-tertiary)', opacity: 0.5 }} />
            </div>
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-secondary); width: 28px;">
                {Math.round(pct)}%
            </span>
        </div>
    );
}
