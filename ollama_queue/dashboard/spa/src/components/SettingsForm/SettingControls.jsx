import { h } from 'preact';
import { useRef } from 'preact/hooks';

// What it shows: Reusable form controls for the settings page — threshold pairs,
//   number inputs, select dropdowns, and boolean toggles. Each saves on blur or change,
//   with a brief green flash to confirm the write.
// Decision it drives: Keeps the main SettingsForm focused on section layout while these
//   components handle input rendering, value parsing, and save-on-blur feedback.

/**
 * Paired pause/resume threshold inputs side by side.
 * Accepts an optional description shown below the section label.
 */
export function ThresholdPair({ label, sublabel, description, pauseKey, resumeKey, unit, step, settings, flashKey, onBlur }) {
  return (
    <div class="flex flex-col gap-1">
      <span style="font-size: var(--type-label); color: var(--text-secondary);">
        {label}
        {sublabel && (
          <span style="display: block; font-size: var(--type-micro); color: var(--text-tertiary); font-family: var(--font-mono);">{sublabel}</span>
        )}
        {description && (
          <span style="display: block; font-size: var(--type-label); color: var(--text-tertiary); font-family: inherit; text-transform: none; letter-spacing: normal; font-weight: 400; margin-top: 1px;">{description}</span>
        )}
      </span>
      <div class="flex flex-col sm:flex-row gap-2">
        <SettingInput
          label="Stop new jobs when above"
          settingKey={pauseKey}
          unit={unit}
          step={step}
          settings={settings}
          flashKey={flashKey}
          onBlur={onBlur}
        />
        <SettingInput
          label="Start again when below"
          settingKey={resumeKey}
          unit={unit}
          step={step}
          settings={settings}
          flashKey={flashKey}
          onBlur={onBlur}
        />
      </div>
    </div>
  );
}

/**
 * Single number input with label, unit suffix, and save-on-blur flash.
 */
export function SettingInput({ label, settingKey, unit, step, settings, flashKey, onBlur }) {
  const inputRef = useRef(null);
  const val = settings[settingKey];
  const isFlash = flashKey === settingKey;

  return (
    <label class="flex items-center gap-2 flex-1" style="min-width: 0;">
      <span style="font-size: var(--type-label); color: var(--text-tertiary); white-space: nowrap; min-width: 70px;">
        {label}
      </span>
      <input
        ref={inputRef}
        type="number"
        step={step || 1}
        class="t-input data-mono"
        style={{
          width: '80px',
          padding: '4px 8px',
          fontSize: 'var(--type-body)',
          background: isFlash ? 'var(--status-healthy-glow)' : 'var(--bg-inset)',
          transition: 'background 0.3s ease',
        }}
        value={val ?? ''}
        onBlur={(e) => onBlur(settingKey, e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur(); }}
      />
      {unit && (
        <span style="font-size: var(--type-label); color: var(--text-tertiary);">{unit}</span>
      )}
    </label>
  );
}

/**
 * Number row: label + optional sublabel description + input + optional unit.
 */
export function NumberRow({ label, sublabel, settingKey, min, max, step, unit, settings, flashKey, onBlur }) {
  const val = settings[settingKey];
  const isFlash = flashKey === settingKey;

  return (
    <label class="flex items-start justify-between gap-3">
      <span style="font-size: var(--type-body); color: var(--text-secondary); padding-top: 4px;">
        {label}
        {sublabel && (
          <span style="display: block; font-size: var(--type-micro); color: var(--text-tertiary); font-family: var(--font-mono); margin-top: 1px; line-height: 1.4;">{sublabel}</span>
        )}
      </span>
      <div class="flex items-center gap-2" style="flex-shrink: 0;">
        <input
          type="number"
          min={min}
          max={max}
          step={step || 1}
          class="t-input data-mono"
          style={{
            width: '90px',
            padding: '4px 8px',
            fontSize: 'var(--type-body)',
            textAlign: 'right',
            background: isFlash ? 'var(--status-healthy-glow)' : 'var(--bg-inset)',
            transition: 'background 0.3s ease',
          }}
          value={val ?? ''}
          onBlur={(e) => onBlur(settingKey, e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur(); }}
        />
        {unit && (
          <span style="font-size: var(--type-label); color: var(--text-tertiary); min-width: 30px;">{unit}</span>
        )}
      </div>
    </label>
  );
}

/**
 * Select row for string enum settings.
 * Accepts an optional optionLabels map to show friendly names instead of raw values.
 */
export function SelectRow({ label, sublabel, settingKey, options, optionLabels, settings, flashKey, onSave, flash }) {
  const val = settings[settingKey];
  const isFlash = flashKey === settingKey;

  const handleChange = async (e) => {
    const selected = e.target.value;
    if (selected === val) return;
    const ok = await onSave(settingKey, selected);
    if (ok) flash(settingKey);
  };

  return (
    <label
      class="flex items-start justify-between gap-3"
      style={{
        background: isFlash ? 'var(--status-healthy-glow)' : 'transparent',
        transition: 'background 0.3s ease',
        padding: '4px 0',
        borderRadius: 'var(--radius)',
      }}
    >
      <span style="font-size: var(--type-body); color: var(--text-secondary); padding-top: 4px;">
        {label}
        {sublabel && (
          <span style="display: block; font-size: var(--type-micro); color: var(--text-tertiary); font-family: var(--font-mono); margin-top: 1px;">{sublabel}</span>
        )}
      </span>
      <select
        class="t-input data-mono"
        style={{
          width: '90px',
          padding: '4px 8px',
          fontSize: 'var(--type-body)',
          background: isFlash ? 'var(--status-healthy-glow)' : 'var(--bg-inset)',
          transition: 'background 0.3s ease',
          flexShrink: 0,
        }}
        value={val ?? options[0]}
        onChange={handleChange}
      >
        {options.map(o => (
          <option key={o} value={o}>{optionLabels ? optionLabels[o] || o : o}</option>
        ))}
      </select>
    </label>
  );
}

/**
 * Toggle row for boolean settings.
 */
export function ToggleRow({ label, sublabel, settingKey, settings, flashKey, onSave, flash }) {
  const val = settings[settingKey];
  const isFlash = flashKey === settingKey;

  const handleChange = async (e) => {
    const checked = e.target.checked;
    const ok = await onSave(settingKey, checked);
    if (ok) flash(settingKey);
  };

  return (
    <label
      class="flex items-start justify-between gap-3"
      style={{
        background: isFlash ? 'var(--status-healthy-glow)' : 'transparent',
        transition: 'background 0.3s ease',
        padding: '4px 0',
        borderRadius: 'var(--radius)',
      }}
    >
      <span style="font-size: var(--type-body); color: var(--text-secondary); padding-top: 2px;">
        {label}
        {sublabel && (
          <span style="display: block; font-size: var(--type-micro); color: var(--text-tertiary); font-family: var(--font-mono); margin-top: 1px;">{sublabel}</span>
        )}
      </span>
      <input
        type="checkbox"
        checked={!!val}
        onChange={handleChange}
        style="width: 18px; height: 18px; accent-color: var(--accent); flex-shrink: 0; margin-top: 2px;"
      />
    </label>
  );
}
