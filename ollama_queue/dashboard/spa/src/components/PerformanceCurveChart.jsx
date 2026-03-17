
// What it shows: A scatter plot of model size (GB) vs throughput (tok/min) with a fitted
//   log-linear regression line and 90% confidence band. Point size encodes run count.
// Decision it drives: Is a model performing above or below expected for its size class?
//   Points above the line are overperforming; points below may indicate configuration issues.

const W = 560, H = 240, PAD = { top: 20, right: 20, bottom: 40, left: 55 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

function logScale(val, min, max) {
    if (val <= 0 || min <= 0) return 0;
    return (Math.log(val) - Math.log(min)) / (Math.log(max) - Math.log(min));
}

function linScale(val, min, max) {
    if (max === min) return 0.5;
    return (val - min) / (max - min);
}

export default function PerformanceCurveChart({ curve, models }) {
    if (!models || models.length === 0) return null;

    const withSize = models.filter(m => m.model_size_gb > 0 && m.avg_tok_per_min > 0);
    if (withSize.length === 0) return null;

    const sizes = withSize.map(m => m.model_size_gb);
    const rates = withSize.map(m => m.avg_tok_per_min);
    const counts = withSize.map(m => m.run_count || 1);
    const maxCount = Math.max(...counts);

    const sizeMin = Math.min(...sizes) * 0.7;
    const sizeMax = Math.max(...sizes) * 1.4;
    const rateMin = 0;
    const rateMax = Math.max(...rates) * 1.2;

    function toX(gb) { return PAD.left + logScale(gb, sizeMin, sizeMax) * PLOT_W; }
    function toY(tok) { return PAD.top + (1 - linScale(tok, rateMin, rateMax)) * PLOT_H; }

    // Generate regression line + CI band if curve is fitted.
    // API shape: { tok_slope, tok_intercept, tok_residual_std, fitted, points: [...] }
    let linePath = '';
    let bandPath = '';
    if (curve && curve.fitted && curve.tok_slope != null) {
        const steps = 50;
        const linePoints = [];
        const upperPoints = [];
        const lowerPoints = [];
        const std = curve.tok_residual_std || 0.3;
        const z = 1.28; // 90% CI

        for (let i = 0; i <= steps; i++) {
            const t = i / steps;
            const gb = Math.exp(Math.log(sizeMin) + t * (Math.log(sizeMax) - Math.log(sizeMin)));
            const logRate = curve.tok_slope * Math.log(gb) + curve.tok_intercept;
            const mean = Math.exp(logRate);
            const lower = Math.exp(logRate - z * std);
            const upper = Math.exp(logRate + z * std);

            linePoints.push(`${toX(gb).toFixed(1)},${toY(mean).toFixed(1)}`);
            upperPoints.push(`${toX(gb).toFixed(1)},${toY(upper).toFixed(1)}`);
            lowerPoints.push(`${toX(gb).toFixed(1)},${toY(lower).toFixed(1)}`);
        }

        linePath = `M${linePoints.join('L')}`;
        bandPath = `M${upperPoints.join('L')}L${lowerPoints.reverse().join('L')}Z`;
    }

    // X-axis ticks at nice log intervals
    const xTicks = [0.5, 1, 2, 4, 8, 16, 32, 64].filter(v => v >= sizeMin && v <= sizeMax);
    // Y-axis ticks
    const yStep = niceStep(rateMax, 5);
    const yTicks = [];
    for (let v = 0; v <= rateMax; v += yStep) yTicks.push(v);

    return (
        <div class="t-frame" data-label="Throughput vs Model Size">
            <svg viewBox={`0 0 ${W} ${H}`} style="width: 100%; max-width: 560px; height: auto;">
                {/* Grid lines — horizontal only (Tufte: minimize non-data ink) */}
                {yTicks.map(v => (
                    <line
                        key={v}
                        x1={PAD.left} x2={PAD.left + PLOT_W}
                        y1={toY(v)} y2={toY(v)}
                        stroke="var(--border-subtle)" stroke-width="0.5"
                    />
                ))}

                {/* CI band */}
                {bandPath && (
                    <path d={bandPath} fill="var(--accent)" opacity="0.12" />
                )}

                {/* Regression line */}
                {linePath && (
                    <path d={linePath} fill="none" stroke="var(--accent)" stroke-width="1.5" opacity="0.7" />
                )}

                {/* Data points */}
                {withSize.map((model, idx) => {
                    const r = 3 + Math.sqrt(counts[idx] / maxCount) * 6;
                    return (
                        <circle
                            key={model.name}
                            cx={toX(sizes[idx])}
                            cy={toY(rates[idx])}
                            r={r}
                            fill="var(--accent)"
                            opacity="0.8"
                        >
                            <title>{`${model.name}\n${sizes[idx].toFixed(1)} GB · ${rates[idx].toFixed(0)} tok/min · ${counts[idx]} runs`}</title>
                        </circle>
                    );
                })}

                {/* X axis labels */}
                {xTicks.map(v => (
                    <text
                        key={v}
                        x={toX(v)} y={PAD.top + PLOT_H + 18}
                        text-anchor="middle"
                        fill="var(--text-tertiary)" font-size="10" font-family="var(--font-mono)"
                    >
                        {v}GB
                    </text>
                ))}

                {/* Y axis labels */}
                {yTicks.map(v => (
                    <text
                        key={v}
                        x={PAD.left - 6} y={toY(v) + 3}
                        text-anchor="end"
                        fill="var(--text-tertiary)" font-size="10" font-family="var(--font-mono)"
                    >
                        {v}
                    </text>
                ))}

                {/* Axis labels */}
                <text
                    x={PAD.left + PLOT_W / 2} y={H - 4}
                    text-anchor="middle"
                    fill="var(--text-secondary)" font-size="11" font-family="var(--font-mono)"
                >
                    Model Size (GB, log scale)
                </text>
                <text
                    x={12} y={PAD.top + PLOT_H / 2}
                    text-anchor="middle"
                    fill="var(--text-secondary)" font-size="11" font-family="var(--font-mono)"
                    transform={`rotate(-90, 12, ${PAD.top + PLOT_H / 2})`}
                >
                    tok/min
                </text>
            </svg>
        </div>
    );
}

function niceStep(max, targetTicks) {
    const rough = max / targetTicks;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const normalized = rough / mag;
    if (normalized <= 1) return mag;
    if (normalized <= 2) return 2 * mag;
    if (normalized <= 5) return 5 * mag;
    return 10 * mag;
}
