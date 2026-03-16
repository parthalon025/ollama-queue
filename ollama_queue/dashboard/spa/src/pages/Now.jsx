import { useEffect, useRef } from 'preact/hooks';
import { applyMantra, removeMantra } from 'superhot-ui';
import {
    status, queue, history, healthData, cpuCount, durationData, settings,
    dlqCount, connectionStatus, currentTab, clearDLQ,
    scheduleJobs, fetchSchedule,
} from '../stores';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import { useShatter } from '../hooks/useShatter.js';
import CurrentJob from '../components/CurrentJob.jsx';
import QueueList from '../components/QueueList.jsx';
import HeroCard from '../components/HeroCard.jsx';
import { ShPageBanner, ShStatCard, ShStatsGrid, ShFrozen, ShThreatPulse } from 'superhot-ui/preact';
import { TAB_CONFIG } from '../config/tabs.js';
import InfrastructurePanel from '../components/InfrastructurePanel.jsx';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

// What it shows: The live command center — what's running right now, what's waiting in the
//   queue, how healthy the system is, and whether anything needs attention (DLQ entries,
//   recent failures). KPI cards summarize the last 24h/7d at a glance.
// Decision it drives: Is the queue healthy and progressing? Should I submit more work, cancel
//   something, or go investigate a problem in History? The alert strip makes issues impossible
//   to miss. The + FAB opens SubmitJobModal to queue a one-off job immediately.
export default function Now({ onSubmitRequest }) {
    const _tab = TAB_CONFIG.find(t => t.id === 'now');
    const st = status.value;
    const q = queue.value;
    const hist = history.value;
    const health = healthData.value;
    const durations = durationData.value;
    const sett = settings.value;
    const dlqCnt = dlqCount.value;

    // Action feedback hook for "Dismiss all" — must precede any conditional return
    const [dismissFb, dismissAct] = useActionFeedback();
    const [dismissRef, dismissShatter] = useShatter('earned');

    // Ref for the page container — target for the OFFLINE mantra overlay.
    const pageRef = useRef(null);

    // Fetch schedule once on mount so disabled recurring job count is available
    // even if the Plan tab hasn't been visited yet.
    useEffect(() => { fetchSchedule(); }, []);

    // OFFLINE mantra: stamp "OFFLINE" watermark on the page when the WebSocket
    // connection to the server is lost. Removes itself when reconnected.
    const isOffline = connectionStatus.value === 'disconnected';
    useEffect(() => {
        if (!pageRef.current) return;
        if (isOffline) {
            applyMantra(pageRef.current, 'OFFLINE');
        } else {
            removeMantra(pageRef.current);
        }
        return () => { if (pageRef.current) removeMantra(pageRef.current); };
    }, [isOffline]);

    const daemon = st?.daemon ?? null;
    // Circuit breaker open: daemon is in error state or has explicitly opened the circuit breaker.
    // When open, no new jobs can start — ThreatPulse signals persistent danger on the job card.
    const isCircuitOpen = daemon?.state === 'error' || daemon?.circuit_breaker_open;
    const kpis = st?.kpis ?? null;
    const currentJob = st?.current_job ?? null;
    const activeEval = st?.active_eval ?? null;
    const latestHealth = health?.length > 0 ? health[0] : null;

    // Count failures in last 24h for alert strip
    const oneDayAgo = Date.now() / 1000 - 86400;
    const recentFailures = (hist || []).filter(
        job => (job.status === 'failed' || job.status === 'killed') && (job.completed_at ?? 0) >= oneDayAgo
    ).length;
    // Count recurring jobs that were auto-disabled (have outcome_reason set) — signals a systemic issue
    const disabledRecurring = (scheduleJobs.value || []).filter(rj => !rj.enabled && rj.outcome_reason).length;
    const showAlerts = dlqCnt > 0 || recentFailures > 0 || disabledRecurring > 0;

    // Proxy mini-stat: count proxy calls in last 24h from history signal
    // (reuse the oneDayAgo already computed above for the alert strip)
    const proxyGenerate = (hist || []).filter(
        job => job.command === 'proxy:/api/generate' && (job.completed_at ?? 0) >= oneDayAgo
    ).length;
    const proxyEmbed = (hist || []).filter(
        job => job.command === 'proxy:/api/embed' && (job.completed_at ?? 0) >= oneDayAgo
    ).length;
    const showProxyStat = proxyGenerate > 0 || proxyEmbed > 0;

    // What it shows: KPI summary cards — daemon state, queue depth, 24h job count,
    //   RAM/VRAM utilization. Derived from live signals, not hardcoded.
    // Decision it drives: Is the daemon healthy? Is RAM under pressure? How busy was
    //   the queue today?
    // "warm" = daemon idle but a model is still loaded in VRAM — GPU is occupied even
    //   though no job is running. Distinguishes truly-free GPU from loaded-but-waiting.
    const rawDaemonState = st?.daemon?.state ?? null;
    const warmModel = rawDaemonState === 'idle' ? (latestHealth?.ollama_model ?? null) : null;
    const daemonDisplayValue = warmModel ? 'warm' : (rawDaemonState ?? '—');
    const daemonStatStatus =
        !st ? 'waiting' :
        rawDaemonState === 'running' ? 'active' :
        (rawDaemonState || '').startsWith('paused') ? 'warning' :
        rawDaemonState === 'offline' ? 'error' : 'ok';

    const kpiStats = [
        {
            label: 'Daemon',
            value: daemonDisplayValue,
            status: daemonStatStatus,
            detail: warmModel ? warmModel.split(':')[0] : undefined,
        },
        {
            label: 'Queue Depth',
            value: q?.length ?? 0,
            status: (q?.length ?? 0) > 0 ? 'warning' : 'ok',
            detail: sett?.concurrency ? `max ${sett.concurrency}` : undefined,
        },
        {
            label: 'Jobs (24h)',
            value: kpis?.jobs_24h ?? '—',
            status: 'ok',
            detail: kpis?.success_rate_24h != null ? `${Math.round(kpis.success_rate_24h * 100)}% success` : undefined,
        },
        {
            label: 'RAM',
            value: latestHealth?.ram_pct != null ? `${Math.round(latestHealth.ram_pct)}%` : '—',
            status: (latestHealth?.ram_pct || 0) > 85 ? 'error' : (latestHealth?.ram_pct || 0) > 70 ? 'warning' : 'ok',
        },
    ];
    if (latestHealth?.vram_pct != null) {
        kpiStats.push({
            label: 'VRAM',
            value: `${Math.round(latestHealth.vram_pct)}%`,
            status: latestHealth.vram_pct > 85 ? 'error' : latestHealth.vram_pct > 70 ? 'warning' : 'ok',
        });
    }

    return (
        <div ref={pageRef} class="flex flex-col gap-4 sh-stagger-children animate-page-enter"
             data-mood={showAlerts ? 'dread' : 'dawn'}>
            <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />
            {/* KPI stat cards — live queue health at a glance */}
            {/* ShFrozen: dims the stats block when daemon data goes stale (>30s cooling, >2m frozen, >5m stale) */}
            <ShFrozen timestamp={st?.daemon?.timestamp ? st.daemon.timestamp * 1000 : null}
                      thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
                <ShStatsGrid stats={kpiStats} />
            </ShFrozen>
            {/* Disconnected banner */}
            {connectionStatus.value === 'disconnected' && (
                <div style={{
                    background: 'var(--bg-surface)', color: 'var(--status-warning)',
                    padding: '0.5rem 1rem', borderRadius: 4,
                    border: '1px solid var(--status-warning-subtle)',
                }}>
                    SIGNAL LOST — RECONNECTING
                </div>
            )}

            {/* 2-column layout: left = operations, right = health + KPIs */}
            <div class="now-grid sh-delay-100">

                {/* LEFT: running job + queue */}
                <div class="flex flex-col gap-4">
                    {/* CurrentJob renders its own t-frame — no wrapper needed */}
                    <ShThreatPulse active={isCircuitOpen} persistent>
                        <CurrentJob
                            daemon={daemon}
                            currentJob={currentJob}
                            latestHealth={latestHealth}
                            settings={sett}
                            activeEval={activeEval}
                            onSubmitRequest={onSubmitRequest}
                        />
                    </ShThreatPulse>
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
                                ATTENTION REQUIRED
                            </span>
                            {dlqCnt > 0 && (
                                // What it shows: DLQ count + two quick-action buttons to navigate
                                //   to History or bulk-clear the DLQ without leaving the Now tab.
                                // Decision it drives: User can dismiss noise or jump to detail
                                //   without hunting through tabs.
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                    <span
                                        title="Dead-letter queue — jobs that ran out of retries"
                                        style={{
                                            fontSize: 'var(--type-label)',
                                            color: 'var(--status-error)',
                                            fontFamily: 'var(--font-mono)',
                                        }}
                                    >
                                        {dlqCnt} FAILED — REVIEW REQUIRED
                                    </span>
                                    <button
                                        class="t-btn"
                                        style={{ fontSize: 'var(--type-micro)', padding: '2px 8px' }}
                                        onClick={() => { currentTab.value = 'history'; }}
                                    >VIEW</button>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                        <button
                                            ref={dismissRef}
                                            class="t-btn"
                                            style={{ fontSize: 'var(--type-micro)', padding: '2px 8px', color: 'var(--text-tertiary)' }}
                                            disabled={dismissFb.phase === 'loading'}
                                            onClick={() => { dismissShatter(); dismissAct('CLEARING', clearDLQ, () => 'CLEARED'); }}
                                        >DISMISS ALL</button>
                                        {dismissFb.msg && <span class={`action-fb action-fb--${dismissFb.phase}`}>{dismissFb.msg}</span>}
                                    </div>
                                </div>
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
                                    {recentFailures} FAILURES (24H)
                                </button>
                            )}
                            {disabledRecurring > 0 && (
                                <button
                                    onClick={() => { currentTab.value = 'plan'; }}
                                    title="Recurring jobs that were auto-disabled — click to view in Schedule tab"
                                    style={{
                                        fontSize: 'var(--type-label)',
                                        color: 'var(--status-warning)',
                                        background: 'transparent',
                                        border: 'none',
                                        cursor: 'pointer',
                                        textDecoration: 'underline',
                                        fontFamily: 'var(--font-mono)',
                                        padding: 0,
                                    }}
                                >
                                    {disabledRecurring} JOBS AUTO-DISABLED
                                </button>
                            )}
                        </div>
                    )}

                    {/* Infrastructure panel — host scheduler metrics + per-backend GPU rows */}
                    <InfrastructurePanel
                        latestHealth={latestHealth}
                        settings={sett}
                        cpuCount={cpuCount.value}
                    />

                    {/* KPI cards — 2×2 grid */}
                    {/* ShFrozen: each card dims when health data goes stale — health polls every 5s so thresholds are tight */}
                    <div class="grid grid-cols-2 gap-3 sh-delay-200">
                        <ShFrozen timestamp={latestHealth?.timestamp ? latestHealth.timestamp * 1000 : null}
                                  thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
                            <HeroCard
                                label="Jobs Completed Today"
                                value={kpis ? kpis.jobs_24h : '--'}
                                sparkData={buildHealthSparkline(health, 'ram_pct')}
                                sparkColor="var(--accent)"
                                delta={kpis ? buildJobsDelta(kpis, hist) : null}
                                tooltip="Total jobs completed in the last 24 hours. Rising = queue is healthy. Falling = daemon may be stalled."
                                chroma="lune"
                            />
                        </ShFrozen>
                        <ShFrozen timestamp={latestHealth?.timestamp ? latestHealth.timestamp * 1000 : null}
                                  thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
                            <HeroCard
                                label="Average Wait Before Starting"
                                value={kpis ? formatWaitReadable(kpis.avg_wait_seconds) : '--'}
                                sparkData={buildDurationSparkline(durations)}
                                sparkColor="var(--accent)"
                                delta={kpis ? buildWaitDelta(kpis.avg_wait_seconds) : null}
                                tooltip="Average time a job spends in queue before the daemon starts it. Spikes mean the daemon was busy or paused."
                                chroma="lune"
                            />
                        </ShFrozen>
                        <ShFrozen timestamp={latestHealth?.timestamp ? latestHealth.timestamp * 1000 : null}
                                  thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
                            <HeroCard
                                label="Auto-Paused Time Today"
                                value={kpis ? `${kpis.pause_minutes_24h}` : '--'}
                                unit="min"
                                warning={kpis && kpis.pause_minutes_24h > 30}
                                sparkData={buildPauseSparkline(health)}
                                sparkColor="var(--status-warning)"
                                delta={kpis ? buildPauseDelta(kpis.pause_minutes_24h) : null}
                                tooltip="Total minutes the daemon spent paused in the last 24 hours. High values mean frequent health-triggered pauses."
                                chroma="sciel"
                            />
                        </ShFrozen>
                        <ShFrozen timestamp={latestHealth?.timestamp ? latestHealth.timestamp * 1000 : null}
                                  thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
                            <HeroCard
                                label="7-Day Success Rate"
                                value={kpis ? `${Math.round(kpis.success_rate_7d * 100)}` : '--'}
                                unit="%"
                                warning={kpis && kpis.success_rate_7d < 0.9}
                                sparkData={buildSuccessRateSparkline(durations)}
                                sparkColor="var(--accent)"
                                delta={kpis ? buildSuccessRateDelta(kpis, hist) : null}
                                tooltip="Percentage of completed jobs that succeeded. Below 90% warrants investigation in History."
                                chroma="gustave"
                            />
                        </ShFrozen>
                    </div>

                    {/* Proxy mini-stat — shown only when proxy calls exist in history */}
                    {showProxyStat && (
                        <div
                            title="Requests routed through the Ollama proxy endpoint"
                            style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: 'var(--type-label)',
                                color: 'var(--text-tertiary)',
                                paddingTop: '0.25rem',
                            }}
                        >
                            PROXY{' '}
                            {proxyGenerate > 0 && `${proxyGenerate} GENERATE`}
                            {proxyGenerate > 0 && proxyEmbed > 0 && ' · '}
                            {proxyEmbed > 0 && `${proxyEmbed} EMBED`}
                            {' '}(24H)
                        </div>
                    )}
                </div>
            </div>

        </div>
    );
}

