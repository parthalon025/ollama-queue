import { h } from 'preact';
import { useState } from 'preact/hooks';
import {
    status, queue, history, healthData, durationData, settings,
    dlqCount, connectionStatus, currentTab, refreshQueue,
} from '../store';
import CurrentJob from '../components/CurrentJob.jsx';
import QueueList from '../components/QueueList.jsx';
import HeroCard from '../components/HeroCard.jsx';
import ResourceGauges from '../components/ResourceGauges.jsx';
import SubmitJobModal from '../components/SubmitJobModal.jsx';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

export default function Now() {
    const st = status.value;
    const q = queue.value;
    const hist = history.value;
    const health = healthData.value;
    const durations = durationData.value;
    const sett = settings.value;
    const dlqCnt = dlqCount.value;

    const daemon = st?.daemon ?? null;
    const kpis = st?.kpis ?? null;
    const currentJob = st?.current_job ?? null;
    const latestHealth = health?.length > 0 ? health[0] : null;

    // Count failures in last 24h for alert strip
    const oneDayAgo = Date.now() / 1000 - 86400;
    const recentFailures = (hist || []).filter(
        job => (job.status === 'failed' || job.status === 'killed') && (job.completed_at ?? 0) >= oneDayAgo
    ).length;
    const showAlerts = dlqCnt > 0 || recentFailures > 0;

    const [toast, setToast] = useState(null);

    // Proxy mini-stat: count proxy calls in last 24h from history signal
    // (reuse the oneDayAgo already computed above for the alert strip)
    const proxyGenerate = (hist || []).filter(
        job => job.source === 'proxy:/api/generate' && (job.completed_at ?? 0) >= oneDayAgo
    ).length;
    const proxyEmbed = (hist || []).filter(
        job => job.source === 'proxy:/api/embed' && (job.completed_at ?? 0) >= oneDayAgo
    ).length;
    const showProxyStat = proxyGenerate > 0 || proxyEmbed > 0;

    function handleJobSubmitted(jobId) {
        setToast(`Job #${jobId} queued`);
        setTimeout(() => setToast(null), 2000);
        refreshQueue();
    }

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            {/* Disconnected banner */}
            {connectionStatus.value === 'disconnected' && (
                <div style={{
                    background: '#1c1917', color: '#f97316',
                    padding: '0.5rem 1rem', borderRadius: 4,
                    border: '1px solid rgba(249,115,22,0.4)',
                }}>
                    ⚠ Disconnected — retrying...
                </div>
            )}

            {/* 2-column layout: left = operations, right = health + KPIs */}
            <div class="now-grid">

                {/* LEFT: running job + queue */}
                <div class="flex flex-col gap-4">
                    {/* CurrentJob renders its own t-frame — no wrapper needed */}
                    <CurrentJob
                        daemon={daemon}
                        currentJob={currentJob}
                        latestHealth={latestHealth}
                        settings={sett}
                    />
                    {/* QueueList renders its own t-frame — no wrapper needed */}
                    <QueueList jobs={q} currentJob={currentJob} />
                </div>

                {/* RIGHT: alerts + resource gauges + KPI cards */}
                <div class="flex flex-col gap-4">
                    {/* Alert strip — only when something needs attention */}
                    {showAlerts && (
                        <div style={{
                            display: 'flex',
                            flexWrap: 'wrap',
                            gap: '0.5rem',
                            padding: '0.625rem 0.75rem',
                            background: 'var(--status-error-glow)',
                            border: '1px solid var(--status-error)',
                            borderRadius: 'var(--radius)',
                            alignItems: 'center',
                        }}>
                            <span style={{
                                fontSize: 'var(--type-label)',
                                color: 'var(--status-error)',
                                fontWeight: 700,
                                fontFamily: 'var(--font-mono)',
                                flexShrink: 0,
                            }}>
                                ⚠ ALERTS
                            </span>
                            {dlqCnt > 0 && (
                                <button
                                    onClick={() => { currentTab.value = 'history'; }}
                                    style={{
                                        fontSize: 'var(--type-label)',
                                        color: 'var(--status-error)',
                                        background: 'transparent',
                                        border: 'none',
                                        cursor: 'pointer',
                                        textDecoration: 'underline',
                                        fontFamily: 'var(--font-mono)',
                                        padding: 0,
                                    }}
                                >
                                    {dlqCnt} DLQ {dlqCnt === 1 ? 'entry' : 'entries'}
                                </button>
                            )}
                            {recentFailures > 0 && (
                                <button
                                    onClick={() => { currentTab.value = 'history'; }}
                                    style={{
                                        fontSize: 'var(--type-label)',
                                        color: 'var(--status-error)',
                                        background: 'transparent',
                                        border: 'none',
                                        cursor: 'pointer',
                                        textDecoration: 'underline',
                                        fontFamily: 'var(--font-mono)',
                                        padding: 0,
                                    }}
                                >
                                    {recentFailures} failure{recentFailures > 1 ? 's' : ''} today
                                </button>
                            )}
                        </div>
                    )}

                    {/* Resource gauges */}
                    {latestHealth && (
                        <div class="t-frame" data-label="Resources">
                            <ResourceGauges
                                ram={latestHealth.ram_pct}
                                vram={latestHealth.vram_pct}
                                load={latestHealth.load_avg}
                                swap={latestHealth.swap_pct}
                                settings={sett}
                            />
                        </div>
                    )}

                    {/* KPI cards — 2×2 grid */}
                    <div class="grid grid-cols-2 gap-3">
                        <HeroCard
                            label="Jobs / 24h"
                            value={kpis ? kpis.jobs_24h : '--'}
                            sparkData={buildHealthSparkline(health, 'ram_pct')}
                            sparkColor="var(--accent)"
                            delta={kpis ? buildJobsDelta(kpis, hist) : null}
                        />
                        <HeroCard
                            label="Avg Wait"
                            value={kpis ? formatWaitReadable(kpis.avg_wait_seconds) : '--'}
                            sparkData={buildDurationSparkline(durations)}
                            sparkColor="var(--accent)"
                            delta={kpis ? buildWaitDelta(kpis.avg_wait_seconds) : null}
                        />
                        <HeroCard
                            label="Pause Time"
                            value={kpis ? `${kpis.pause_minutes_24h}` : '--'}
                            unit="min"
                            warning={kpis && kpis.pause_minutes_24h > 30}
                            sparkData={buildHealthSparkline(health, 'ram_pct')}
                            sparkColor="var(--status-warning)"
                            delta={kpis ? buildPauseDelta(kpis.pause_minutes_24h) : null}
                        />
                        <HeroCard
                            label="Success Rate"
                            value={kpis ? `${Math.round(kpis.success_rate_7d * 100)}` : '--'}
                            unit="%"
                            warning={kpis && kpis.success_rate_7d < 0.9}
                            delta={kpis ? buildSuccessRateDelta(kpis, hist) : null}
                        />
                    </div>

                    {/* Proxy mini-stat — shown only when proxy calls exist in history */}
                    {showProxyStat && (
                        <div style={{
                            fontFamily: 'var(--font-mono)',
                            fontSize: 'var(--type-label)',
                            color: 'var(--text-tertiary)',
                            paddingTop: '0.25rem',
                        }}>
                            proxy{' '}
                            {proxyGenerate > 0 && `${proxyGenerate} generate`}
                            {proxyGenerate > 0 && proxyEmbed > 0 && ' · '}
                            {proxyEmbed > 0 && `${proxyEmbed} embed`}
                            {' '}(last 24h)
                        </div>
                    )}
                </div>
            </div>

            {/* Toast notification after job submit */}
            {toast && (
                <div style={{
                    position: 'fixed',
                    bottom: '6rem',
                    right: '4.5rem',
                    background: 'var(--bg-surface-raised)',
                    border: '1px solid var(--status-healthy)',
                    color: 'var(--status-healthy)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 'var(--type-label)',
                    padding: '0.4rem 0.75rem',
                    borderRadius: 'var(--radius)',
                    zIndex: 60,
                }}>
                    ✓ {toast}
                </div>
            )}
            <SubmitJobModal onJobSubmitted={handleJobSubmitted} />
        </div>
    );
}

