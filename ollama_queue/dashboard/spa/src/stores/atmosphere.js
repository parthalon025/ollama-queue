// What it does: Derives system health mode (operational/degraded/critical) from existing
//   signals, manages escalation timers, enforces effect density budgets, and controls audio.
//   Pure reactive computation — no new API calls.
// Decision it drives: Every atmosphere-aware component reads healthMode + escalationLevel
//   to decide which visual effects to show. canFireEffect() prevents cognitive overload by
//   gating effect density. Recovery/failure transitions play audio cues so the operator
//   knows when to look at the dashboard.

import { signal, effect } from '@preact/signals';
import { connectionStatus, status } from './queue.js';
import { dlqCount, backendsData, backendsError } from './health.js';
import { trackEffect, isOverBudget } from 'superhot-ui';
import { playSfx, ShAudio } from 'superhot-ui';

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

let _escalationTimers = [];
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
 * Clear all escalation timers and reset level to 0.
 */
function _clearEscalation() {
    for (const t of _escalationTimers) clearTimeout(t);
    _escalationTimers = [];
    escalationLevel.value = 0;
}

/**
 * Start the escalation timeline:
 *   0s  → level 0 (component effects only)
 *   5s  → level 1 (sidebar pulse)
 *  15s  → level 2 (section mantra)
 *  60s  → level 3 (layout-root mantra)
 */
function _startEscalation() {
    _clearEscalation();
    // Already at level 0 by default after clear

    _escalationTimers.push(
        setTimeout(() => { escalationLevel.value = 1; }, 5000)
    );
    _escalationTimers.push(
        setTimeout(() => { escalationLevel.value = 2; }, 15000)
    );
    _escalationTimers.push(
        setTimeout(() => { escalationLevel.value = 3; }, 60000)
    );
}

// ── Init / Dispose ───────────────────────────────────────────────────────────

/**
 * Start the atmosphere reactive computation and escalation timers.
 * Call once from app.jsx on mount. Returns nothing — call disposeAtmosphere() to clean up.
 */
export function initAtmosphere() {
    // Guard against double-init (hot reload, strict mode) — dispose previous first
    if (_disposeEffect) {
        _disposeEffect();
        _disposeEffect = null;
    }
    _clearEscalation();

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

        // State transition audio cues
        if (currentMode !== 'operational' && newMode === 'operational') {
            // Recovery
            _clearEscalation();
            playSfx('complete');
        } else if (currentMode === 'operational' && newMode !== 'operational') {
            // Entering failure
            _startEscalation();
            playSfx('error');
        } else {
            // Severity change within non-operational (e.g. degraded → critical)
            // Restart escalation timeline from current point — don't reset
        }
    });
}

/**
 * Dispose all reactive subscriptions and timers. Call on unmount.
 */
export function disposeAtmosphere() {
    if (_disposeEffect) {
        _disposeEffect();
        _disposeEffect = null;
    }
    _clearEscalation();
}
