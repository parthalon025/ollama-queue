// Jest global setup — mirrors what esbuild's inject: ['./src/preact-shim.js'] does.
// esbuild makes h and Fragment available globally in the bundle without needing
// an explicit import in each file. This setup file does the same for the Jest
// test environment, so components that have no explicit import { h } from 'preact'
// still work correctly when Babel transforms JSX with pragma: 'h'.
const { h, Fragment } = require('./preact.cjs');
global.h = h;
global.Fragment = Fragment;
