import { h } from 'preact';
import { useState, useRef, useCallback } from 'preact/hooks';

/**
 * Settings form with four sections: Health Thresholds, Defaults, Retention, Daemon Controls.
 * Each input saves on blur via onSave(key, value).
 *
 * @param {{ settings: object, daemonState: string, onSave: (key: string, value: any) => Promise<boolean>, onPause: () => void, onResume: () => void }} props
 */
export default function SettingsForm({ settings, daemonState, onSave, onPause, onResume }) {
  const [flashKey, setFlashKey] = useState(null);

  const flash = useCallback((key) => {
    setFlashKey(key);
    setTimeout(() => setFlashKey(null), 1000);
  }, []);

  const handleBlur = useCallback(async (key, raw) => {
    const current = settings[key];
    // Parse: booleans stay bool, numbers stay number
    let value;
    if (typeof current === 'boolean') {
      value = Boolean(raw);
    } else if (typeof current === 'number') {
      value = Number(raw);
      if (isNaN(value)) return; // invalid, skip
    } else {
      value = raw;
    }
    // Skip if unchanged
    if (value === current) return;
    const ok = await onSave(key, value);
    if (ok) flash(key);
  }, [settings, onSave, flash]);

  const isPaused = daemonState && daemonState.startsWith('paused');

  return (
    <div class="flex flex-col gap-4">
      {/* 1. Health Thresholds */}
      <div class="t-frame" data-label="Health Thresholds">
        <div class="flex flex-col gap-4">
          <ThresholdPair
            label="RAM"
            pauseKey="ram_pause_pct"
            resumeKey="ram_resume_pct"
            unit="%"
            settings={settings}
            flashKey={flashKey}
            onBlur={handleBlur}
          />
          <ThresholdPair
            label="VRAM"
            pauseKey="vram_pause_pct"
            resumeKey="vram_resume_pct"
            unit="%"
            settings={settings}
            flashKey={flashKey}
            onBlur={handleBlur}
          />
          <ThresholdPair
            label="Load"
            pauseKey="load_pause_multiplier"
            resumeKey="load_resume_multiplier"
            unit="x"
            step="0.1"
            settings={settings}
            flashKey={flashKey}
            onBlur={handleBlur}
          />
          <ThresholdPair
            label="Swap"
            pauseKey="swap_pause_pct"
            resumeKey="swap_resume_pct"
            unit="%"
            settings={settings}
            flashKey={flashKey}
            onBlur={handleBlur}
          />
          <ToggleRow
            label="Yield to Interactive"
            settingKey="yield_to_interactive"
            settings={settings}
            flashKey={flashKey}
            onSave={onSave}
            flash={flash}
          />
        </div>
      </div>

      {/* 2. Defaults */}
      <div class="t-frame" data-label="Defaults">
        <div class="flex flex-col gap-3">
          <NumberRow label="Default Priority" settingKey="default_priority" min={1} max={10} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Default Timeout" settingKey="default_timeout_seconds" min={1} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Poll Interval" settingKey="poll_interval_seconds" min={1} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 3. Retention */}
      <div class="t-frame" data-label="Retention">
        <div class="flex flex-col gap-3">
          <NumberRow label="Job History" settingKey="job_log_retention_days" min={1} unit="days" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Health Log" settingKey="health_log_retention_days" min={1} unit="days" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Duration Stats" settingKey="duration_stats_retention_days" min={1} unit="days" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 4. Retry Defaults */}
      <div class="t-frame" data-label="Retry Defaults">
        <div class="flex flex-col gap-3">
          <NumberRow label="Max Retries (default)" settingKey="default_max_retries" min={0} max={10} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Backoff Base" settingKey="retry_backoff_base_seconds" min={1} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Backoff Multiplier" settingKey="retry_backoff_multiplier" min={1} step="0.1" unit="×" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 5. Stall Detection */}
      <div class="t-frame" data-label="Stall Detection">
        <div class="flex flex-col gap-3">
          <NumberRow label="Stall Multiplier" settingKey="stall_multiplier" min={1} step="0.1" unit="× est." settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 6. Daemon Controls */}
      <div class="t-frame" data-label="Daemon Controls">
        <div class="flex items-center gap-3">
          {isPaused ? (
            <button
              class="t-btn t-btn-primary px-4 py-2 text-sm"
              onClick={onResume}
            >
              Resume Daemon
            </button>
          ) : (
            <button
              class="t-btn t-btn-secondary px-4 py-2 text-sm"
              onClick={onPause}
            >
              Pause Daemon
            </button>
          )}
          <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
            {daemonState || 'unknown'}
          </span>
        </div>
      </div>
    </div>
  );
}

/**
 * Paired pause/resume threshold inputs side by side.
 */
function ThresholdPair({ label, pauseKey, resumeKey, unit, step, settings, flashKey, onBlur }) {
  return (
    <div class="flex flex-col gap-1">
      <span style="font-size: var(--type-label); color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em;">
        {label}
      </span>
      <div class="flex flex-col sm:flex-row gap-2">
        <SettingInput
          label="Pause at"
          settingKey={pauseKey}
          unit={unit}
          step={step}
          settings={settings}
          flashKey={flashKey}
          onBlur={onBlur}
        />
        <SettingInput
          label="Resume at"
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
function SettingInput({ label, settingKey, unit, step, settings, flashKey, onBlur }) {
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
 * Simple number row: label + input + optional unit.
 */
function NumberRow({ label, settingKey, min, max, step, unit, settings, flashKey, onBlur }) {
  const val = settings[settingKey];
  const isFlash = flashKey === settingKey;

  return (
    <label class="flex items-center justify-between gap-3">
      <span style="font-size: var(--type-body); color: var(--text-secondary);">
        {label}
      </span>
      <div class="flex items-center gap-2">
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
 * Toggle row for boolean settings.
 */
function ToggleRow({ label, settingKey, settings, flashKey, onSave, flash }) {
  const val = settings[settingKey];
  const isFlash = flashKey === settingKey;

  const handleChange = async (e) => {
    const checked = e.target.checked;
    const ok = await onSave(settingKey, checked);
    if (ok) flash(settingKey);
  };

  return (
    <label
      class="flex items-center justify-between gap-3"
      style={{
        background: isFlash ? 'var(--status-healthy-glow)' : 'transparent',
        transition: 'background 0.3s ease',
        padding: '4px 0',
        borderRadius: 'var(--radius)',
      }}
    >
      <span style="font-size: var(--type-body); color: var(--text-secondary);">
        {label}
      </span>
      <input
        type="checkbox"
        checked={!!val}
        onChange={handleChange}
        style="width: 18px; height: 18px; accent-color: var(--accent);"
      />
    </label>
  );
}
