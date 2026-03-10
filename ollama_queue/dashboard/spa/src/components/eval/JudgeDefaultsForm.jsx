import { h } from 'preact';
import { useState, useRef, useEffect } from 'preact/hooks';
// What it shows: Default judge (scorer) model configuration — which AI scores
//   generated principles, from which provider, with what consistency setting.
// Decision it drives: User sets the judge model used for all eval runs unless
//   overridden per-run. Getting this right matters for score reliability.

import { evalSettings, saveEvalSettings } from '../../store.js';
import { EVAL_TRANSLATIONS } from './translations.js';
import ModelSelect from '../ModelSelect.jsx';

export default function JudgeDefaultsForm() {
  // Read .value at top of body to subscribe to signal changes
  const settings = evalSettings.value;

  const [judgeModel,       setJudgeModel]       = useState(settings['eval.judge_model']       ?? 'deepseek-r1:8b');
  const [judgeBackend,     setJudgeBackend]     = useState(settings['eval.judge_backend']     ?? 'ollama');
  const [judgeTemperature, setJudgeTemperature] = useState(
    parseFloat(settings['eval.judge_temperature'] ?? '0.1')
  );
  const [saving,    setSaving]    = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveOk,    setSaveOk]    = useState(false);
  const [tempError, setTempError] = useState('');
  const saveTimer = useRef(null);
  useEffect(() => () => { if (saveTimer.current) clearTimeout(saveTimer.current); }, []);

  function validateTemperature(val) {
    const n = parseFloat(val);
    if (isNaN(n) || n < 0.0 || n > 2.0) {
      setTempError('Must be between 0.0 and 2.0');
      return false;
    }
    setTempError('');
    return true;
  }

  async function handleSave() {
    if (!validateTemperature(judgeTemperature)) return;
    setSaving(true);
    setSaveError('');
    setSaveOk(false);
    try {
      await saveEvalSettings({
        'eval.judge_model':       judgeModel,
        'eval.judge_backend':     judgeBackend,
        'eval.judge_temperature': judgeTemperature,
      });
      setSaveOk(true);
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => setSaveOk(false), 2000);
    } catch (err) {
      setSaveError(err.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  const T = EVAL_TRANSLATIONS;

  return (
    <div class="eval-settings-form t-frame" data-label="Scorer defaults">
      {/* Scorer AI */}
      <label class="eval-settings-label">
        <span>
          {T.judge_model.label}
          {T.judge_model.tooltip && (
            <span class="eval-tooltip-trigger" title={T.judge_model.tooltip} aria-label={T.judge_model.tooltip}> ?</span>
          )}
        </span>
        <ModelSelect
          value={judgeModel}
          onChange={val => setJudgeModel(val)}
          backend={judgeBackend}
          placeholder="deepseek-r1:8b"
          class="eval-settings-input"
          disabled={saving}
        />
      </label>

      {/* Scorer provider */}
      <label class="eval-settings-label">
        <span>
          {T.judge_backend.label}
          {T.judge_backend.tooltip && (
            <span class="eval-tooltip-trigger" title={T.judge_backend.tooltip} aria-label={T.judge_backend.tooltip}> ?</span>
          )}
        </span>
        <select
          class="t-input eval-settings-input"
          value={judgeBackend}
          onChange={evt => setJudgeBackend(evt.currentTarget.value)}
        >
          <option value="ollama">ollama (local)</option>
          <option value="openai">openai (GPT-4o-mini)</option>
        </select>
      </label>

      {/* Scorer consistency (temperature) */}
      <label class="eval-settings-label">
        <span>
          {T.judge_temperature.label}
          {T.judge_temperature.tooltip && (
            <span class="eval-tooltip-trigger" title={T.judge_temperature.tooltip} aria-label={T.judge_temperature.tooltip}> ?</span>
          )}
        </span>
        <input
          class="t-input eval-settings-input"
          type="number"
          min="0.0"
          max="2.0"
          step="0.05"
          value={judgeTemperature}
          onInput={evt => {
            setJudgeTemperature(parseFloat(evt.currentTarget.value));
            validateTemperature(evt.currentTarget.value);
          }}
        />
        {tempError && <span class="eval-settings-error" role="alert">{tempError}</span>}
      </label>

      <div class="eval-settings-form__footer">
        <button
          type="button"
          class="t-btn t-btn-primary"
          onClick={handleSave}
          disabled={saving || !!tempError}
        >
          {saving ? 'Saving…' : saveOk ? 'Saved ✓' : 'Save'}
        </button>
        {saveError && <span class="eval-settings-error" role="alert">{saveError}</span>}
      </div>
    </div>
  );
}
