import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { status, queue, history, healthData, durationData, heatmapData, settings, connectionStatus } from '../store';
import CurrentJob from '../components/CurrentJob.jsx';
import QueueList from '../components/QueueList.jsx';
import HeroCard from '../components/HeroCard.jsx';
import CollapsibleSection from '../components/CollapsibleSection.jsx';
import TimeChart from '../components/TimeChart.jsx';
import ActivityHeatmap from '../components/ActivityHeatmap.jsx';
import HistoryList from '../components/HistoryList.jsx';

export default function Dashboard() {
  const st = status.value;
  const q = queue.value;
  const hist = history.value;
  const health = healthData.value;
  const durations = durationData.value;
  const heatmap = heatmapData.value;
  const sett = settings.value;

  const daemon = st ? st.daemon : null;
  const kpis = st ? st.kpis : null;
  const currentJob = st ? st.current_job : null;

  // Latest health entry for resource gauges (health rows are DESC, first = newest)
  const latestHealth = health && health.length > 0 ? health[0] : null;

  const isStalled = !!(currentJob && currentJob.stall_detected_at);

  // #9 — live/disconnected timestamp ticker
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <div class="flex flex-col gap-4 animate-page-enter">
      {/* Stall alert banner — current running job only */}
      {isStalled && (
        <div style={{ background: '#7c2d12', color: '#fff', padding: '0.5rem 1rem',
                      borderRadius: 4 }}>
          ⚠ Running job #{currentJob.id} ({currentJob.source}) may be stalled. Check stall action in Settings.
        </div>
      )}

      {/* #2 — Disconnected banner */}
      {connectionStatus.value === 'disconnected' && (
        <div style={{ background: '#1c1917', color: '#f97316', padding: '0.5rem 1rem',
                      borderRadius: 4, border: '1px solid rgba(249,115,22,0.4)' }}>
          ⚠ Disconnected — retrying...
        </div>
      )}

      {/* 1. Current Job — always visible */}
      <CurrentJob daemon={daemon} currentJob={currentJob} latestHealth={latestHealth} settings={sett} />

      {/* 2. Queue — collapsible, default open if jobs pending */}
      <CollapsibleSection title="Queue" defaultOpen={q && q.length > 0} summary={`${(q || []).length} pending${currentJob ? ' • 1 running' : ''}`}>
        <QueueList jobs={q} currentJob={currentJob} />
      </CollapsibleSection>

      {/* 3. Hero Cards — 4-up KPI grid */}
      {/* #9 — live/disconnected indicator */}
      <div style="text-align: right; margin-bottom: -8px;">
        <span class="data-mono" style={`font-size: var(--type-micro); color: ${connectionStatus.value === 'ok' ? 'var(--status-healthy)' : 'var(--status-error)'};`}>
          ● {connectionStatus.value === 'ok' ? 'live' : 'disconnected'}
        </span>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* #5 — HeroCard sparklines */}
        <HeroCard
          label="Jobs / 24h"
          value={kpis ? kpis.jobs_24h : '--'}
          sparkData={buildHealthSparkline(health, 'ram_pct')}
          sparkColor="var(--accent)"
        />
        <HeroCard
          label="Avg Wait"
          value={kpis ? formatWaitReadable(kpis.avg_wait_seconds) : '--'}
          sparkData={buildDurationSparkline(durations)}
          sparkColor="var(--accent)"
        />
        <HeroCard
          label="Pause Time"
          value={kpis ? `${kpis.pause_minutes_24h}` : '--'}
          unit="min"
          warning={kpis && kpis.pause_minutes_24h > 30}
          sparkData={buildHealthSparkline(health, 'ram_pct')}
          sparkColor="var(--status-warning)"
        />
        <HeroCard
          label="Success Rate"
          value={kpis ? `${Math.round(kpis.success_rate_7d * 100)}` : '--'}
          unit="%"
          warning={kpis && kpis.success_rate_7d < 0.9}
          delta={kpis ? buildSuccessRateDelta(kpis, hist) : null}
        />
      </div>

      {/* 4. Resource Trends — 4 small TimeChart multiples (#4 — added Swap chart) */}
      <CollapsibleSection title="Resource Trends" defaultOpen={false} summary={health && health.length > 0 ? `${health.length} samples` : 'no data'}>
        {health && health.length > 0 ? (
          <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div class="t-frame" data-label="RAM %">
              <TimeChart
                data={buildHealthSeries(health, 'ram_pct')}
                series={[{ label: 'RAM', color: 'var(--accent)', width: 1.5 }]}
                height={100}
              />
            </div>
            <div class="t-frame" data-label="VRAM %">
              <TimeChart
                data={buildHealthSeries(health, 'vram_pct')}
                series={[{ label: 'VRAM', color: 'var(--status-warning)', width: 1.5 }]}
                height={100}
              />
            </div>
            <div class="t-frame" data-label="Load">
              <TimeChart
                data={buildHealthSeries(health, 'load_avg')}
                series={[{ label: 'Load', color: 'var(--accent-purple)', width: 1.5 }]}
                height={100}
              />
            </div>
            <div class="t-frame" data-label="Swap %">
              <TimeChart
                data={buildHealthSeries(health, 'swap_pct')}
                series={[{ label: 'Swap', color: '#a78bfa', width: 1.5 }]}
                height={100}
              />
            </div>
          </div>
        ) : (
          <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">No health data yet</p>
        )}
      </CollapsibleSection>

      {/* 5. Duration Trends — small multiples by source */}
      <CollapsibleSection title="Duration Trends" defaultOpen={false} summary={durations && durations.length > 0 ? `${durations.length} records` : 'no data'}>
        {durations && durations.length > 0 ? (
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            {buildDurationBySources(durations).map(({ source, data }) => (
              <div key={source} class="t-frame" data-label={source}>
                <TimeChart
                  data={data}
                  series={[{ label: source, color: 'var(--accent)', width: 1.5 }]}
                  height={100}
                />
              </div>
            ))}
          </div>
        ) : (
          <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">No duration data yet</p>
        )}
      </CollapsibleSection>

      {/* 6. Activity Heatmap */}
      <CollapsibleSection title="Activity" defaultOpen={false} summary={heatmap && heatmap.length > 0 ? 'last 7 days' : 'no data'}>
        <ActivityHeatmap data={heatmap} />
      </CollapsibleSection>

      {/* 7. History */}
      <CollapsibleSection title="History" defaultOpen={true} summary={`${(hist || []).length} jobs`}>
        <HistoryList jobs={hist} />
      </CollapsibleSection>
    </div>
  );
}

