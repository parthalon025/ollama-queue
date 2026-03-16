// src/pages/Consumers.jsx
// What it shows: All detected Ollama-calling services with their patch status, streaming risk,
//   request counts, and intercept mode control.
// Decision it drives: User decides which services to route through the queue,
//   applies patches, and optionally enables system-wide iptables interception.
import { useEffect } from 'preact/hooks';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import {
  consumers, consumersScanning,
  fetchConsumers, scanConsumers,
  fetchInterceptStatus, interceptStatus,
  enableIntercept, disableIntercept,
} from '../stores';
import { ConsumerRow } from '../components/consumers/ConsumerRow.jsx';
import { ShPageBanner, ShEmptyState } from 'superhot-ui/preact';
import { TAB_CONFIG } from '../config/tabs.js';

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
            onClick={() => run('DISABLING', disableIntercept, () => 'INTERCEPT DISABLED')}
            disabled={fb.phase === 'loading'}
          >
            {fb.phase === 'loading' ? fb.msg : 'Disable intercept'}
          </button>
        ) : (
          <button
            class={`action-fb--${fb.phase}`}
            onClick={() => run('ENABLING', enableIntercept, () => 'INTERCEPT ACTIVE')}
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

export default function Consumers() {
  const _tab = TAB_CONFIG.find(t => t.id === 'consumers');
  const [scanFb, runScan] = useActionFeedback();

  useEffect(() => { fetchConsumers(); }, []);

  const list = consumers.value;
  const newlyDiscovered = list.filter(consumer => consumer.status === 'discovered');
  const showWizard = newlyDiscovered.length > 0 && list.every(consumer => consumer.status === 'discovered');

  return (
    <div class="consumers-page sh-stagger-children">
      <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />

      {/* What it shows: A plain-language explanation of what consumers are and how the
          scanner works. Decision it drives: User understands why services appear here
          and what their options are (patch, ignore, or use intercept mode). */}
      <p style={{
        fontSize: 'var(--type-sm)',
        color: 'var(--text-secondary)',
        margin: '0 0 1rem 0',
        lineHeight: 1.6,
      }}>
        Consumers are services on this machine that talk directly to Ollama (port 11434)
        and bypass the queue. The scanner detects them automatically. You can patch their
        config to route through the queue, or mark them as ignored if they&apos;re intentional.
      </p>

      <InterceptBanner />

      <div class="consumers-header">
        <h2 style="margin:0">Consumers</h2>
        <button
          class={`action-fb--${scanFb.phase}`}
          onClick={() => runScan('SCANNING', scanConsumers, () => `FOUND ${consumers.value.length} CONSUMER(S)`)}
          disabled={consumersScanning.value || scanFb.phase === 'loading'}
        >
          {scanFb.phase === 'loading' ? 'Scanning…' : 'Scan Now'}
        </button>
      </div>

      {scanFb.phase === 'error' && <div class="action-fb--error">{scanFb.msg}</div>}

      {showWizard && (
        <div class="consumers-wizard-banner">
          <strong>{newlyDiscovered.length} service{newlyDiscovered.length > 1 ? 's' : ''} detected calling Ollama directly.</strong>
          {' '}Review below and include or ignore each one.
        </div>
      )}

      {list.length === 0 ? (
        <ShEmptyState mantra="DARK" hint="scan to detect services" />
      ) : (
        <table class="consumers-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Streaming?</th>
              <th>Requests</th>
              <th>Last Seen</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {list.map(consumer => <ConsumerRow key={consumer.id} consumer={consumer} />)}
          </tbody>
        </table>
      )}
    </div>
  );
}
