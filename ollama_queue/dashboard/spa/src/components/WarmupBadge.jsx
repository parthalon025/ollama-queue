import { h } from 'preact';

// What it shows: Whether a running job is still loading the AI model (warming up)
//   or actively generating output. For completed jobs, shows the warmup/generation breakdown.
// Decision it drives: Lets the user know if wait time is model loading vs actual
//   generation, so they can judge whether the job is progressing normally.

export function WarmupBadge({ job, metrics }) {
    // Running job: show phase based on elapsed time vs typical warmup
    if (job && job.status === 'running') {
        const elapsed = (Date.now() / 1000) - (job.started_at || 0);
        // If metrics exist from a previous run of same model, use avg warmup
        const avgWarmup = metrics?.avg_warmup_s;
        const isWarming = avgWarmup ? elapsed < avgWarmup : elapsed < 5;

        return (
            <span class={`warmup-badge warmup-badge--${isWarming ? 'warming' : 'generating'}`}>
                {isWarming ? 'Warming up' : 'Generating'}
            </span>
        );
    }

    // Completed job with metrics: show breakdown
    if (metrics && metrics.load_duration_ns != null && metrics.eval_duration_ns != null) {
        const warmupS = (metrics.load_duration_ns / 1e9).toFixed(1);
        const genS = (metrics.eval_duration_ns / 1e9).toFixed(1);
        return (
            <span class="warmup-badge warmup-badge--complete">
                Warmup: {warmupS}s | Generation: {genS}s
            </span>
        );
    }

    return null;
}
