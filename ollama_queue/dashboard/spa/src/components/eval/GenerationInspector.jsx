import { h } from 'preact';
import { EVAL_TRANSLATIONS } from './translations.js';
// What it shows: L3 deep inspection of a single scored pair — the principle text,
//   target lesson, score grid, scorer reasoning (captured <think> blocks), and
//   a link to the originating queue job.
// Decision it drives: User can see exactly why a score was awarded and override it
//   if the scorer made an obvious error.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

function ScoreCell({ label, score, override }) {
  const effective = override ?? score;
  return (
    <td style={{ padding: '4px 8px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}>
      <span style={{ color: effective >= 4 ? 'var(--status-healthy)' : effective >= 3 ? 'var(--text-primary)' : 'var(--status-error)' }}>
        {effective ?? '—'}/5
      </span>
      {override != null && (
        <span style={{ color: 'var(--accent)', fontSize: '0.6rem', marginLeft: '2px' }}>✎</span>
      )}
    </td>
  );
}

export default function GenerationInspector({ result }) {
  // result: eval_results row with principle, judge_reasoning, scores, queue_job_id, etc.
  if (!result) return null;

  const {
    principle,
    judge_reasoning,
    score_transfer,
    score_precision,
    score_action,
    override_score_transfer,
    override_score_precision,
    override_score_action,
    override_reason,
    queue_job_id,
    error,
    generation_time_s,
  } = result;

  return (
    <div class="eval-inspector" style={{ padding: '0.75rem 1rem', background: 'var(--bg-base)', borderTop: '1px solid var(--border-subtle)' }}>

      {/* Principle text */}
      <div style={{ marginBottom: '0.75rem' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>
          Principle extracted
        </div>
        <div style={{ color: 'var(--text-primary)', fontSize: 'var(--type-body)', lineHeight: '1.5' }}>
          {principle ?? <em style={{ color: 'var(--text-tertiary)' }}>No principle generated</em>}
        </div>
      </div>

      {/* Score grid */}
      <div style={{ marginBottom: '0.75rem' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>
          Scores
        </div>
        <table class="eval-metrics-table" style={{ width: 'auto' }}>
          <thead>
            <tr>
              <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'left' }}>
                {EVAL_TRANSLATIONS.recall.label}
              </th>
              <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'left' }}>
                {EVAL_TRANSLATIONS.precision.label}
              </th>
              <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'left' }}>
                {EVAL_TRANSLATIONS.actionability.label}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <ScoreCell score={score_transfer} override={override_score_transfer} />
              <ScoreCell score={score_precision} override={override_score_precision} />
              <ScoreCell score={score_action} override={override_score_action} />
            </tr>
          </tbody>
        </table>
        {override_reason && (
          <div style={{ marginTop: '4px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)' }}>
            Override reason: {override_reason}
          </div>
        )}
      </div>

      {/* Scorer reasoning */}
      {judge_reasoning && (
        <div style={{ marginBottom: '0.75rem' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>
            Scorer reasoning
          </div>
          <div style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius)',
            padding: '0.5rem 0.75rem',
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--type-label)',
            color: 'var(--text-secondary)',
            whiteSpace: 'pre-wrap',
            maxHeight: '200px',
            overflowY: 'auto',
          }}>
            {judge_reasoning}
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{ marginBottom: '0.75rem', color: 'var(--status-error)', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}>
          Error: {error}
        </div>
      )}

      {/* Metadata row */}
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
        {generation_time_s != null && (
          <span>Generated in {generation_time_s.toFixed(1)}s</span>
        )}
        {queue_job_id && (
          <span>Queue job: #{queue_job_id}</span>
        )}
      </div>
    </div>
  );
}
