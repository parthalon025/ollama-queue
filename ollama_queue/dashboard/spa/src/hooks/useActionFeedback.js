// What it shows: Nothing directly — pure logic hook that tracks loading/success/error state
//   for a single async action button. Fires confirmAction() on success for glitch-burst
//   feedback so the operator knows the system received the command.
// Decision it drives: Lets every action button show exactly what is happening — "Cancelling…",
//   "Run #12 started", "Cancel failed: already complete" — without duplicating state boilerplate.
import { useState, useRef, useEffect } from 'preact/hooks';
import { confirmAction } from 'superhot-ui';

export function useActionFeedback() {
  const [state, setState] = useState({ phase: 'idle', msg: '' });
  const timerRef = useRef(null);
  const targetRef = useRef(null);

  // Clear any pending timeout on unmount to prevent setState on unmounted component
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  async function run(loadingLabel, fn, successLabel) {
    if (state.phase === 'loading') return;
    setState({ phase: 'loading', msg: loadingLabel });
    try {
      const result = await fn();
      const msg = typeof successLabel === 'function'
        ? successLabel(result)
        : (successLabel || 'Done');
      setState({ phase: 'success', msg });

      // Fire confirmAction glitch burst + SFX on the nearest action element
      confirmAction(targetRef.current, { sound: 'complete', intensity: 'low' });

      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        setState({ phase: 'idle', msg: '' });
      }, 3000);
    } catch (e) {
      setState({ phase: 'error', msg: e.message || 'Failed' });
    }
  }

  return [state, run, targetRef];
}
