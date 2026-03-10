import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
// What it shows: A 2-step getting-started guide for first-time eval setup.
//   Hidden once setup_complete=true is stored in eval settings.
// Decision it drives: Guides new users through connecting a data source
//   and running their first eval — so they reach a working state without reading docs.

import {
  evalSettings, evalRuns, evalSubTab,
  testDataSource, saveEvalSettings,
} from '../../stores';

// A single step row — shows a checkbox, title, detail, and action button
function Step({ number, complete, disabled, title, detail, actionLabel, onAction }) {
  return (
    <div class={`setup-step${complete ? ' complete' : ''}${disabled ? ' disabled' : ''}`}>
      <span class="setup-step__check" aria-hidden="true">{complete ? '☑' : '☐'}</span>
      <div class="setup-step__body">
        <p class="setup-step__title">
          {number}. {title}
        </p>
        {detail && <p class="setup-step__detail">{detail}</p>}
        {!complete && !disabled && actionLabel && (
          <button
            type="button"
            class="t-btn t-btn-secondary setup-step__action"
            onClick={onAction}
            disabled={disabled}
          >
            {actionLabel}
          </button>
        )}
      </div>
    </div>
  );
}

export default function SetupChecklist() {
  // Read signals at top of render body for Preact subscription
  const settings = evalSettings.value;
  const runs     = evalRuns.value;

  const [step1Status, setStep1Status] = useState(null); // null | 'testing' | 'ok' | 'fail'
  const [step1Detail, setStep1Detail] = useState('');

  // Step completion: 2 real, automatable gates
  const step1Complete = step1Status === 'ok';
  const step2Complete = step1Complete && runs.length > 0;

  // Auto-test on mount (step 1 auto-advances if datasource is reachable)
  useEffect(() => {
    async function autoTest() {
      try {
        setStep1Status('testing');
        const result = await testDataSource();
        if (result && result.ok) {
          setStep1Status('ok');
          setStep1Detail(`${result.item_count ?? '?'} lessons · ${result.cluster_count ?? '?'} groups · tested just now`);
        } else {
          setStep1Status('fail');
          setStep1Detail('Connection failed — check data source URL in settings below.');
        }
      } catch (err) {
        console.error('autoTest failed:', err);
        setStep1Status('fail');
        setStep1Detail('Could not reach data source — check URL and try again.');
      }
    }
    autoTest();
  }, []);

  // When both steps complete, mark setup done and hide
  useEffect(() => {
    if (step2Complete) {
      saveEvalSettings({ 'eval.setup_complete': true }).catch(() => {});
    }
  }, [step2Complete]);

  // If setup already complete, render nothing
  if (settings['eval.setup_complete'] === true || settings['eval.setup_complete'] === 'true') {
    return null;
  }

  async function handleStep1Action() {
    setStep1Status('testing');
    setStep1Detail('Testing…');
    try {
      const result = await testDataSource();
      if (result && result.ok) {
        setStep1Status('ok');
        setStep1Detail(`${result.item_count ?? '?'} lessons · ${result.cluster_count ?? '?'} groups · ${result.response_ms ?? '?'}ms`);
      } else {
        setStep1Status('fail');
        setStep1Detail('Connection failed — check URL in settings below.');
      }
    } catch (err) {
      console.error('handleStep1Action failed:', err);
      setStep1Status('fail');
      setStep1Detail('Could not reach data source.');
    }
  }

  return (
    <div class="setup-checklist t-frame" data-label="Get Started — 2 Steps to Your First Quality Test">
      <Step
        number={1}
        complete={step1Complete}
        disabled={false}
        title="Connect a data source"
        detail={
          step1Status === 'testing'
            ? 'Testing connection…'
            : step1Complete
              ? `✓ Connected · ${step1Detail}`
              : step1Detail || 'Connect your lesson library so evals have something to test.'
        }
        actionLabel={step1Status === 'testing' ? 'Testing…' : 'Test connection'}
        onAction={handleStep1Action}
      />
      <Step
        number={2}
        complete={step2Complete}
        disabled={!step1Complete}
        title="Start your first quality test"
        detail={
          !step1Complete
            ? 'Complete step 1 first.'
            : runs.length === 0
              ? 'Start your first test to see which configurations perform best.'
              : `${runs.length} run${runs.length !== 1 ? 's' : ''} completed.`
        }
        actionLabel="Start first test →"
        onAction={() => { evalSubTab.value = 'runs'; }}
      />
    </div>
  );
}
