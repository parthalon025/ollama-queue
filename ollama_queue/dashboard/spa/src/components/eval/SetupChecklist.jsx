import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
// What it shows: A 4-step getting-started guide for first-time eval setup.
//   Hidden once setup_complete=true is stored in eval settings.
// Decision it drives: Guides new users through connecting a data source,
//   verifying models, creating configs, and running their first eval — so
//   they reach a working state without reading docs.

import {
  evalSettings, evalVariants, evalRuns, evalSubTab,
  testDataSource, saveEvalSettings, currentTab,
} from '../../store.js';

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
  const variants = evalVariants.value;
  const runs     = evalRuns.value;

  const [step1Status, setStep1Status] = useState(null); // null | 'testing' | 'ok' | 'fail'
  const [step1Detail, setStep1Detail] = useState('');

  // If setup already complete, render nothing
  if (settings['eval.setup_complete'] === true || settings['eval.setup_complete'] === 'true') {
    return null;
  }

  // Step 1: data source connected
  const step1Complete = step1Status === 'ok';

  // Step 2: models available — check if all unique variant models appear in the models list
  // We don't have the full models signal here; treat as complete if step1 is done and
  // user has clicked "Check models" (handled by navigation). Default: incomplete.
  const step2Complete = false; // User must navigate to Models tab and confirm manually

  // Step 3: user has created configs beyond system defaults (more than 5 variants)
  const step3Complete = step2Complete && variants.length > 5;

  // Step 4: at least one eval run exists
  const step4Complete = step3Complete && runs.length > 0;

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
      } catch {
        setStep1Status('fail');
        setStep1Detail('Could not reach data source — check URL and try again.');
      }
    }
    autoTest();
  }, []);

  // When all 4 steps complete, mark setup done and hide
  useEffect(() => {
    if (step4Complete) {
      saveEvalSettings({ 'eval.setup_complete': true }).catch(() => {});
    }
  }, [step4Complete]);

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
    } catch {
      setStep1Status('fail');
      setStep1Detail('Could not reach data source.');
    }
  }

  function handleStep2Action() {
    // Navigate to Models tab
    currentTab.value = 'models';
  }

  function handleStep3Action() {
    // Navigate to Configurations sub-tab
    evalSubTab.value = 'configurations';
  }

  function handleStep4Action() {
    // Navigate to Runs sub-tab
    evalSubTab.value = 'runs';
  }

  return (
    <div class="setup-checklist t-frame" data-label="Getting started with eval">
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
        title="Verify AI models are available"
        detail={
          !step1Complete
            ? 'Complete step 1 first.'
            : 'Confirm that your variant models are loaded in Ollama.'
        }
        actionLabel="Check models"
        onAction={handleStep2Action}
      />
      <Step
        number={3}
        complete={step3Complete}
        disabled={!step2Complete}
        title="Create configurations to test"
        detail={
          !step2Complete
            ? 'Complete step 2 first.'
            : variants.length <= 5
              ? 'Add at least one custom configuration beyond the system defaults.'
              : `${variants.length} configurations ready.`
        }
        actionLabel="Go to Configurations →"
        onAction={handleStep3Action}
      />
      <Step
        number={4}
        complete={step4Complete}
        disabled={!step3Complete}
        title="Run your first evaluation"
        detail={
          !step3Complete
            ? 'Complete step 3 first.'
            : runs.length === 0
              ? 'Start your first eval run to begin collecting quality data.'
              : `${runs.length} run${runs.length !== 1 ? 's' : ''} completed.`
        }
        actionLabel="Start first run →"
        onAction={handleStep4Action}
      />
    </div>
  );
}
