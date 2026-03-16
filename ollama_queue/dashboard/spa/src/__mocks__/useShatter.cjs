// Mock for useShatter hook — returns a no-op ref + fire function.
const { useRef } = require('./preact-hooks.cjs');
module.exports = {
    useShatter: (tier = 'routine') => [{ current: null }, () => {}],
};
