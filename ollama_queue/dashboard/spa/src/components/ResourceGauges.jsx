import { h } from 'preact';

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
  const gauges = [
    { label: 'RAM',  title: 'Main memory (RAM) — used by running programs',                value: ram,  pause: s.ram_pause_pct || 85,                              resume: s.ram_resume_pct || 75 },
    { label: 'GPU',  title: 'GPU memory (VRAM) — used by AI models',                       value: vram, pause: s.vram_pause_pct || 90,                             resume: s.vram_resume_pct || 80 },
    { label: 'CPU',  title: 'CPU workload — how busy the processor is',                    value: load, pause: (s.load_pause_multiplier || 2) * 50,                resume: (s.load_resume_multiplier || 1.5) * 50 },
    { label: 'Swap', title: 'Swap memory — disk used as overflow when RAM is full',        value: swap, pause: s.swap_pause_pct || 50,                             resume: s.swap_resume_pct || 40 },
  ];

  return (
    <div class="flex gap-3 flex-wrap">
      {gauges.map((g) => {
        const pct = Math.min(100, Math.max(0, g.value ?? 0));
        let color = 'var(--accent)';
        if (pct >= g.pause) color = 'var(--status-error)';
        else if (pct >= g.resume) color = 'var(--status-warning)';

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
