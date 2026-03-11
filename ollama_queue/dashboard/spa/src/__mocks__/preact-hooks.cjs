// Minimal preact/hooks mock for jest — stubs used hooks
function useRef(init) { return { current: init ?? null }; }
function useEffect() {}
function useState(init) { return [typeof init === 'function' ? init() : init, () => {}]; }
function useCallback(fn) { return fn; }
function useMemo(fn) { return fn(); }
module.exports = { useRef, useEffect, useState, useCallback, useMemo };