// ── Data helpers (copied verbatim from Dashboard.jsx) ────────────────────────

function buildDurationSparkline(rows) {
    if (!rows || rows.length < 2) return null;
    const sorted = [...rows].sort((a, b) => a.recorded_at - b.recorded_at).slice(-24);
    return [sorted.map((r) => r.recorded_at), sorted.map((r) => r.duration)];
}

// Pause state sparkline — 1.0 when daemon was paused, 0.0 otherwise, over last 24 health rows.
// Uses health_log.daemon_state field which records the daemon state at each poll interval.
// Drives: lets the user see visually whether pauses cluster in a burst or are spread out.
function buildPauseSparkline(rows) {
    if (!rows || rows.length < 2) return null;
    const sorted = [...rows].reverse();
    return [
        sorted.map((r) => r.timestamp),
        sorted.map((r) => (r.daemon_state === 'paused' ? 1 : 0)),
    ];
}

// Success rate sparkline — rolling success fraction over last 24 completed duration_history rows.
// Uses exit_code === 0 as the success signal (same convention used by the daemon).
// Drives: shows whether success rate is trending up or down recently, beyond the 7-day aggregate.
function buildSuccessRateSparkline(rows) {
    if (!rows || rows.length < 2) return null;
    const sorted = [...rows].sort((a, b) => a.recorded_at - b.recorded_at).slice(-24);
    // Compute a rolling 5-job window so the sparkline shows trend shape, not just 0/1 noise.
    const values = sorted.map((row, idx, arr) => {
        const window = arr.slice(Math.max(0, idx - 4), idx + 1);
        const successes = window.filter((r) => r.exit_code === 0).length;
        return successes / window.length;
    });
    return [sorted.map((r) => r.recorded_at), values];
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
    if (!kpis || kpis.jobs_24h === 0) return 'NO JOBS (24H)';
    const oneDayAgo = Date.now() / 1000 - 86400;
    const todayFailed = (hist || []).filter(
        (j) => (j.status === 'failed' || j.status === 'killed') && (j.completed_at ?? 0) >= oneDayAgo
    ).length;
    if (todayFailed === 0) return 'ALL SUCCEEDED';
    return `${todayFailed} FAILED TODAY`;
}

