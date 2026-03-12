// Minimal stores/eval.js mock for jest — stubs eval signals and functions.
const signal = (v) => ({ value: v });
const computed = (fn) => ({ get value() { return fn(); } });
module.exports = {
    evalActiveRun: signal(null),
    evalWinner: signal(null),
    evalSubTab: signal('runs'),
    focusVariantId: signal(null),
    cancelEvalRun: jest.fn().mockResolvedValue({ ok: true }),
};
