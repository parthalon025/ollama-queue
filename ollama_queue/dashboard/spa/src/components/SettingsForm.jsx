import { h } from 'preact';
import { useState, useRef, useCallback } from 'preact/hooks';

/**
 * What it shows: All queue configuration in four sections: Health Thresholds (when to pause
 *   for high RAM/VRAM/load/swap), Defaults (timeout, priority, retry backoff), Retention
 *   (how long to keep health logs, job history, duration stats), and Daemon Controls
 *   (manual pause/resume buttons + stall detection settings).
 * Decision it drives: Tune the queue's behavior. Lower the pause thresholds if the system
 *   is being hammered; raise them if the queue pauses too aggressively. Increase default
 *   timeout if long-running jobs are being killed prematurely. Each field saves on blur —
 *   no Save button needed; changes take effect on the next poll cycle.
 *
 * @param {{ settings: object, daemonState: string, onSave: (key: string, value: any) => Promise<boolean>, onPause: () => void, onResume: () => void, pauseFb: object, resumeFb: object }} props
 */
export default function SettingsForm({ settings, daemonState, onSave, onPause, onResume, pauseFb, resumeFb }) {
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
          <NumberRow label="Posterior Threshold" settingKey="stall_posterior_threshold" min={0} max={1} step="0.01" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <SelectRow label="Stall Action" settingKey="stall_action" options={['log', 'kill']} settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
          {settings.stall_action === 'kill' && (
            <div style="font-size: var(--type-micro); color: #f97316; background: rgba(249,115,22,0.08);
                        border: 1px solid rgba(249,115,22,0.3); border-radius: 4px; padding: 6px 8px;">
              ⚠ Kill mode: stalled jobs will receive SIGTERM after the grace period elapses.
            </div>
          )}
          <NumberRow label="Kill Grace" settingKey="stall_kill_grace_seconds" min={0} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 6. Concurrency */}
      <div class="t-frame" data-label="Concurrency">
        <div class="flex flex-col gap-3">
          <NumberRow label="Max Concurrent Jobs" settingKey="max_concurrent_jobs" min={1} max={8} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Concurrent Shadow Hours" settingKey="concurrent_shadow_hours" min={0} max={168} unit="hr" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="VRAM Safety Factor" settingKey="vram_safety_factor" min={1.0} max={2.0} step="0.1" unit="×" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 7. Circuit Breaker */}
      {/* What it shows: Automatic fault isolation — how many failures trigger the breaker,
       *   and how long the daemon backs off before retrying. Prevents a broken Ollama
       *   from causing cascading job failures.
       * Decision it drives: Tune failure tolerance vs recovery speed. Low threshold = faster
       *   isolation; long cooldown = more breathing room for Ollama to recover. */}
      <div class="t-frame" data-label="Circuit Breaker">
        <div class="flex flex-col gap-3">
          <NumberRow label="Failure Threshold" settingKey="cb_failure_threshold" min={1} max={20} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Base Cooldown" settingKey="cb_base_cooldown" min={5} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Max Cooldown" settingKey="cb_max_cooldown" min={30} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 8. Admission */}
      {/* What it shows: Controls on what gets let into the queue — max depth before 429s,
       *   minimum VRAM requirement per model, and CPU-offload efficiency multiplier.
       * Decision it drives: Protect the system from queue floods. Raise max_queue_depth
       *   if legitimate work is being rejected; lower min_model_vram_mb to allow smaller
       *   models to run without VRAM checks. */}
      <div class="t-frame" data-label="Admission">
        <div class="flex flex-col gap-3">
          <NumberRow label="Max Queue Depth" settingKey="max_queue_depth" min={1} max={500} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Min Model VRAM" settingKey="min_model_vram_mb" min={0} unit="MB" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="CPU Offload Efficiency" settingKey="cpu_offload_efficiency" min={0.0} max={1.0} step="0.05" unit="×" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 9. Scheduling */}
      {/* What it shows: Shortest-Job-First tuning — how fast older jobs age up in priority
       *   (aging factor) and how much AoI (Age of Information) urgency matters vs raw SJF.
       * Decision it drives: If old low-priority jobs get stuck behind fast new jobs, reduce
       *   sjf_aging_factor or increase aoi_weight to give them more urgency. */}
      <div class="t-frame" data-label="Scheduling">
        <div class="flex flex-col gap-3">
          <NumberRow label="SJF Aging Factor" settingKey="sjf_aging_factor" min={60} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="AoI Weight" settingKey="aoi_weight" min={0.0} max={1.0} step="0.05" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 10. Preemption */}
      {/* What it shows: Whether the daemon is allowed to pause a low-priority running job
       *   so a critical job can run first, and the guard rails around doing so.
       * Decision it drives: Enable preemption for latency-sensitive high-priority jobs;
       *   tune the window and per-job cap to avoid starving any single job. */}
      <div class="t-frame" data-label="Preemption">
        <div class="flex flex-col gap-3">
          <ToggleRow label="Enable Preemption" settingKey="preemption_enabled" settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
          <NumberRow label="Preemption Window" settingKey="preemption_window_seconds" min={10} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Max Preemptions / Job" settingKey="max_preemptions_per_job" min={0} max={10} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 11. Entropy */}
      {/* What it shows: Anomaly detection thresholds — the rolling window size and how many
       *   standard deviations above baseline triggers a "queue entropy spike" alert, plus
       *   whether low-priority jobs are suspended during a spike.
       * Decision it drives: Tighten the sigma to catch subtle anomalies earlier; widen it
       *   to reduce false-alarm noise during normal traffic variance. */}
      <div class="t-frame" data-label="Entropy">
        <div class="flex flex-col gap-3">
          <NumberRow label="Alert Window" settingKey="entropy_alert_window" min={5} max={120} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Alert Sigma" settingKey="entropy_alert_sigma" min={0.5} max={5.0} step="0.1" unit="σ" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <ToggleRow label="Suspend Low-Priority on Spike" settingKey="entropy_suspend_low_priority" settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
        </div>
      </div>

      {/* 12. Daemon Controls */}
      <div class="t-frame" data-label="Daemon Controls">
        <div class="flex flex-col gap-2">
          <div class="flex items-center gap-3">
            {isPaused ? (
              <button
                class="t-btn t-btn-primary px-4 py-2 text-sm"
                disabled={resumeFb?.phase === 'loading'}
                onClick={onResume}
              >
                {resumeFb?.phase === 'loading' ? 'Resuming…' : 'Resume Daemon'}
              </button>
            ) : (
              <button
                class="t-btn t-btn-secondary px-4 py-2 text-sm"
                disabled={pauseFb?.phase === 'loading'}
                onClick={onPause}
              >
                {pauseFb?.phase === 'loading' ? 'Pausing…' : 'Pause Daemon'}
              </button>
            )}
            <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
              {daemonState || 'unknown'}
            </span>
          </div>
          {pauseFb?.msg && <div class={`action-fb action-fb--${pauseFb.phase}`}>{pauseFb.msg}</div>}
          {resumeFb?.msg && <div class={`action-fb action-fb--${resumeFb.phase}`}>{resumeFb.msg}</div>}
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
 * Select row for string enum settings.
 */
function SelectRow({ label, settingKey, options, settings, flashKey, onSave, flash }) {
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
      <select
        class="t-input data-mono"
        style={{
          width: '90px',
          padding: '4px 8px',
          fontSize: 'var(--type-body)',
          background: isFlash ? 'var(--status-healthy-glow)' : 'var(--bg-inset)',
          transition: 'background 0.3s ease',
        }}
        value={val ?? options[0]}
        onChange={handleChange}
      >
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
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