function buildWaitDelta(seconds) {
    if (seconds === null || seconds <= 0) return 'NO WAIT DATA';
    if (seconds <= 30) return 'QUEUE FLOWING';
    if (seconds <= 120) return 'LIGHT WAIT';
    if (seconds <= 300) return 'BACKLOG — CHECK QUEUE';
    return 'HEAVY WAIT — JOBS STACKING';
}

function buildPauseDelta(minutes) {
    if (!minutes || minutes <= 0) return 'NO PAUSES';
    if (minutes <= 30) return 'PAUSES — HEALTH THRESHOLDS HIT';
    return 'FREQUENT PAUSES — LOWER THRESHOLDS';
}

function buildSuccessRateDelta(kpis, hist) {
    const ok = kpis.jobs_7d_ok ?? 0;
    const bad = kpis.jobs_7d_bad ?? 0;
    const total = ok + bad;
    if (total === 0) return 'NO JOBS (7D)';
    if (bad === 0) return 'ALL CLEAN';

    const sevenDaysAgo = Date.now() / 1000 - 7 * 86400;
    const recentFails = (hist || []).filter(
        (j) => (j.status === 'failed' || j.status === 'killed') && j.completed_at >= sevenDaysAgo
    );

    const timeouts = recentFails.filter((j) => j.outcome_reason && /timeout/i.test(j.outcome_reason));
    const stalls = recentFails.filter((j) => j.stall_detected_at);
    const crashes = recentFails.filter((j) => j.outcome_reason && /exit code [^0]|non.zero|crash|error/i.test(j.outcome_reason));

    const n = bad;

    if (timeouts.length > 0 && timeouts.length >= recentFails.length / 2)
        return `${n} TIMED OUT — RAISE DEFAULT TIMEOUT`;
    if (stalls.length > 0 && stalls.length >= recentFails.length / 2)
        return `${n} STALLED — REVIEW STALL DETECTION`;
    if (crashes.length > 0 && crashes.length >= recentFails.length / 2)
        return `${n} CRASHED — CHECK HISTORY`;
    if (bad === 1) return '1 FAILED — CHECK HISTORY';
    return `${n} FAILED THIS WEEK — CHECK DLQ`;
}
