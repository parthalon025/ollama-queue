import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { evalActiveRun, fetchEvalRuns, fetchEvalVariants, fetchEvalSettings, startEvalPoll } from '../stores';
import ActiveRunProgress from '../components/eval/ActiveRunProgress.jsx';
import RunTriggerPanel from '../components/eval/RunTriggerPanel.jsx';
import RunHistoryTable from '../components/eval/RunHistoryTable.jsx';
// What it shows: The Eval Runs view — live run progress (if active), a collapsible
//   trigger panel for starting new runs, and the full run history table.
// Decision it drives: User sees what's running now, can start new runs, and
//   reviews past results to track which config is winning over time.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

// What it shows: Collapsible explainer card for new users — what eval does, the 4 pipeline
//   stages, how to start a run, and how to read the quality score.
// Decision it drives: Lets a first-time user understand the system without leaving the page,
//   then collapses out of the way for experienced users.
function EvalIntroCard() {
  const [open, setOpen] = useState(false);

  return (
    <div class="t-frame" style={{ borderLeft: '3px solid var(--accent)', padding: '0.75rem 1rem' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          background: 'none', border: 'none', cursor: 'pointer',
          width: '100%', textAlign: 'left',
          fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
          color: 'var(--text-primary)', fontWeight: 700,
        }}
        aria-expanded={open}
      >
        <span style={{ color: 'var(--accent)', fontSize: 'var(--type-label)' }}>{open ? '▼' : '▶'}</span>
        What is Eval? — how quality testing works
      </button>

      {open && (
        <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

          {/* What it does */}
          <div>
            <p style={{ margin: '0 0 0.35rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--text-primary)', fontWeight: 700 }}>
              What it does
            </p>
            <p style={{ margin: 0, fontSize: 'var(--type-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Eval automatically tests different AI model settings — called <strong>configurations</strong> — against a set of lessons you've collected.
              It asks the AI to generate principles from those lessons, then has a second AI (the "judge") score the results.
              The winner is the configuration that produces the most accurate, useful output.
            </p>
          </div>

          {/* Pipeline stages */}
          <div>
            <p style={{ margin: '0 0 0.35rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--text-primary)', fontWeight: 700 }}>
              The 4 stages of a test run
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {[
                { stage: 'Waiting', desc: 'The test is queued up, waiting for the AI to be free.' },
                { stage: 'Writing Principles', desc: 'The AI reads each lesson and writes a principle from it. This is the main work phase — it can take several minutes.' },
                { stage: 'Scoring Quality', desc: 'A second AI (the judge) reads the generated principles alongside the expected answers and scores each one for accuracy and usefulness.' },
                { stage: 'Done ✓', desc: 'Results are in. You\'ll see a quality score (F1) for each configuration — higher is better. The winner can be promoted to production.' },
              ].map(item => (
                <div key={item.stage} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                    color: 'var(--accent)', fontWeight: 700, whiteSpace: 'nowrap',
                    minWidth: '9rem', paddingTop: '0.1rem',
                  }}>
                    {item.stage}
                  </span>
                  <span style={{ fontSize: 'var(--type-body)', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {item.desc}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* How to start */}
          <div>
            <p style={{ margin: '0 0 0.35rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--text-primary)', fontWeight: 700 }}>
              How to start a new test run
            </p>
            <ol style={{ margin: 0, paddingLeft: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              {[
                'Click "Start a New Evaluation Run" below to open the setup panel.',
                'Choose which configurations to test (or leave all selected to compare them all).',
                'Pick a scheduling mode — "Batch" is fastest, "Opportunistic" is gentlest on the queue.',
                'Click "Start Test Run" — progress appears above automatically.',
              ].map((step, idx) => (
                <li key={idx} style={{ fontSize: 'var(--type-body)', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  {step}
                </li>
              ))}
            </ol>
            <p style={{ margin: '0.5rem 0 0', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              Advanced: <code>POST /api/eval/runs</code> with <code>{`{"variant_ids": [1,2,3], "scheduling_mode": "opportunistic"}`}</code>
            </p>
          </div>

          {/* How to interpret results */}
          <div>
            <p style={{ margin: '0 0 0.35rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--text-primary)', fontWeight: 700 }}>
              How to read the results
            </p>
            <p style={{ margin: 0, fontSize: 'var(--type-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              After a run finishes, each configuration gets a <strong>Quality Score</strong> (0–100%).
              Higher is better. 100% means the AI's output matched what was expected perfectly.
              Below 75% usually means the configuration needs tuning.
              Click "Use this config" on the winning run to make it active.
            </p>
            <p style={{ margin: '0.35rem 0 0', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              Technical: Quality Score = F1 = harmonic mean of precision (avoids wrong matches) and recall (catches right patterns).
            </p>
          </div>

        </div>
      )}
    </div>
  );
}

export default function EvalRuns() {
  // Read .value at top so Preact subscribes to the signal
  const activeRun = evalActiveRun.value;

  const terminalStatuses = ['complete', 'failed', 'cancelled'];
  const hasActiveRun = activeRun && !terminalStatuses.includes(activeRun.status);

  useEffect(() => {
    // Load data when view mounts
    fetchEvalRuns();
    fetchEvalVariants();
    fetchEvalSettings();
    // If an active run was restored from sessionStorage, restart polling so the
    // progress bar continues updating after a page reload.
    if (hasActiveRun) startEvalPoll(activeRun.run_id);
  }, []);

  return (
    <div class="flex flex-col gap-4 animate-page-enter">
      {/* Live progress panel — shown only when a run is active */}
      {hasActiveRun && <ActiveRunProgress />}

      {/* What is Eval? — collapsible intro for new users */}
      <EvalIntroCard />

      {/* Trigger panel — auto-collapsed when a run is active */}
      <RunTriggerPanel defaultCollapsed={hasActiveRun} />

      {/* Run history */}
      <RunHistoryTable />
    </div>
  );
}
