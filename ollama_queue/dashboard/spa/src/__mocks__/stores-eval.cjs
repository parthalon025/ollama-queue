// Minimal stores/eval.js mock for jest — stubs eval signals and functions.
const signal = (v) => ({ value: v });
module.exports = {
    evalActiveRun: signal(null),
    cancelEvalRun: jest.fn(),
};
