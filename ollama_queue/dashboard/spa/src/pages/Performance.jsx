import { useEffect } from 'preact/hooks';
import {
    modelPerformance, performanceCurve,
    fetchModelPerformance, fetchPerformanceCurve,
} from '../stores';
import { SystemHealth } from '../components/SystemHealth.jsx';
import PerformanceCurveChart from '../components/PerformanceCurveChart.jsx';
import LoadHeatmap from '../components/LoadHeatmap.jsx';
import PageBanner from '../components/PageBanner.jsx';
import ModelChip from '../components/ModelChip.jsx';

// What it shows: Model-level performance data — how fast each model generates tokens,
//   how long each takes to warm up, how many times each has run — plus a fitted regression
//   curve showing the relationship between model size and throughput.
// Decision it drives: Which models are fast enough for interactive use? Which are slow and
//   should only be used for batch jobs? Is a new model underperforming its size class?

export default function Performance() {
    const stats = modelPerformance.value;
    const curve = performanceCurve.value;

    useEffect(() => {
        fetchModelPerformance();
        fetchPerformanceCurve();
    }, []);

    // Convert stats object { model_name: { run_count, avg_tok_per_min, ... } } to sorted array
    const models = stats
        ? Object.entries(stats)
              .map(([name, data]) => ({ name, ...data }))
              .sort((a, b) => (b.avg_tok_per_min || 0) - (a.avg_tok_per_min || 0))
        : [];

    return (
        <div class="flex flex-col gap-6 animate-page-enter">
            <PageBanner title="Performance" subtitle="model benchmarks and system metrics" />

            {/* System Health — always visible at top */}
            <SystemHealth />

            {/* Model Performance Table */}
            <div class="t-frame" data-label="Model Performance">
                {models.length === 0 ? (
                    <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
                        No performance data yet — run some jobs first
                    </p>
                ) : (
                    <div style="overflow-x: auto;">
                        <table style={{
                            width: '100%',
                            borderCollapse: 'collapse',
                            fontFamily: 'var(--font-mono)',
                            fontSize: 'var(--type-label)',
                        }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                    <th style={thStyle}>Model</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Runs</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>tok/min</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Warmup</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Size</th>
                                </tr>
                            </thead>
                            <tbody>
                                {models.map(model => (
                                    <tr key={model.name} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                        <td style={tdStyle}><ModelChip model={model.name} /></td>
                                        <td style={tdRight}>{model.run_count}</td>
                                        <td style={tdRight}>
                                            {model.avg_tok_per_min != null
                                                ? model.avg_tok_per_min.toFixed(0)
                                                : '—'}
                                        </td>
                                        <td style={tdRight}>
                                            {model.avg_warmup_s != null
                                                ? `${model.avg_warmup_s.toFixed(1)}s`
                                                : '—'}
                                        </td>
                                        <td style={tdRight}>
                                            {model.model_size_gb != null
                                                ? `${model.model_size_gb.toFixed(1)} GB`
                                                : '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Performance Curve — tok/min vs model size */}
            <p style="font-size: var(--type-label); color: var(--text-secondary); margin-bottom: 8px;">
                Jobs completed per hour. Use this to estimate capacity for batch workloads.
            </p>
            <PerformanceCurveChart curve={curve} models={models} />

            {/* Load Heatmap — hour × day-of-week */}
            <p style="font-size: var(--type-label); color: var(--text-secondary); margin-bottom: 8px;">
                Activity by hour of day and day of week. Darker cells mean more jobs ran during that window.
            </p>
            <LoadHeatmap />
        </div>
    );
}

const thStyle = {
    padding: '0.4rem 0.5rem',
    textAlign: 'left',
    color: 'var(--text-tertiary)',
    fontWeight: 600,
    whiteSpace: 'nowrap',
};
const tdStyle = {
    padding: '0.4rem 0.5rem',
    color: 'var(--text-primary)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: '200px',
};
const tdRight = { ...tdStyle, textAlign: 'right' };
