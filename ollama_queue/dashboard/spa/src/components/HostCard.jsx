// What it shows: One GPU backend — its state (running/eval/warm/idle/offline),
//   loaded model, VRAM pressure, and host resource gauges (RAM/CPU/Swap for local hosts).
// Decision it drives: "What is each host doing and is it healthy enough to take more work?"
//   Replaces the old CurrentJob + InfrastructurePanel split — backend is the top-level unit.

import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import { useSignal } from '@preact/signals';
import { applyMantra, removeMantra } from 'superhot-ui';
import { ShStatusBadge, ShThreatPulse, ShFrozen, ShGlitch, ShShatter } from 'superhot-ui/preact';
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

// ── Component placeholder — full JSX implemented in next task ─────────────────
export default function HostCard() { return null; }
