import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

/**
 * What it shows: RAM, VRAM, CPU load, and swap — the four metrics the health monitor watches
 *   before deciding to pause the queue. Bar color: blue=healthy, orange=approaching the pause
 *   threshold, red=past the pause threshold (queue will stall if a new job tries to start).
 *   The dashed vertical marker on each bar shows exactly where the pause threshold sits.
 * Decision it drives: Is the system healthy enough to start the next job? If bars are orange
 *   or red, the queue is about to auto-pause — check Settings to adjust thresholds.
 *
 * @param {{ ram: number, vram: number, load: number, swap: number, settings: object }} props
 *   settings shape: { ram_pause_pct, ram_resume_pct, vram_pause_pct, vram_resume_pct, ... }
 */
export default function ResourceGauges({ ram, vram, load, swap, settings }) {
  const s = settings || {};
  const containerRef = useRef(null);

  const ramPause = s.ram_pause_pct || 85;
  const vramPause = s.vram_pause_pct || 90;
  const isOverThreshold = (ram ?? 0) >= ramPause || (vram ?? 0) >= vramPause;

  // ThreatPulse: red glow when RAM or VRAM hits the pause threshold.
  // Signals that the queue is about to auto-pause — this is urgent, not informational.
  useEffect(() => {
    if (!containerRef.current) return;
    if (isOverThreshold) {
      containerRef.current.setAttribute('data-sh-effect', 'threat-pulse');
    } else {
      containerRef.current.removeAttribute('data-sh-effect');
    }
  }, [isOverThreshold]);

  // Plain-English explanations for each resource gauge (ARIA "Explain like I'm 5")
  const GAUGE_TOOLTIPS = {
    ram:  'System RAM in use. Above the pause threshold, the daemon stops accepting new jobs.',
    vram: 'GPU memory in use by Ollama. Near 100% causes model loading failures — most common bottleneck.',
    load: '1-minute system load average. Values above CPU count indicate the system is overloaded.',
    swap: 'Swap (disk memory) in use. Non-zero swap on a machine with adequate RAM signals memory pressure.',
  };

  const gauges = [
    { label: 'RAM',  title: GAUGE_TOOLTIPS.ram,  value: ram,  pause: s.ram_pause_pct || 85,                              resume: s.ram_resume_pct || 75 },
    { label: 'GPU',  title: GAUGE_TOOLTIPS.vram, value: vram, pause: s.vram_pause_pct || 90,                             resume: s.vram_resume_pct || 80 },
    { label: 'CPU',  title: GAUGE_TOOLTIPS.load, value: load, pause: (s.load_pause_multiplier || 2) * 100,              resume: (s.load_resume_multiplier || 1.5) * 100 },
    { label: 'Swap', title: GAUGE_TOOLTIPS.swap, value: swap, pause: s.swap_pause_pct || 50,                             resume: s.swap_resume_pct || 40 },
  ];

  return (
    <div ref={containerRef} class="flex gap-3 flex-wrap">
      {gauges.map((g) => {
        const raw = g.value ?? 0;
        const pct = Math.min(100, Math.max(0, raw));
        let color = 'var(--accent)';
        // Use raw (unclamped) value for color — CPU load can exceed 100% of one core's worth,
        // and the bar must turn warning/error even when the bar width is capped at 100%.
        if (raw >= g.pause) color = 'var(--status-error)';
        else if (raw >= g.resume) color = 'var(--status-warning)';

        return (
          <div key={g.label} title={g.title} class="flex items-center gap-1" style="min-width: 80px; flex: 1;">
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); width: 32px; text-align: right;">
              {g.label}
            </span>
            <div style="flex: 1; height: 6px; background: var(--bg-inset); border-radius: 3px; position: relative; overflow: hidden;">
              {/* Pause threshold marker */}
              <div
                title="Pause threshold — the queue stops starting new jobs above this level"
                style={{
                  position: 'absolute',
                  left: `${g.pause}%`,
                  top: 0,
                  bottom: 0,
                  width: '1px',
                  borderLeft: '1px dashed var(--text-tertiary)',
                  opacity: 0.5,
                  zIndex: 1,
                }}
              />
              <div style={{
                width: `${pct}%`,
                height: '100%',
                background: color,
                borderRadius: '3px',
                transition: 'width 0.3s ease, background 0.3s ease',
              }} />
            </div>
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-secondary); width: 28px;">
              {Math.round(pct)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}
