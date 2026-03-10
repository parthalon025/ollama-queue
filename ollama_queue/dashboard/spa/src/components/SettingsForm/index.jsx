import { h } from 'preact';
import { useState, useRef, useCallback, useEffect } from 'preact/hooks';
import { ThresholdPair, NumberRow, SelectRow, ToggleRow } from './SettingControls.jsx';

/**
 * What it shows: All queue configuration in fourteen sections. Each field saves on blur —
 *   no Save button needed; changes take effect on the next poll cycle (~5s).
 *
 * Sections:
 *   1. Auto-Pause Thresholds — when to stop starting new jobs
 *   2. Job Defaults — what new jobs get unless you say otherwise
 *   3. Data Retention — how long to keep records
 *   4. Automatic Retry — what happens when a job fails
 *   5. Stuck Job Detection — find and kill frozen jobs
 *   6. Parallel Jobs — how many jobs can run at once
 *   7. Fail-Safe Circuit Breaker — stop running when too many jobs fail
 *   8. Queue Limits — prevent the queue from getting overloaded
 *   9. Priority Ordering — decide what runs next
 *  10. Interrupt for Urgent Jobs — let high-priority jobs cut the line
 *  11. Anomaly Detection — spot unusual spikes in queue activity
 *  12. Daemon Controls — manually pause or resume the queue
 *  13. DLQ Auto-Reschedule — automatically retry failed jobs in quiet slots
 *  14. Proactive Deferral — pause jobs when resources are tight
 *
 * @param {{ settings: object, daemonState: string, onSave: (key: string, value: any) => Promise<boolean>, onPause: () => void, onResume: () => void, pauseFb: object, resumeFb: object }} props
 */
