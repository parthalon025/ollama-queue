import { h } from 'preact';
import { useState } from 'preact/hooks';
// What it shows: Numeric eval configuration fields — how much data each run
//   generates and what quality thresholds trigger promotion.
// Decision it drives: User tunes the quantity/quality trade-off. Higher
//   per_cluster = more data per run, slower throughput. Higher f1_threshold =
//   harder to promote a variant automatically.

import { evalSettings, saveEvalSettings } from '../../store.js';
import { EVAL_TRANSLATIONS } from './translations.js';
import ModelSelect from '../ModelSelect.jsx';

const FIELD_DEFS = [
  {
    key:      'eval.per_cluster',
    transKey: 'per_cluster',
    type:     'number',
    min:      1,
    max:      20,
    step:     1,
    parse:    parseInt,
    validate: v => v >= 1 && v <= 20 ? '' : 'Must be 1–20',
    default:  4,
  },
  {
    key:      'eval.error_budget',
    transKey: 'error_budget',
    type:     'number',
    min:      0.0,
    max:      1.0,
    step:     0.05,
    parse:    parseFloat,
    validate: v => v >= 0.0 && v <= 1.0 ? '' : 'Must be 0.0–1.0',
    default:  0.3,
  },
  {
    key:      'eval.f1_threshold',
    transKey: 'f1_threshold',
    type:     'number',
    min:      0.0,
    max:      1.0,
    step:     0.05,
    parse:    parseFloat,
    validate: v => v >= 0.0 && v <= 1.0 ? '' : 'Must be 0.0–1.0',
    default:  0.75,
  },
  {
    key:      'eval.stability_window',
    transKey: 'stability_window',
    type:     'number',
    min:      1,
    max:      20,
    step:     1,
    parse:    parseInt,
    validate: v => v >= 1 && v <= 20 ? '' : 'Must be 1–20',
    default:  3,
  },
];

// What it shows: Auto-promote toggle and minimum improvement threshold.
// Decision it drives: User opts into automatic promotion and sets the bar for how much
//   better a variant must be before it replaces the current production config.
const TOGGLE_DEFS = [
  {
    key:      'eval.auto_promote',
    transKey: 'auto_promote',
    default:  false,
  },
];

const IMPROVEMENT_DEFS = [
  {
    key:      'eval.auto_promote_min_improvement',
    transKey: 'auto_promote_min_improvement',
    type:     'number',
    min:      0.0,
    max:      1.0,
    step:     0.01,
    parse:    parseFloat,
    validate: v => v >= 0.0 && v <= 1.0 ? '' : 'Must be 0.0–1.0',
    default:  0.05,
  },
];

