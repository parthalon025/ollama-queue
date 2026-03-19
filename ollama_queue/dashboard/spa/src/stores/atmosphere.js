// What it does: Derives system health mode (operational/degraded/critical) from existing
//   signals, manages escalation via orchestrateEscalation coordinator, enforces effect density
//   budgets, and controls audio. Uses recoverySequence for choreographed recovery transitions.
//   Pure reactive computation — no new API calls.
// Decision it drives: Every atmosphere-aware component reads healthMode + escalationLevel
//   to decide which visual effects to show. canFireEffect() prevents cognitive overload by
//   gating effect density. Recovery/failure transitions play audio cues so the operator
//   knows when to look at the dashboard.

import { signal, effect } from '@preact/signals';
import { connectionStatus, status } from './queue.js';
import { dlqCount, backendsData, backendsError, addToast } from './health.js';
import { playSfx, ShAudio } from 'superhot-ui';
import { orchestrateEscalation, recoverySequence, glitchText } from 'superhot-ui';

// ── Local effect density tracking ────────────────────────────────────────────
// Limits simultaneous visual effects to prevent cognitive overload.
// Max 3 active effects at once — each trackEffect() call returns a cleanup fn.
const MAX_CONCURRENT_EFFECTS = 3;
let _activeEffects = new Set();

function trackEffect(id) {
    _activeEffects.add(id);
    return () => { _activeEffects.delete(id); };
}

function isOverBudget() {
    return _activeEffects.size >= MAX_CONCURRENT_EFFECTS;
}

// ── Health mode ──────────────────────────────────────────────────────────────

// What it shows: Overall system health — operational (green), degraded (amber), or critical (red).
// Decision it drives: Layout-root data attribute drives CSS mantra/pulse; sidebar indicator color.
export const healthMode = signal('operational');

// What it shows: The previous health mode before the most recent transition.
// Decision it drives: Components compare prev vs current to detect recovery (degraded→operational)
//   or entering failure (operational→degraded).
export const prevHealthMode = signal('operational');

// ── Escalation ───────────────────────────────────────────────────────────────

// What it shows: How long the system has been in a non-operational state (0–3).
// Decision it drives: Level 0 = component-only effects; 1 = sidebar pulse; 2 = section mantra;
//   3 = layout-root mantra. Higher levels draw more attention.
export const escalationLevel = signal(0);

// What it shows: Which services are currently failing.
// Decision it drives: Sidebar/banner can enumerate specific failures rather than a generic alert.
export const failedServices = signal([]);

// ── Audio preference ─────────────────────────────────────────────────────────

const AUDIO_KEY = 'queue-audio';

function _loadAudioPref() {
    try {
        return localStorage.getItem(AUDIO_KEY) === 'true';
    } catch (_e) {
        return false;
    }
}

/**
 * Persist the user's audio preference and sync ShAudio.enabled.
 * @param {boolean} enabled
 */
export function setAudioEnabled(enabled) {
    ShAudio.enabled = !!enabled;
    try {
        localStorage.setItem(AUDIO_KEY, String(!!enabled));
    } catch (_e) {
        // localStorage unavailable — silent
    }
}

// ── Effect density + cooldown ────────────────────────────────────────────────

let _lastEffectTs = 0;
const EFFECT_COOLDOWN_MS = 300;

/**
 * Gate for firing a visual effect — checks superhot-ui budget AND 300ms cooldown.
 * Returns a cleanup function on success, or null if the effect should be suppressed.
 * @param {string} id — Unique effect identifier
 * @returns {(() => void) | null}
 */
export function canFireEffect(id) {
    const now = Date.now();
    if (isOverBudget()) return null;
    if (now - _lastEffectTs < EFFECT_COOLDOWN_MS) return null;
    _lastEffectTs = now;
    return trackEffect(id);
}

// ── Internal state ───────────────────────────────────────────────────────────

let _orchestrator = null;
let _disposeEffect = null;

/**
 * Compute health mode from current signal values.
 * Returns both mode and reasons — caller writes to signals so all updates
 * happen in the same location (avoids split-write timing issues).
 * @returns {{ mode: 'operational' | 'degraded' | 'critical', services: string[] }}
 */
function _computeHealthMode() {
    const failed = [];

    // Critical conditions
    if (connectionStatus.value === 'disconnected') {
        failed.push('connection');
    }

    const daemonState = status.value?.daemon_state;
    if (daemonState === 'offline' || daemonState === 'error') {
        failed.push('daemon');
    }

    const backends = backendsData.value;
    if (Array.isArray(backends) && backends.length > 0) {
        const allUnhealthy = backends.every(
            (b) => b.status === 'unreachable' || b.status === 'error' || b.healthy === false
        );
        if (allUnhealthy) {
            failed.push('all-backends');
        }
    }

    if (failed.length > 0) {
        return { mode: 'critical', services: failed };
    }

    // Degraded conditions
    const degradedReasons = [];

    if (Array.isArray(backends) && backends.length > 0) {
        const anyUnhealthy = backends.some(
            (b) => b.status === 'unreachable' || b.status === 'error' || b.healthy === false
        );
        if (anyUnhealthy) {
            degradedReasons.push('backend-unhealthy');
        }
    }

    if (backendsError.value) {
        degradedReasons.push('backends-fetch-error');
    }

    if (dlqCount.value > 0) {
        degradedReasons.push('dlq');
    }

    if (daemonState === 'paused') {
        degradedReasons.push('daemon-paused');
    }

    if (degradedReasons.length > 0) {
        return { mode: 'degraded', services: degradedReasons };
    }

    return { mode: 'operational', services: [] };
}

