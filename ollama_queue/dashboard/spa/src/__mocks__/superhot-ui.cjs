// Minimal superhot-ui mock for Jest — stubs visual effect functions that require DOM/canvas.
// All functions are no-ops or return resolved promises so component tests can import freely.
module.exports = {
    glitchText: async () => {},
    shatterElement: () => () => {},
    applyFreshness: () => 'fresh',
    applyMantra: () => {},
    removeMantra: () => {},
};
