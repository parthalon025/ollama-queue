// Mock for stores/atmosphere.js — stubs all exports as no-ops/defaults.
module.exports = {
    healthMode: { value: 'operational', peek: () => 'operational' },
    prevHealthMode: { value: 'operational', peek: () => 'operational' },
    escalationLevel: { value: 0 },
    failedServices: { value: [] },
    canFireEffect: () => () => {},
    initAtmosphere: () => {},
    disposeAtmosphere: () => {},
    setAudioEnabled: () => {},
};
