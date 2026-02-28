import { h } from 'preact';
import { status, queue, history, healthData, durationData, heatmapData, settings } from '../store';
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

  const stalledJobs = (q || []).filter(j => j.stall_detected_at && j.status === 'running');

  return (
    <div class="flex flex-col gap-4 animate-page-enter">
      {/* Stall alert banner */}
      {stalledJobs.length > 0 && (
        <div style={{ background: '#7c2d12', color: '#fff', padding: '0.5rem 1rem',
                      borderRadius: 4 }}>
          ⚠ {stalledJobs.length} job{stalledJobs.length > 1 ? 's' : ''} may be stalled. Check Queue tab.
        </div>
      )}

      {/* 1. Current Job — always visible */}
      <CurrentJob daemon={daemon} currentJob={currentJob} latestHealth={latestHealth} settings={sett} />

      {/* 2. Queue — collapsible, default open if jobs pending */}
      <CollapsibleSection title="Queue" defaultOpen={q && q.length > 0} summary={`${(q || []).length} pending`}>
        <QueueList jobs={q} />
      </CollapsibleSection>

      {/* 3. Hero Cards — 4-up KPI grid */}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <HeroCard
          label="Jobs / 24h"
          value={kpis ? kpis.jobs_24h : '--'}
        />
        <HeroCard
          label="Avg Wait"
          value={kpis ? formatWaitReadable(kpis.avg_wait_seconds) : '--'}
        />
        <HeroCard
          label="Pause Time"
          value={kpis ? `${kpis.pause_minutes_24h}` : '--'}
          unit="min"
          warning={kpis && kpis.pause_minutes_24h > 30}
        />
        <HeroCard
          label="Success Rate"
          value={kpis ? `${Math.round(kpis.success_rate_7d * 100)}` : '--'}
          unit="%"
          warning={kpis && kpis.success_rate_7d < 0.9}
        />
      </div>

      {/* 4. Resource Trends — 3 small TimeChart multiples */}
      <CollapsibleSection title="Resource Trends" defaultOpen={false} summary={health && health.length > 0 ? `${health.length} samples` : 'no data'}>
        {health && health.length > 0 ? (
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
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
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