export default function SettingsForm({ settings, daemonState, onSave, onPause, onResume, pauseFb, resumeFb }) {
  const [flashKey, setFlashKey] = useState(null);
  const flashTimer = useRef(null);
  useEffect(() => () => { if (flashTimer.current) clearTimeout(flashTimer.current); }, []);

  const flash = useCallback((key) => {
    setFlashKey(key);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlashKey(null), 1000);
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

  // Human-friendly daemon state label
  const daemonStateLabel = {
    running:            'Running a job right now',
    idle:               'Ready — nothing running',
    paused_health:      'Paused — system resources are too high',
    paused_manual:      'Paused manually by you',
    paused_interactive: 'Paused — someone is actively using the computer',
  }[daemonState] || (daemonState || 'Unknown');

  return (
    <div class="flex flex-col gap-4">

      {/* 1. Auto-Pause Thresholds */}
      {/* What it shows: The resource usage percentages at which the queue automatically
       *   stops starting new jobs (pause) and when it's safe to start again (resume).
       * Decision it drives: Tune how aggressively the queue backs off. Lower the pause
       *   threshold if your system feels sluggish during heavy jobs; raise it if the queue
       *   pauses too often during normal use. The dashed marker on resource gauges shows
       *   exactly where each threshold sits. */}
      <div class="t-frame" data-label="Auto-Pause Thresholds — when to stop starting new jobs">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          The queue checks these limits before starting each job. If any are exceeded, it waits
          until usage drops back below the resume level before trying again.
        </p>
        <div class="flex flex-col gap-4">
          <ThresholdPair
            label="Main Memory (RAM)"
            sublabel="ram_pause_pct / ram_resume_pct"
            description="Stop new jobs when RAM is this full; restart when it drops back"
            pauseKey="ram_pause_pct"
            resumeKey="ram_resume_pct"
            unit="%"
            settings={settings}
            flashKey={flashKey}
            onBlur={handleBlur}
          />
          <ThresholdPair
            label="CPU Workload"
            sublabel="load_pause_multiplier / load_resume_multiplier"
            description="Stop new jobs when CPU load is this high relative to your core count"
            pauseKey="load_pause_multiplier"
            resumeKey="load_resume_multiplier"
            unit="x cores"
            step="0.1"
            settings={settings}
            flashKey={flashKey}
            onBlur={handleBlur}
          />
          <ThresholdPair
            label="Swap Memory"
            sublabel="swap_pause_pct / swap_resume_pct"
            description="Swap is disk space used as overflow when RAM is full — high swap means the system is struggling"
            pauseKey="swap_pause_pct"
            resumeKey="swap_resume_pct"
            unit="%"
            settings={settings}
            flashKey={flashKey}
            onBlur={handleBlur}
          />
          <ToggleRow
            label="Pause for Active Users"
            sublabel="yield_to_interactive"
            description="Stop the queue when someone is actively using keyboard or mouse on this computer"
            settingKey="yield_to_interactive"
            settings={settings}
            flashKey={flashKey}
            onSave={onSave}
            flash={flash}
          />
        </div>
      </div>

      {/* 2. Job Defaults */}
      <div class="t-frame" data-label="Job Defaults — what new jobs get unless you say otherwise">
        <div class="flex flex-col gap-3">
          <NumberRow label="Default Priority" sublabel="default_priority · 1 = run first, 10 = run last" settingKey="default_priority" min={1} max={10} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Job Time Limit" sublabel="default_timeout_seconds · kill a job if it runs longer than this" settingKey="default_timeout_seconds" min={1} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Check Interval" sublabel="poll_interval_seconds · how often the queue looks for new work to start" settingKey="poll_interval_seconds" min={1} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 3. Data Retention */}
      <div class="t-frame" data-label="Data Retention — how long to keep records">
        <div class="flex flex-col gap-3">
          <NumberRow label="Job History" sublabel="job_log_retention_days · completed and failed job records" settingKey="job_log_retention_days" min={1} unit="days" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="System Health Log" sublabel="health_log_retention_days · RAM / GPU / CPU readings" settingKey="health_log_retention_days" min={1} unit="days" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Job Timing Records" sublabel="duration_stats_retention_days · used to estimate how long future jobs will take" settingKey="duration_stats_retention_days" min={1} unit="days" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 4. Automatic Retry */}
      <div class="t-frame" data-label="Automatic Retry — what happens when a job fails">
        <div class="flex flex-col gap-3">
          <NumberRow label="Max Retry Attempts" sublabel="default_max_retries · after this many failures the job moves to the dead-letter queue" settingKey="default_max_retries" min={0} max={10} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Wait Before First Retry" sublabel="retry_backoff_base_seconds · seconds to pause after the first failure" settingKey="retry_backoff_base_seconds" min={1} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Wait Growth Between Retries" sublabel="retry_backoff_multiplier · each retry waits this many times longer than the last (e.g. 2x = 30s, 60s, 120s...)" settingKey="retry_backoff_multiplier" min={1} step="0.1" unit="x" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 5. Stuck Job Detection */}
      <div class="t-frame" data-label="Stuck Job Detection — find and kill frozen jobs">
        <div class="flex flex-col gap-3">
          <NumberRow label="Stall Sensitivity" sublabel="stall_posterior_threshold · probability (0-1) the job is stuck before acting; lower = more sensitive" settingKey="stall_posterior_threshold" min={0} max={1} step="0.01" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <SelectRow label="Action When a Job Gets Stuck" sublabel="stall_action" settingKey="stall_action" options={['log', 'kill']} optionLabels={{ log: 'Log only — record it but let the job keep running', kill: 'Kill — terminate the frozen job' }} settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
          {settings.stall_action === 'kill' && (
            <div style="font-size: var(--type-label); color: #f97316; background: rgba(249,115,22,0.08); border: 1px solid rgba(249,115,22,0.3); border-radius: 4px; padding: 6px 8px;">
              {'\u26A0'} Kill mode is ON — when a job looks frozen, it will receive a shutdown signal
              after the grace period below. The job will be removed and moved to the failed queue
              where you can retry it manually.
            </div>
          )}
          <NumberRow label="Grace Period Before Kill" sublabel="stall_kill_grace_seconds · seconds to wait after detecting a stall before sending the kill signal" settingKey="stall_kill_grace_seconds" min={0} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 6. Parallel Jobs */}
      <div class="t-frame" data-label="Parallel Jobs — how many jobs can run at the same time">
        <div class="flex flex-col gap-3">
          <NumberRow label="Max Jobs at Once" sublabel="max_concurrent_jobs · set to 1 to run jobs one at a time (safest)" settingKey="max_concurrent_jobs" min={1} max={8} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Learning Period" sublabel="concurrent_shadow_hours · collect timing data for this many hours before enabling parallel execution" settingKey="concurrent_shadow_hours" min={0} max={168} unit="hr" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="GPU Memory Buffer" sublabel="vram_safety_factor · multiply estimated GPU usage by this factor when checking headroom (1.2 = reserve 20% extra)" settingKey="vram_safety_factor" min={1.0} max={2.0} step="0.1" unit="x" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 7. Fail-Safe Circuit Breaker */}
      <div class="t-frame" data-label="Fail-Safe Circuit Breaker — stops the queue if too many jobs fail in a row">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          If jobs keep failing (e.g. Ollama crashed), this stops the queue automatically and
          waits before trying again — like a circuit breaker in your home's fuse box.
        </p>
        <div class="flex flex-col gap-3">
          <NumberRow label="Failures Before Stopping" sublabel="cb_failure_threshold · consecutive failures needed to trip the breaker" settingKey="cb_failure_threshold" min={1} max={20} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Initial Recovery Wait" sublabel="cb_base_cooldown · seconds to wait before trying again after the breaker trips" settingKey="cb_base_cooldown" min={5} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Maximum Recovery Wait" sublabel="cb_max_cooldown · cap on how long the backoff can grow between retries" settingKey="cb_max_cooldown" min={30} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 8. Queue Limits */}
      <div class="t-frame" data-label="Queue Limits — prevent the queue from getting overloaded">
        <div class="flex flex-col gap-3">
          <NumberRow label="Queue Size Limit" sublabel="max_queue_depth · reject new job submissions (HTTP 429) once this many are already waiting" settingKey="max_queue_depth" min={1} max={500} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Minimum GPU Memory Per Model" sublabel="min_model_vram_mb · skip the GPU memory check for models that report less than this (0 = always check)" settingKey="min_model_vram_mb" min={0} unit="MB" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="CPU Fallback Efficiency" sublabel="cpu_offload_efficiency · how fast a CPU-run model is compared to GPU (0 = can't use it, 1 = just as fast)" settingKey="cpu_offload_efficiency" min={0.0} max={1.0} step="0.05" unit="x" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 9. Priority Ordering */}
      <div class="t-frame" data-label="Priority Ordering — how the queue decides what runs next">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          The queue prefers shorter jobs first (so fast tasks don't wait behind slow ones) but
          also ages up older jobs so nothing waits forever.
        </p>
        <div class="flex flex-col gap-3">
          <NumberRow label="Old Job Age-Up Rate" sublabel="sjf_aging_factor (seconds) · a waiting job gains priority every this many seconds so it eventually runs" settingKey="sjf_aging_factor" min={60} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Freshness vs. Speed Balance" sublabel="aoi_weight · 0 = schedule by estimated duration only; 1 = weight waiting time equally" settingKey="aoi_weight" min={0.0} max={1.0} step="0.05" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 10. Interrupt for Urgent Jobs */}
      <div class="t-frame" data-label="Interrupt for Urgent Jobs — let high-priority jobs cut the line">
        <div class="flex flex-col gap-3">
          <ToggleRow label="Allow Interrupting Running Jobs" sublabel="preemption_enabled · a high-priority job can pause a lower-priority running job to go first" settingKey="preemption_enabled" settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
          <NumberRow label="Max Time a Job Can Be Interrupted" sublabel="preemption_window_seconds · only pause a running job if it started less than this many seconds ago" settingKey="preemption_window_seconds" min={10} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Interrupt Limit Per Job" sublabel="max_preemptions_per_job · a single job can be interrupted at most this many times total (0 = no limit)" settingKey="max_preemptions_per_job" min={0} max={10} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 11. Anomaly Detection */}
      <div class="t-frame" data-label="Anomaly Detection — spot unusual spikes in queue activity">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          Watches for job failure patterns that are statistically unusual — like 10 failures
          in 5 minutes when the normal rate is 1 per hour.
        </p>
        <div class="flex flex-col gap-3">
          <NumberRow label="Detection Window (jobs)" sublabel="entropy_alert_window · how many recent jobs to watch for unusual patterns" settingKey="entropy_alert_window" min={5} max={120} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Alert Sensitivity" sublabel="entropy_alert_sigma (sigma) · how many standard deviations above normal triggers an alert — lower = more sensitive" settingKey="entropy_alert_sigma" min={0.5} max={5.0} step="0.1" unit="sigma" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <ToggleRow label="Pause Background Jobs During Spikes" sublabel="entropy_suspend_low_priority · hold low-priority work when an anomaly is detected" settingKey="entropy_suspend_low_priority" settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
        </div>
      </div>

      {/* 13. DLQ Auto-Reschedule */}
      <div class="t-frame" data-label="DLQ Auto-Reschedule — automatically retry failed jobs in quiet slots">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          When a job lands in the Dead Letter Queue, the system can automatically find a low-load
          time slot and reschedule it. Jobs that fail repeatedly are marked "chronic" and left alone.
        </p>
        <div class="flex flex-col gap-3">
          <ToggleRow label="Enable Auto-Reschedule" sublabel="dlq.auto_reschedule · automatically retry DLQ entries in optimal time slots" settingKey="dlq.auto_reschedule" settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
          <NumberRow label="Sweep Interval" sublabel="dlq.sweep_fallback_minutes · how often the system checks for reschedulable DLQ entries" settingKey="dlq.sweep_fallback_minutes" min={5} max={240} unit="min" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Chronic Failure Threshold" sublabel="dlq.chronic_failure_threshold · after this many reschedule attempts, stop trying" settingKey="dlq.chronic_failure_threshold" min={1} max={20} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 14. Proactive Deferral */}
      <div class="t-frame" data-label="Proactive Deferral — pause jobs when resources are tight">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          Instead of letting low-priority jobs fail, the system can defer them until resources free
          up — like waiting for GPU temperature to drop or memory to clear.
        </p>
        <div class="flex flex-col gap-3">
          <ToggleRow label="Enable Deferral" sublabel="defer.enabled · proactively defer jobs when system resources are constrained" settingKey="defer.enabled" settings={settings} flashKey={flashKey} onSave={onSave} flash={flash} />
          <NumberRow label="Burst Priority Threshold" sublabel="defer.burst_priority_threshold · during burst regime, defer jobs above this priority number (lower = more important)" settingKey="defer.burst_priority_threshold" min={1} max={10} settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="GPU Thermal Limit" sublabel="defer.thermal_threshold_c · defer jobs when GPU temperature exceeds this" settingKey="defer.thermal_threshold_c" min={60} max={100} unit="C" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
          <NumberRow label="Resource Wait Timeout" sublabel="defer.resource_wait_timeout_s · how long to wait for resources before deferring" settingKey="defer.resource_wait_timeout_s" min={10} max={600} unit="sec" settings={settings} flashKey={flashKey} onBlur={handleBlur} />
        </div>
      </div>

      {/* 12. Daemon Controls */}
      <div class="t-frame" data-label="Daemon Controls — manually pause or resume the queue">
        <div class="flex flex-col gap-2">
          <div class="flex items-center gap-3">
            {isPaused ? (
              <button
                class="t-btn t-btn-primary px-4 py-2 text-sm"
                disabled={resumeFb?.phase === 'loading'}
                onClick={onResume}
              >
                {resumeFb?.phase === 'loading' ? 'Resuming\u2026' : 'Resume the Queue'}
              </button>
            ) : (
              <button
                class="t-btn t-btn-secondary px-4 py-2 text-sm"
                disabled={pauseFb?.phase === 'loading'}
                onClick={onPause}
              >
                {pauseFb?.phase === 'loading' ? 'Pausing\u2026' : 'Pause the Queue'}
              </button>
            )}
            <span style="font-size: var(--type-label); color: var(--text-secondary);">
              {daemonStateLabel}
              <span class="data-mono" style="display: block; font-size: var(--type-micro); color: var(--text-tertiary);">
                {daemonState || 'unknown'}
              </span>
            </span>
          </div>
          {pauseFb?.msg && <div class={`action-fb action-fb--${pauseFb.phase}`}>{pauseFb.msg}</div>}
          {resumeFb?.msg && <div class={`action-fb action-fb--${resumeFb.phase}`}>{resumeFb.msg}</div>}
        </div>
      </div>
    </div>
  );
}
