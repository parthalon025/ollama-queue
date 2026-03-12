// Minimal @preact/signals mock for jest — stubs signal primitives used in components.
// useSignal is a jest.fn() so tests can call mockReturnValueOnce() for state-specific tests.
function _defaultUseSignal(init) {
    let _value = init;
    return {
        get value() { return _value; },
        set value(v) { _value = v; },
    };
}
const useSignal = jest.fn(_defaultUseSignal);
function signal(init) { return _defaultUseSignal(init); }
function computed(fn) { return { get value() { return fn(); } }; }
function effect() { return () => {}; }
module.exports = { useSignal, signal, computed, effect };
