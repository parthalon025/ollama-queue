import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import {
  fetchInterceptStatus, interceptStatus,
  enableIntercept, disableIntercept
} from '../store.js';

// What it shows: System-wide iptables intercept mode status + enable/disable toggle.
// Decision it drives: Lets user activate comprehensive Ollama MITM that catches
//   hardcoded URLs env-var patching can't reach.
function InterceptBanner() {
  const [fb, run] = useActionFeedback();
  const status = interceptStatus.value;

  useEffect(() => { fetchInterceptStatus(); }, []);

  return (
    <div class={`intercept-banner ${status.enabled ? 'intercept-banner--active' : ''}`}>
      <div class="intercept-banner__info">
        <strong>Intercept Mode</strong>
        <span>Redirect ALL :11434 traffic through queue — catches hardcoded URLs
          that env-var patching can&apos;t reach. Streaming fully supported.</span>
        <span class="intercept-badge">
          {status.enabled ? '● Active' : '○ Disabled'}
          {status.enabled && !status.rule_present && ' ⚠ Rule missing — re-enable'}
        </span>
      </div>
      <div class="intercept-banner__action">
        {status.enabled ? (
          <button
            class={`action-fb--${fb.phase}`}
            onClick={() => run('Disabling…', disableIntercept, () => 'Intercept disabled')}
            disabled={fb.phase === 'loading'}
          >
            {fb.phase === 'loading' ? fb.msg : 'Disable intercept'}
          </button>
        ) : (
          <button
            class={`action-fb--${fb.phase}`}
            onClick={() => run('Enabling…', enableIntercept, () => 'Intercept active')}
            disabled={fb.phase === 'loading'}
          >
            {fb.phase === 'loading' ? fb.msg : 'Enable intercept mode'}
          </button>
        )}
        {fb.phase === 'error' && <span class="action-fb--error">{fb.msg}</span>}
        <small>Requires sudo · Linux only</small>
      </div>
    </div>
  );
}

// What it shows: Consumers tab — detected Ollama consumers, patch status, intercept control.
// Decision it drives: User decides which services to route through the queue and whether
//   to enable system-wide iptables interception.
export default function Consumers() {
  return (
    <div class="consumers-page" style="padding: 1rem;">
      <InterceptBanner />
      <p style="color: var(--text-secondary); margin-top: 1rem;">
        Consumer detection coming soon — scanner will list services using Ollama.
      </p>
    </div>
  );
}