export default function GeneralSettings() {
  // Read .value at top of body to subscribe to signal changes
  const settings = evalSettings.value;

  // Initialise local form state from signal values
  const [values, setValues] = useState(() => {
    const init = {};
    FIELD_DEFS.forEach(def => {
      init[def.key] = settings[def.key] != null ? def.parse(settings[def.key]) : def.default;
    });
    TOGGLE_DEFS.forEach(def => {
      init[def.key] = settings[def.key] ?? def.default;
    });
    IMPROVEMENT_DEFS.forEach(def => {
      init[def.key] = settings[def.key] != null ? def.parse(settings[def.key]) : def.default;
    });
    return init;
  });

  const [errors,    setErrors]    = useState({});
  const [saving,    setSaving]    = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveOk,    setSaveOk]    = useState(false);
  const [analysisModel, setAnalysisModel] = useState(settings['eval.analysis_model'] ?? '');

  function handleChange(key, rawValue, def) {
    const parsed = def.parse(rawValue);
    const errMsg = isNaN(parsed) ? 'Must be a number' : def.validate(parsed);
    if (!isNaN(parsed)) setValues(prev => ({ ...prev, [key]: parsed }));
    setErrors(prev => ({ ...prev, [key]: errMsg }));
  }

  async function handleSave() {
    // All-or-nothing: validate all fields first
    const newErrors = {};
    let anyError = false;
    FIELD_DEFS.forEach(def => {
      const msg = def.validate(values[def.key]);
      if (msg) { newErrors[def.key] = msg; anyError = true; }
    });
    IMPROVEMENT_DEFS.forEach(def => {
      const msg = def.validate(values[def.key]);
      if (msg) { newErrors[def.key] = msg; anyError = true; }
    });
    setErrors(newErrors);
    if (anyError) return;

    setSaving(true);
    setSaveError('');
    setSaveOk(false);
    try {
      const payload = {};
      FIELD_DEFS.forEach(def => { payload[def.key] = values[def.key]; });
      TOGGLE_DEFS.forEach(def => { payload[def.key] = values[def.key]; });
      IMPROVEMENT_DEFS.forEach(def => { payload[def.key] = values[def.key]; });
      payload['eval.analysis_model'] = analysisModel;
      await saveEvalSettings(payload);
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
    } catch (err) {
      setSaveError(err.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  const T = EVAL_TRANSLATIONS;

  return (
    <div class="eval-settings-form t-frame" data-label="General settings">
      {FIELD_DEFS.map(def => {
        const trans = T[def.transKey] || { label: def.transKey, tooltip: null };
        return (
          <label key={def.key} class="eval-settings-label">
            <span>
              {trans.label}
              {trans.tooltip && (
                <span
                  class="eval-tooltip-trigger"
                  title={trans.tooltip}
                  aria-label={trans.tooltip}
                >
                  {' '}?
                </span>
              )}
            </span>
            <input
              class="t-input eval-settings-input"
              type={def.type}
              min={def.min}
              max={def.max}
              step={def.step}
              value={values[def.key]}
              onInput={evt => handleChange(def.key, evt.currentTarget.value, def)}
            />
            {errors[def.key] && (
              <span class="eval-settings-error" role="alert">{errors[def.key]}</span>
            )}
          </label>
        );
      })}

      {/* Auto-promote section */}
      <div style={{ marginTop: '1rem', borderTop: '1px solid var(--border-subtle)', paddingTop: '0.75rem' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
          Auto-promote
        </div>
        {TOGGLE_DEFS.map(def => {
          const trans = T[def.transKey] || { label: def.transKey, tooltip: null };
          return (
            <label key={def.key} class="eval-settings-label" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.75rem' }}>
              <input
                type="checkbox"
                checked={values[def.key]}
                onChange={evt => setValues(prev => ({ ...prev, [def.key]: evt.currentTarget.checked }))}
              />
              <span>
                {trans.label}
                {trans.tooltip && (
                  <span class="eval-tooltip-trigger" title={trans.tooltip} aria-label={trans.tooltip}> ?</span>
                )}
              </span>
            </label>
          );
        })}
        {IMPROVEMENT_DEFS.map(def => {
          const trans = T[def.transKey] || { label: def.transKey, tooltip: null };
          return (
            <label key={def.key} class="eval-settings-label">
              <span>
                {trans.label}
                {trans.tooltip && (
                  <span class="eval-tooltip-trigger" title={trans.tooltip} aria-label={trans.tooltip}> ?</span>
                )}
              </span>
              <input
                class="t-input eval-settings-input"
                type={def.type}
                min={def.min}
                max={def.max}
                step={def.step}
                value={values[def.key]}
                onInput={evt => handleChange(def.key, evt.currentTarget.value, def)}
              />
              {errors[def.key] && (
                <span class="eval-settings-error" role="alert">{errors[def.key]}</span>
              )}
            </label>
          );
        })}
      </div>

      {/* Analysis model */}
      <div style={{ marginTop: '1rem', borderTop: '1px solid var(--border-subtle)', paddingTop: '0.75rem' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
          Analysis model
        </div>
        <label class="eval-settings-label">
          <span>
            Analysis model
            <span class="eval-tooltip-trigger" title="Model used to generate run analysis. Leave blank to use the judge model." aria-label="Model used to generate run analysis. Leave blank to use the judge model."> ?</span>
          </span>
          <ModelSelect
            value={analysisModel}
            onChange={val => setAnalysisModel(val)}
            backend="ollama"
            placeholder="Leave blank to use judge model"
            class="eval-settings-input"
            disabled={saving}
          />
        </label>
      </div>

      <div class="eval-settings-form__footer">
        <button
          type="button"
          class="t-btn t-btn-primary"
          onClick={handleSave}
          disabled={saving || Object.values(errors).some(Boolean)}
        >
          {saving ? 'Saving…' : saveOk ? 'Saved ✓' : 'Save'}
        </button>
        {saveError && <span class="eval-settings-error" role="alert">{saveError}</span>}
      </div>
    </div>
  );
}