// ── Data helpers (copied verbatim from Dashboard.jsx) ────────────────────────

function buildDurationSparkline(rows) {
    if (!rows || rows.length < 2) return null;
    const sorted = [...rows].sort((a, b) => a.recorded_at - b.recorded_at).slice(-24);
    return [sorted.map((r) => r.recorded_at), sorted.map((r) => r.duration)];
}

function buildHealthSparkline(rows, field) {
    if (!rows || rows.length < 2) return null;
    const sorted = [...rows].reverse();
    return [sorted.map((r) => r.timestamp), sorted.map((r) => r[field] ?? null)];
}

function formatWaitReadable(seconds) {
    if (seconds === null || seconds <= 0) return '0s';
    const s = Math.round(seconds);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    const hr = Math.floor(m / 60);
    return `${hr}h ${m % 60}m`;
}

function buildJobsDelta(kpis, hist) {
    if (!kpis || kpis.jobs_24h === 0) return 'no jobs in the last 24h';
    const oneDayAgo = Date.now() / 1000 - 86400;
    const todayFailed = (hist || []).filter(
        (j) => (j.status === 'failed' || j.status === 'killed') && (j.completed_at ?? 0) >= oneDayAgo
    ).length;
    if (todayFailed === 0) return 'all completed successfully';
    const s = todayFailed === 1 ? '' : 's';
    return `${todayFailed} job${s} failed today`;
}

function buildWaitDelta(seconds) {
    if (seconds === null || seconds <= 0) return 'no wait data yet';
    if (seconds <= 30) return 'queue flowing smoothly';
    if (seconds <= 120) return 'light wait — normal range';
    if (seconds <= 300) return 'moderate backlog — check queue';
    return 'heavy wait — jobs are stacking up';
}

function buildPauseDelta(minutes) {
    if (!minutes || minutes <= 0) return 'no pauses — running clean';
    if (minutes <= 30) return 'some pauses — health thresholds triggered';
    return 'frequent pauses — lower thresholds in Settings';
}

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

    const timeouts = recentFails.filter((j) => j.outcome_reason && /timeout/i.test(j.outcome_reason));
    const stalls = recentFails.filter((j) => j.stall_detected_at);
    const crashes = recentFails.filter((j) => j.outcome_reason && /exit code [^0]|non.zero|crash|error/i.test(j.outcome_reason));

    const n = bad;
    const s = n === 1 ? '' : 's';

    if (timeouts.length > 0 && timeouts.length >= recentFails.length / 2)
        return `${n} job${s} ran past their time limit — raise Default Timeout in Settings`;
    if (stalls.length > 0 && stalls.length >= recentFails.length / 2)
        return `${n} job${s} appeared stuck and were killed — review Stall Detection in Settings`;
    if (crashes.length > 0 && crashes.length >= recentFails.length / 2)
        return `${n} job${s} crashed with an error — check History for the command output`;
    if (bad === 1) return '1 job failed — tap History to see what went wrong';
    return `${n} jobs failed this week — check History or DLQ for patterns`;
}