/**
 * Initialize the orchestrateEscalation coordinator.
 * Lazily deferred until the DOM is available (called from initAtmosphere).
 * Syncs escalationLevel signal with the timer's internal level on each advance.
 */
function _initOrchestrator() {
    if (_orchestrator) return;

    // Resolve surfaces from the live DOM — these selectors match app.jsx layout
    const layout = document.querySelector('.layout-root');
    const sidebar = document.querySelector('.layout-sidebar');
    const main = document.querySelector('.layout-main');

    _orchestrator = orchestrateEscalation({
        surfaces: {
            component: layout ? [layout] : [],
            sidebar: sidebar ? [sidebar] : [],
            section: main || undefined,
            layout: layout || undefined,
        },
        sectionMantra: 'DEGRADED',
        layoutMantra: 'SYSTEM CRITICAL',
        sounds: true,
        thresholds: [5000, 10000, 45000, 60000],
    });

    // Patch onEscalate to keep the escalationLevel signal in sync
    const origTimer = _orchestrator.timer;
    const origOnEscalate = origTimer.onEscalate;
    origTimer.onEscalate = (level, name) => {
        escalationLevel.value = level;
        origOnEscalate(level, name);
    };
    const origOnReset = origTimer.onReset;
    origTimer.onReset = () => {
        escalationLevel.value = 0;
        origOnReset();
    };
}

/**
 * Run the choreographed recovery sequence — glitch burst → border transition →
 * pulse stop → RESTORED toast. Uses superhot-ui recoverySequence utility.
 */
function _runRecovery() {
    const main = document.querySelector('.layout-main');
    recoverySequence({
        glitchFn: () => {
            if (main) glitchText(main, { duration: 200, intensity: 'medium' });
        },
        onBorderTransition: () => {
            if (main) {
                main.style.borderColor = 'var(--sh-phosphor)';
                setTimeout(() => { main.style.borderColor = ''; }, 600);
            }
        },
        onPulseStop: () => {
            const pulsing = document.querySelectorAll('[data-sh-effect="threat-pulse"]');
            pulsing.forEach(el => el.removeAttribute('data-sh-effect'));
        },
        onToast: () => {
            addToast('SYSTEM RESTORED', 'success');
        },
        delays: { afterGlitch: 200, afterBorder: 300, afterPulse: 200 },
    });
}

// ── Init / Dispose ───────────────────────────────────────────────────────────

/**
 * Start the atmosphere reactive computation and orchestrated escalation.
 * Call once from app.jsx on mount. Returns nothing — call disposeAtmosphere() to clean up.
 */
export function initAtmosphere() {
    // Guard against double-init (hot reload, strict mode) — dispose previous first
    if (_disposeEffect) {
        _disposeEffect();
        _disposeEffect = null;
    }
    if (_orchestrator) {
        _orchestrator.reset();
        _orchestrator = null;
    }

    // Load audio preference from localStorage
    ShAudio.enabled = _loadAudioPref();

    // Reactive computation: re-runs whenever connectionStatus, status, dlqCount,
    // backendsData, or backendsError change.
    _disposeEffect = effect(() => {
        const { mode: newMode, services } = _computeHealthMode();
        const currentMode = healthMode.peek();

        // Always update failedServices even if mode unchanged (reasons may differ)
        failedServices.value = services;

        if (newMode === currentMode) return;

        // Write both signals together so subscribers see a consistent snapshot
        prevHealthMode.value = currentMode;
        healthMode.value = newMode;

        // Lazy-init orchestrator on first transition (DOM must exist)
        _initOrchestrator();

        // State transition effects via orchestrateEscalation + recoverySequence
        if (currentMode !== 'operational' && newMode === 'operational') {
            // Recovery — choreographed sequence then reset orchestrator
            if (_orchestrator) _orchestrator.reset();
            _runRecovery();
            playSfx('complete');
        } else if (currentMode === 'operational' && newMode !== 'operational') {
            // Entering failure — start orchestrated escalation
            if (_orchestrator) _orchestrator.start();
            playSfx('error');
        } else {
            // Severity change within non-operational (e.g. degraded → critical)
            // Orchestrator continues its timeline — no restart needed
        }
    });
}

/**
 * Dispose all reactive subscriptions and orchestration timers. Call on unmount.
 */
export function disposeAtmosphere() {
    if (_disposeEffect) {
        _disposeEffect();
        _disposeEffect = null;
    }
    if (_orchestrator) {
        _orchestrator.stop();
        _orchestrator = null;
    }
    escalationLevel.value = 0;
}
