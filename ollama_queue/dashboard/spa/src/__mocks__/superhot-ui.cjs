// Minimal superhot-ui mock for Jest — stubs visual effect functions that require DOM/canvas.
// All functions are no-ops or return resolved promises so component tests can import freely.
module.exports = {
    glitchText: async () => {},
    shatterElement: () => () => {},
    applyFreshness: () => 'fresh',
    applyMantra: () => {},
    removeMantra: () => {},
    playSfx: () => {},
    ShAudio: { enabled: false },
    trackEffect: () => () => {},
    isOverBudget: () => false,
    activeEffectCount: () => 0,
    MAX_EFFECTS: 3,
    SHATTER_PRESETS: { toast: 4, cancel: 6, alert: 8, purge: 12 },
    setCrtMode: () => {},
    CRT_PRESETS: {},
    setCrtPreset: () => {},
};
