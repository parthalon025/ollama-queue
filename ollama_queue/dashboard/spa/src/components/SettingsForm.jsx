import { h } from 'preact';
import { useState, useRef, useCallback } from 'preact/hooks';

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
      {/* What it shows: The fallback values used when a job doesn't specify its own
       *   priority, time limit, or poll rate.
       * Decision it drives: If most jobs run fine with the defaults you rarely need to
       *   touch these. Raise Default Timeout if long-running jobs are being killed too soon. */}
      <div class="t-frame" data-label="Job Defaults — what new jobs get unless you say otherwise">
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Default Priority"
            sublabel="default_priority · 1 = run first, 10 = run last"
            settingKey="default_priority"
            min={1} max={10}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Job Time Limit"
            sublabel="default_timeout_seconds · kill a job if it runs longer than this"
            settingKey="default_timeout_seconds"
            min={1} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Check Interval"
            sublabel="poll_interval_seconds · how often the queue looks for new work to start"
            settingKey="poll_interval_seconds"
            min={1} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 3. Data Retention */}
      <div class="t-frame" data-label="Data Retention — how long to keep records">
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Job History"
            sublabel="job_log_retention_days · completed and failed job records"
            settingKey="job_log_retention_days"
            min={1} unit="days"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="System Health Log"
            sublabel="health_log_retention_days · RAM / GPU / CPU readings"
            settingKey="health_log_retention_days"
            min={1} unit="days"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Job Timing Records"
            sublabel="duration_stats_retention_days · used to estimate how long future jobs will take"
            settingKey="duration_stats_retention_days"
            min={1} unit="days"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 4. Automatic Retry */}
      {/* What it shows: How many times a failed job is automatically re-tried, and how
       *   long to wait between each attempt (exponential backoff — each wait is longer
       *   than the last so the system has time to recover).
       * Decision it drives: Set retries to 0 to disable retries entirely. Increase
       *   backoff multiplier if jobs fail in bursts and need more breathing room. */}
      <div class="t-frame" data-label="Automatic Retry — what happens when a job fails">
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Max Retry Attempts"
            sublabel="default_max_retries · after this many failures the job moves to the dead-letter queue"
            settingKey="default_max_retries"
            min={0} max={10}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Wait Before First Retry"
            sublabel="retry_backoff_base_seconds · seconds to pause after the first failure"
            settingKey="retry_backoff_base_seconds"
            min={1} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Wait Growth Between Retries"
            sublabel="retry_backoff_multiplier · each retry waits this many times longer than the last (e.g. 2× = 30s, 60s, 120s…)"
            settingKey="retry_backoff_multiplier"
            min={1} step="0.1" unit="×"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 5. Stuck Job Detection */}
      {/* What it shows: Settings that control how the queue detects a job that has
       *   frozen (producing no output and not making progress) and what to do about it.
       * Decision it drives: Lower the sensitivity to catch subtle stalls earlier; raise
       *   it to reduce false alarms on legitimately slow jobs. */}
      <div class="t-frame" data-label="Stuck Job Detection — find and kill frozen jobs">
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Stall Sensitivity"
            sublabel="stall_posterior_threshold · probability (0–1) the job is stuck before acting; lower = more sensitive"
            settingKey="stall_posterior_threshold"
            min={0} max={1} step="0.01"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <SelectRow
            label="Action When a Job Gets Stuck"
            sublabel="stall_action"
            settingKey="stall_action"
            options={['log', 'kill']}
            optionLabels={{ log: 'Log only — record it but let the job keep running', kill: 'Kill — terminate the frozen job' }}
            settings={settings} flashKey={flashKey} onSave={onSave} flash={flash}
          />
          {settings.stall_action === 'kill' && (
            <div style="font-size: var(--type-label); color: #f97316; background: rgba(249,115,22,0.08);
                        border: 1px solid rgba(249,115,22,0.3); border-radius: 4px; padding: 6px 8px;">
              ⚠ Kill mode is ON — when a job looks frozen, it will receive a shutdown signal
              after the grace period below. The job will be removed and moved to the failed queue
              where you can retry it manually.
            </div>
          )}
          <NumberRow
            label="Grace Period Before Kill"
            sublabel="stall_kill_grace_seconds · seconds to wait after detecting a stall before sending the kill signal"
            settingKey="stall_kill_grace_seconds"
            min={0} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 6. Parallel Jobs */}
      {/* What it shows: Controls for running more than one job at a time. Shadow hours
       *   is a safety period where the queue collects data before enabling concurrency.
       * Decision it drives: Raise Max Jobs at Once if your GPU has headroom and you want
       *   faster throughput. Keep it at 1 if jobs contend for GPU memory. */}
      <div class="t-frame" data-label="Parallel Jobs — how many jobs can run at the same time">
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Max Jobs at Once"
            sublabel="max_concurrent_jobs · set to 1 to run jobs one at a time (safest)"
            settingKey="max_concurrent_jobs"
            min={1} max={8}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Learning Period"
            sublabel="concurrent_shadow_hours · collect timing data for this many hours before enabling parallel execution"
            settingKey="concurrent_shadow_hours"
            min={0} max={168} unit="hr"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="GPU Memory Buffer"
            sublabel="vram_safety_factor · multiply estimated GPU usage by this factor when checking headroom (1.2 = reserve 20% extra)"
            settingKey="vram_safety_factor"
            min={1.0} max={2.0} step="0.1" unit="×"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 7. Fail-Safe Circuit Breaker */}
      {/* What it shows: Automatic fault isolation — how many consecutive failures trigger
       *   the breaker (which pauses all new jobs), and how long to back off before trying
       *   again. Prevents a broken Ollama from causing a cascade of failures.
       * Decision it drives: Low threshold = faster isolation; long cooldown = more time
       *   for Ollama to recover before jobs resume. */}
      <div class="t-frame" data-label="Fail-Safe Circuit Breaker — stops the queue if too many jobs fail in a row">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          If jobs keep failing (e.g. Ollama crashed), this stops the queue automatically and
          waits before trying again — like a circuit breaker in your home's fuse box.
        </p>
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Failures Before Stopping"
            sublabel="cb_failure_threshold · consecutive failures needed to trip the breaker"
            settingKey="cb_failure_threshold"
            min={1} max={20}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Initial Recovery Wait"
            sublabel="cb_base_cooldown · seconds to wait before trying again after the breaker trips"
            settingKey="cb_base_cooldown"
            min={5} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Maximum Recovery Wait"
            sublabel="cb_max_cooldown · cap on how long the backoff can grow between retries"
            settingKey="cb_max_cooldown"
            min={30} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 8. Queue Limits */}
      {/* What it shows: Controls on what gets admitted to the queue — max depth before
       *   new submissions are rejected, minimum VRAM requirement per model, and CPU-offload
       *   efficiency multiplier.
       * Decision it drives: Protect the system from queue floods. Raise max_queue_depth
       *   if legitimate work is being rejected; lower min_model_vram_mb to allow smaller
       *   models without VRAM checks. */}
      <div class="t-frame" data-label="Queue Limits — prevent the queue from getting overloaded">
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Queue Size Limit"
            sublabel="max_queue_depth · reject new job submissions (HTTP 429) once this many are already waiting"
            settingKey="max_queue_depth"
            min={1} max={500}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Minimum GPU Memory Per Model"
            sublabel="min_model_vram_mb · skip the GPU memory check for models that report less than this (0 = always check)"
            settingKey="min_model_vram_mb"
            min={0} unit="MB"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="CPU Fallback Efficiency"
            sublabel="cpu_offload_efficiency · how fast a CPU-run model is compared to GPU (0 = can't use it, 1 = just as fast)"
            settingKey="cpu_offload_efficiency"
            min={0.0} max={1.0} step="0.05" unit="×"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 9. Priority Ordering */}
      {/* What it shows: Shortest-Job-First tuning — how fast older jobs age up in priority
       *   (so they don't get stuck forever behind new short jobs), and how much a job's
       *   waiting time factors into the scheduling score vs. its estimated duration.
       * Decision it drives: If old low-priority jobs never run, lower sjf_aging_factor
       *   so they age up faster, or increase aoi_weight to give waiting time more influence. */}
      <div class="t-frame" data-label="Priority Ordering — how the queue decides what runs next">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          The queue prefers shorter jobs first (so fast tasks don't wait behind slow ones) but
          also ages up older jobs so nothing waits forever.
        </p>
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Old Job Age-Up Rate"
            sublabel="sjf_aging_factor (seconds) · a waiting job gains priority every this many seconds so it eventually runs"
            settingKey="sjf_aging_factor"
            min={60} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Freshness vs. Speed Balance"
            sublabel="aoi_weight · 0 = schedule by estimated duration only; 1 = weight waiting time equally"
            settingKey="aoi_weight"
            min={0.0} max={1.0} step="0.05"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 10. Interrupt for Urgent Jobs */}
      {/* What it shows: Whether the daemon is allowed to pause a low-priority running job
       *   so a higher-priority job can run first, and the guard rails around doing so.
       * Decision it drives: Enable preemption for latency-sensitive high-priority jobs;
       *   tune the window and per-job cap to avoid starving any single job. */}
      <div class="t-frame" data-label="Interrupt for Urgent Jobs — let high-priority jobs cut the line">
        <div class="flex flex-col gap-3">
          <ToggleRow
            label="Allow Interrupting Running Jobs"
            sublabel="preemption_enabled · a high-priority job can pause a lower-priority running job to go first"
            settingKey="preemption_enabled"
            settings={settings} flashKey={flashKey} onSave={onSave} flash={flash}
          />
          <NumberRow
            label="Max Time a Job Can Be Interrupted"
            sublabel="preemption_window_seconds · only pause a running job if it started less than this many seconds ago"
            settingKey="preemption_window_seconds"
            min={10} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Interrupt Limit Per Job"
            sublabel="max_preemptions_per_job · a single job can be interrupted at most this many times total (0 = no limit)"
            settingKey="max_preemptions_per_job"
            min={0} max={10}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 11. Anomaly Detection */}
      {/* What it shows: Thresholds for detecting unusual spikes in queue activity — the
       *   rolling window size, how many standard deviations above baseline triggers an alert,
       *   and whether low-priority jobs are suspended during a spike.
       * Decision it drives: Lower sigma = catch subtle anomalies earlier but more false
       *   alarms. Raise it to reduce noise during normal traffic variance. */}
      <div class="t-frame" data-label="Anomaly Detection — spot unusual spikes in queue activity">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          Watches for job failure patterns that are statistically unusual — like 10 failures
          in 5 minutes when the normal rate is 1 per hour.
        </p>
        <div class="flex flex-col gap-3">
          <NumberRow
            label="Detection Window (jobs)"
            sublabel="entropy_alert_window · how many recent jobs to watch for unusual patterns"
            settingKey="entropy_alert_window"
            min={5} max={120}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Alert Sensitivity"
            sublabel="entropy_alert_sigma (σ) · how many standard deviations above normal triggers an alert — lower = more sensitive"
            settingKey="entropy_alert_sigma"
            min={0.5} max={5.0} step="0.1" unit="σ"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <ToggleRow
            label="Pause Background Jobs During Spikes"
            sublabel="entropy_suspend_low_priority · hold low-priority work when an anomaly is detected"
            settingKey="entropy_suspend_low_priority"
            settings={settings} flashKey={flashKey} onSave={onSave} flash={flash}
          />
        </div>
      </div>

      {/* 13. DLQ Auto-Reschedule */}
      {/* What it shows: Whether the system automatically retries dead-letter jobs in quieter
       *   time slots, how often the sweep runs, and how many failures before giving up.
       * Decision it drives: Turn off auto-reschedule if you want full manual control of failed
       *   jobs. Raise the chronic threshold if flaky jobs need more chances. */}
      <div class="t-frame" data-label="DLQ Auto-Reschedule — automatically retry failed jobs in quiet slots">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          When a job lands in the Dead Letter Queue, the system can automatically find a low-load
          time slot and reschedule it. Jobs that fail repeatedly are marked "chronic" and left alone.
        </p>
        <div class="flex flex-col gap-3">
          <ToggleRow
            label="Enable Auto-Reschedule"
            sublabel="dlq.auto_reschedule · automatically retry DLQ entries in optimal time slots"
            settingKey="dlq.auto_reschedule"
            settings={settings} flashKey={flashKey} onSave={onSave} flash={flash}
          />
          <NumberRow
            label="Sweep Interval"
            sublabel="dlq.sweep_fallback_minutes · how often the system checks for reschedulable DLQ entries"
            settingKey="dlq.sweep_fallback_minutes"
            min={5} max={240} unit="min"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Chronic Failure Threshold"
            sublabel="dlq.chronic_failure_threshold · after this many reschedule attempts, stop trying"
            settingKey="dlq.chronic_failure_threshold"
            min={1} max={20}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 14. Proactive Deferral */}
      {/* What it shows: Whether the system proactively defers jobs when resources are tight,
       *   GPU thermal thresholds, and burst priority cutoff.
       * Decision it drives: Turn off deferral if you always want jobs to queue normally.
       *   Lower thermal threshold if GPU overheating is a concern. */}
      <div class="t-frame" data-label="Proactive Deferral — pause jobs when resources are tight">
        <p style="font-size: var(--type-label); color: var(--text-tertiary); margin: 0 0 0.75rem;">
          Instead of letting low-priority jobs fail, the system can defer them until resources free
          up — like waiting for GPU temperature to drop or memory to clear.
        </p>
        <div class="flex flex-col gap-3">
          <ToggleRow
            label="Enable Deferral"
            sublabel="defer.enabled · proactively defer jobs when system resources are constrained"
            settingKey="defer.enabled"
            settings={settings} flashKey={flashKey} onSave={onSave} flash={flash}
          />
          <NumberRow
            label="Burst Priority Threshold"
            sublabel="defer.burst_priority_threshold · during burst regime, defer jobs above this priority number (lower = more important)"
            settingKey="defer.burst_priority_threshold"
            min={1} max={10}
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="GPU Thermal Limit"
            sublabel="defer.thermal_threshold_c · defer jobs when GPU temperature exceeds this"
            settingKey="defer.thermal_threshold_c"
            min={60} max={100} unit="°C"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
          <NumberRow
            label="Resource Wait Timeout"
            sublabel="defer.resource_wait_timeout_s · how long to wait for resources before deferring"
            settingKey="defer.resource_wait_timeout_s"
            min={10} max={600} unit="sec"
            settings={settings} flashKey={flashKey} onBlur={handleBlur}
          />
        </div>
      </div>

      {/* 12. Daemon Controls */}
      {/* What it shows: Manual pause/resume for the queue daemon, plus its current state.
       * Decision it drives: Pause the queue to perform maintenance or free up resources,
       *   then resume when ready. Changes take effect immediately. */}
      <div class="t-frame" data-label="Daemon Controls — manually pause or resume the queue">
        <div class="flex flex-col gap-2">
          <div class="flex items-center gap-3">
            {isPaused ? (
              <button
                class="t-btn t-btn-primary px-4 py-2 text-sm"
                disabled={resumeFb?.phase === 'loading'}
                onClick={onResume}
              >
                {resumeFb?.phase === 'loading' ? 'Resuming…' : 'Resume the Queue'}
              </button>
            ) : (
              <button
                class="t-btn t-btn-secondary px-4 py-2 text-sm"
                disabled={pauseFb?.phase === 'loading'}
                onClick={onPause}
              >
                {pauseFb?.phase === 'loading' ? 'Pausing…' : 'Pause the Queue'}
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

/**
 * Paired pause/resume threshold inputs side by side.
 * Accepts an optional description shown below the section label.
 */
function ThresholdPair({ label, sublabel, description, pauseKey, resumeKey, unit, step, settings, flashKey, onBlur }) {
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
 * Number row: label + optional sublabel description + input + optional unit.
 */
function NumberRow({ label, sublabel, settingKey, min, max, step, unit, settings, flashKey, onBlur }) {
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
function SelectRow({ label, sublabel, settingKey, options, optionLabels, settings, flashKey, onSave, flash }) {
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
function ToggleRow({ label, sublabel, settingKey, settings, flashKey, onSave, flash }) {
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