/**
 * Build uPlot-compatible [timestamps[], values[]] from health_log rows.
 * Health rows come sorted DESC from API — reverse for chronological order.
 */
function buildHealthSeries(rows, field) {
  if (!rows || rows.length === 0) return [[], []];
  const sorted = [...rows].reverse();
  const ts = sorted.map((r) => r.timestamp);
  const vals = sorted.map((r) => r[field] ?? null);
  return [ts, vals];
}

/**
 * Group duration_history rows by source, build uPlot series per source.
 */
function buildDurationBySources(rows) {
  const bySource = {};
  for (const r of rows) {
    const s = r.source || 'unknown';
    if (!bySource[s]) bySource[s] = [];
    bySource[s].push(r);
  }
  return Object.entries(bySource).map(([source, items]) => {
    const sorted = [...items].sort((a, b) => a.recorded_at - b.recorded_at);
    const ts = sorted.map((r) => r.recorded_at);
    const vals = sorted.map((r) => r.duration);
    return { source, data: [ts, vals] };
  });
}

/**
 * Format seconds into human-readable wait time.
 */
function formatWaitReadable(seconds) {
  if (seconds === null || seconds <= 0) return '0s';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const hr = Math.floor(m / 60);
  return `${hr}h ${m % 60}m`;
}

/**
 * #5 — Build sparkline data from health_log rows for a given field.
 * Returns uPlot-compatible [timestamps[], values[]] in chronological order,
 * or null if insufficient data.
 */
function buildHealthSparkline(rows, field) {
  if (!rows || rows.length < 2) return null;
  const sorted = [...rows].reverse();
  return [sorted.map((r) => r.timestamp), sorted.map((r) => r[field] ?? null)];
}

/**
 * #5 — Build sparkline data from duration_history rows.
 * Takes the last 24 records sorted chronologically.
 * Returns uPlot-compatible [timestamps[], values[]], or null if insufficient data.
 */
function buildDurationSparkline(rows) {
  if (!rows || rows.length < 2) return null;
  const sorted = [...rows].sort((a, b) => a.recorded_at - b.recorded_at).slice(-24);
  return [sorted.map((r) => r.recorded_at), sorted.map((r) => r.duration)];
}

/**
 * Build a plain-English explanation + recommendation for the Success Rate card.
 * Uses recent history to detect failure patterns (timeouts, stalls, crashes).
 */
function buildSuccessRateDelta(kpis, hist) {
  const ok = kpis.jobs_7d_ok ?? 0;
  const bad = kpis.jobs_7d_bad ?? 0;
  const total = ok + bad;
  if (total === 0) return 'no jobs run in the last 7 days';
  if (bad === 0) return 'everything is running clean';

  const sevenDaysAgo = Date.now() / 1000 - 7 * 86400;
  const recentFails = (hist || []).filter(
    (j) => (j.status === 'failed' || j.status === 'killed') && j.completed_at >= sevenDaysAgo
  );

  const timeouts = recentFails.filter(
    (j) => j.outcome_reason && /timeout/i.test(j.outcome_reason)
  );
  const stalls = recentFails.filter((j) => j.stall_detected_at);
  const crashes = recentFails.filter(
    (j) => j.outcome_reason && /exit code [^0]|non.zero|crash|error/i.test(j.outcome_reason)
  );

  const n = bad;
  const s = n === 1 ? '' : 's';

  if (timeouts.length > 0 && timeouts.length >= recentFails.length / 2) {
    return `${n} job${s} ran past their time limit — raise Default Timeout in Settings`;
  }
  if (stalls.length > 0 && stalls.length >= recentFails.length / 2) {
    return `${n} job${s} appeared stuck and were killed — review Stall Detection in Settings`;
  }
  if (crashes.length > 0 && crashes.length >= recentFails.length / 2) {
    return `${n} job${s} crashed with an error — check History for the command output`;
  }
  if (bad === 1) return '1 job failed — tap History to see what went wrong';
  return `${n} jobs failed this week — check History or DLQ for patterns`;
}
