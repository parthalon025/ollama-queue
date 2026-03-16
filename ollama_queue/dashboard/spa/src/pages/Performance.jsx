import { useEffect } from 'preact/hooks';
import {
    modelPerformance, performanceCurve,
    fetchModelPerformance, fetchPerformanceCurve,
    healthData,
} from '../stores';
import { backendMetrics, fetchBackendMetrics } from '../stores/health.js';
import { evalVariants, fetchEvalVariants } from '../stores/eval.js';
import { SystemHealth } from '../components/SystemHealth.jsx';
import PerformanceCurveChart from '../components/PerformanceCurveChart.jsx';
import LoadHeatmap from '../components/LoadHeatmap.jsx';
import { ShPageBanner, ShTimeChart } from 'superhot-ui/preact';
import { TAB_CONFIG } from '../config/tabs.js';
import ModelChip from '../components/ModelChip.jsx';
import F1Score from '../components/F1Score.jsx';

// What it shows: Model-level performance data — how fast each model generates tokens,
//   how long each takes to warm up, how many times each has run — plus a fitted regression
//   curve showing the relationship between model size and throughput.
// Decision it drives: Which models are fast enough for interactive use? Which are slow and
//   should only be used for batch jobs? Is a new model underperforming its size class?

export default function Performance() {
    const _tab = TAB_CONFIG.find(t => t.id === 'performance');
    const stats = modelPerformance.value;
    const curve = performanceCurve.value;
    const backends = backendMetrics.value;

    // What it shows: RAM usage trend over the last 24h from the health log.
    // Decision it drives: Is RAM pressure increasing over time? Should concurrency
    //   be reduced or a job deferred to avoid an OOM condition?
    const ramChartData = (healthData.value || [])
        .filter(entry => entry.timestamp != null && entry.ram_pct != null)
        .map(entry => ({ t: entry.timestamp, v: entry.ram_pct }))
        .reverse(); // healthData is newest-first; ShTimeChart expects oldest-first

    // What it shows: Which eval variant is currently in production and which model it uses as judge.
    // Decision it drives: User can see the eval judge model's performance context alongside
    //   benchmark data — confirming the right model is judging quality.
    const productionVariant = (evalVariants.value || []).find(v => v.is_production);
    const judgeModel = productionVariant?.judge_model;

    useEffect(() => {
        fetchModelPerformance();
        fetchPerformanceCurve();
        fetchBackendMetrics();
        fetchEvalVariants(); // needed for judge model annotation when landing directly on this tab
    }, []);

    // Convert stats object { model_name: { run_count, avg_tok_per_min, ... } } to sorted array
    const models = stats
        ? Object.entries(stats)
              .map(([name, data]) => ({ name, ...data }))
              .sort((a, b) => (b.avg_tok_per_min || 0) - (a.avg_tok_per_min || 0))
        : [];

    return (
        <div class="flex flex-col gap-6 sh-stagger-children animate-page-enter">
            <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />

            {/* System Health — always visible at top */}
            <SystemHealth />

            {/* RAM usage trend — last 24h from health log */}
            {ramChartData.length > 0 && (
                <ShTimeChart
                    data={ramChartData}
                    label="RAM %"
                    color="var(--sh-phosphor)"
                />
            )}

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
                Throughput vs model size. Points above the curve are overperforming for their size class.
            </p>
            <PerformanceCurveChart curve={curve} models={models} />

            {judgeModel && (
                <div class="perf-eval-annotation">
                    ★ <strong>{judgeModel}</strong> is the current eval judge
                    {productionVariant?.latest_f1 != null && <> · <F1Score value={productionVariant.latest_f1} /></>}
                </div>
            )}

            {/* Per-Backend Throughput — which GPU is serving each model, and how fast */}
            {backends.length > 0 && (
                <div class="t-frame" data-label="Per-Backend Throughput">
                    {/* What it shows: How fast each configured GPU serves each model, measured in tokens/min.
                        Decision it drives: Which model to run on which backend? Is the remote GPU
                          worth routing to for a given model? Is a backend underperforming its size class? */}
                    <div style="overflow-x: auto;">
                        <table style={{
                            width: '100%',
                            borderCollapse: 'collapse',
                            fontFamily: 'var(--font-mono)',
                            fontSize: 'var(--type-label)',
                        }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                    <th style={thStyle}>Backend</th>
                                    <th style={thStyle}>Model</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Runs</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>tok/min</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Warmup</th>
                                </tr>
                            </thead>
                            <tbody>
                                {backends.map((row, i) => (
                                    <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                        <td style={tdStyle}>{_abbrevBackend(row.backend_url)}</td>
                                        <td style={tdStyle}><ModelChip model={row.model} /></td>
                                        <td style={tdRight}>{row.run_count}</td>
                                        <td style={tdRight}>
                                            {row.avg_tok_per_min != null
                                                ? row.avg_tok_per_min.toFixed(0)
                                                : '—'}
                                        </td>
                                        <td style={tdRight}>
                                            {row.avg_warmup_s != null
                                                ? `${row.avg_warmup_s.toFixed(1)}s`
                                                : '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Load Heatmap — hour × day-of-week */}
            <p style="font-size: var(--type-label); color: var(--text-secondary); margin-bottom: 8px;">
                Activity by hour of day and day of week. Darker cells mean more jobs ran during that window.
            </p>
            <LoadHeatmap />
        </div>
    );
}

function _abbrevBackend(url) {
    try {
        const u = new URL(url);
        const host = u.hostname;
        return (host === '127.0.0.1' || host === 'localhost') ? 'Local' : host;
    } catch {
        return url;
    }
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
