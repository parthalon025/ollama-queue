import { signal } from '@preact/signals';
// What it shows: 4 scheduling mode radio options with conditional sub-inputs.
//   Full speed / One at a time / Fill open slots / Scheduled.
// Decision it drives: How aggressively the eval run uses the queue.
//   Sub-inputs rendered via signal-driven show/hide (not CSS display:none)
//   so stale hidden values cannot be included in form submission.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

// Sub-field signals declared at module level so they survive parent re-renders.
// Creating signal() inside a render body creates a new object every call,
// silently discarding any user input when the parent re-renders.
const fillLimitType = signal('time');  // 'time' | 'count' | 'both'
const fillHours = signal(2);
const fillRuns = signal(5);
const scheduledAt = signal('');

export default function SchedulingModeSelector({ value, onChange }) {
  // value: 'batch' | 'opportunistic' | 'fill-open-slots' | 'scheduled'
  // onChange: (mode, subFields) => void

  function handleModeChange(mode) {
    const sub = buildSubFields(mode);
    onChange(mode, sub);
  }

  function handleFillLimitChange(type) {
    fillLimitType.value = type;
    onChange('fill-open-slots', buildSubFields('fill-open-slots'));
  }

  function buildSubFields(mode) {
    if (mode === 'fill-open-slots') {
      return {
        fill_limit_type: fillLimitType.value,
        max_time_s: fillLimitType.value !== 'count' ? fillHours.value * 3600 : null,
        max_runs: fillLimitType.value !== 'time' ? fillRuns.value : null,
      };
    }
    if (mode === 'scheduled') {
      return { scheduled_at: scheduledAt.value };
    }
    return {};
  }

  const limitType = fillLimitType.value;

  return (
    <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
      <legend style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--type-label)',
        color: 'var(--text-secondary)',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        marginBottom: '0.5rem',
      }}>
        How should this run use the queue?
      </legend>

      {/* Full speed */}
      <label class="eval-mode-option">
        <input
          type="radio"
          name="run_mode"
          value="batch"
          checked={value === 'batch'}
          onChange={() => handleModeChange('batch')}
        />
        <span class="eval-mode-label">Full speed</span>
        <span class="eval-mode-desc">Submit all jobs now. Fastest option.</span>
      </label>

      {/* One at a time */}
      <label class="eval-mode-option">
        <input
          type="radio"
          name="run_mode"
          value="opportunistic"
          checked={value === 'opportunistic'}
          onChange={() => handleModeChange('opportunistic')}
        />
        <span class="eval-mode-label">One at a time</span>
        <span class="eval-mode-desc">One job at a time. Only when queue is idle. Your other work is never delayed.</span>
      </label>

      {/* Fill open slots */}
      <label class="eval-mode-option">
        <input
          type="radio"
          name="run_mode"
          value="fill-open-slots"
          checked={value === 'fill-open-slots'}
          onChange={() => handleModeChange('fill-open-slots')}
        />
        <span class="eval-mode-label">Fill open slots</span>
        <span class="eval-mode-desc">Use all available slots. Builds trend data automatically.</span>
      </label>

      {/* Fill sub-inputs — only rendered when fill-open-slots is selected */}
      {value === 'fill-open-slots' && (
        <div class="eval-mode-subfields">
          <div style={{ marginBottom: '0.4rem', color: 'var(--text-secondary)', fontSize: 'var(--type-label)' }}>
            Keep running until:
          </div>
          <label class="eval-mode-sub-option">
            <input
              type="radio"
              name="fill_limit"
              value="time"
              checked={limitType === 'time'}
              onChange={() => handleFillLimitChange('time')}
            />
            Time limit{' '}
            <input
              type="number"
              min="1"
              max="72"
              value={fillHours.value}
              onInput={e => { fillHours.value = parseInt(e.target.value) || 2; onChange('fill-open-slots', buildSubFields('fill-open-slots')); }}
              class="t-input eval-num-input"
              disabled={limitType !== 'time' && limitType !== 'both'}
            />
            {' '}hrs
          </label>
          <label class="eval-mode-sub-option">
            <input
              type="radio"
              name="fill_limit"
              value="count"
              checked={limitType === 'count'}
              onChange={() => handleFillLimitChange('count')}
            />
            Run count{' '}
            <input
              type="number"
              min="1"
              max="100"
              value={fillRuns.value}
              onInput={e => { fillRuns.value = parseInt(e.target.value) || 5; onChange('fill-open-slots', buildSubFields('fill-open-slots')); }}
              class="t-input eval-num-input"
              disabled={limitType !== 'count' && limitType !== 'both'}
            />
            {' '}runs
          </label>
          <label class="eval-mode-sub-option">
            <input
              type="radio"
              name="fill_limit"
              value="both"
              checked={limitType === 'both'}
              onChange={() => handleFillLimitChange('both')}
            />
            Both — whichever comes first
          </label>
        </div>
      )}

      {/* Scheduled */}
      <label class="eval-mode-option">
        <input
          type="radio"
          name="run_mode"
          value="scheduled"
          checked={value === 'scheduled'}
          onChange={() => handleModeChange('scheduled')}
        />
        <span class="eval-mode-label">Scheduled</span>
        <span class="eval-mode-desc">Start at a specific time.</span>
      </label>

      {/* Scheduled sub-input — only rendered when scheduled is selected */}
      {value === 'scheduled' && (
        <div class="eval-mode-subfields">
          <label style={{ color: 'var(--text-secondary)', fontSize: 'var(--type-label)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            Start at:
            <input
              type="datetime-local"
              class="t-input"
              style={{ padding: '4px 8px', fontSize: 'var(--type-label)' }}
              value={scheduledAt.value}
              onInput={e => { scheduledAt.value = e.target.value; onChange('scheduled', { scheduled_at: e.target.value }); }}
            />
          </label>
        </div>
      )}
    </fieldset>
  );
}
