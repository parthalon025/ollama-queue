// What it shows: Nothing directly — pure logic hook that tracks loading/success/error state
//   for a single async action button.
// Decision it drives: Lets every action button show exactly what is happening — "Cancelling…",
//   "Run #12 started", "Cancel failed: already complete" — without duplicating state boilerplate.
import { useState } from 'preact/hooks';

export function useActionFeedback() {
  const [state, setState] = useState({ phase: 'idle', msg: '' });

  async function run(loadingLabel, fn, successLabel) {
    if (state.phase === 'loading') return;
    setState({ phase: 'loading', msg: loadingLabel });
    try {
      const result = await fn();
      const msg = typeof successLabel === 'function'
        ? successLabel(result)
        : (successLabel || 'Done');
      setState({ phase: 'success', msg });
      setTimeout(() => setState({ phase: 'idle', msg: '' }), 3000);
    } catch (e) {
      setState({ phase: 'error', msg: e.message || 'Failed' });
    }
  }

  return [state, run];
}
