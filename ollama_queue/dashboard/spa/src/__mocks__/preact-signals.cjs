// Minimal @preact/signals mock for jest — stubs signal primitives used in components.
// useSignal returns a plain object with a .value property to satisfy signal reads in tests.
function useSignal(init) {
    let _value = init;
    return {
        get value() { return _value; },
        set value(v) { _value = v; },
    };
}
function signal(init) { return useSignal(init); }
function computed(fn) { return { get value() { return fn(); } }; }
function effect() { return () => {}; }
module.exports = { useSignal, signal, computed, effect };
