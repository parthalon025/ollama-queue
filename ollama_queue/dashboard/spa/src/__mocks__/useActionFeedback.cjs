// Minimal useActionFeedback hook mock for jest.
module.exports = {
    useActionFeedback: () => [{ phase: 'idle', msg: '' }, jest.fn()],
};
