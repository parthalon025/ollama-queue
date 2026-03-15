import { useRef } from 'preact/hooks';
import { healthData, settings, connectionStatus } from '../stores';
import { scheduledEvalCount } from '../stores/eval.js';
import SystemHealthChip from './SystemHealthChip.jsx';
import SystemSummaryLine from './SystemSummaryLine.jsx';
import EvalWinnerChip from './EvalWinnerChip.jsx';
import { TAB_CONFIG } from '../config/tabs.js';

// NOTE: callback params use descriptive names (item, etc.) — never 'h' (shadows JSX factory)

// What it shows: Sidebar navigation + aggregate system health chip at the top.
// Decision it drives: User can navigate between tabs and see at a glance whether the
//   system is healthy, has warnings, or has issues requiring attention.
// What it shows: Desktop navigation rail with tab buttons, system health chip, and a
//   persistent [+ Submit] button at the bottom. The submit button is hidden when the daemon
//   is in an error state (no point queuing if the system is broken).
// Decision it drives: User can navigate between tabs and submit a new job from any tab without
//   hunting for the action — it's always visible on desktop.
export default function Sidebar({ active, onNavigate, daemonState, dlqCount, theme, onToggleTheme, onSubmitRequest }) {
    // Read health/settings/connection signals directly — avoids threading more props through App
    const latestHealth = healthData.value?.length > 0 ? healthData.value[0] : null;
    const sett = settings.value;
    const connStatus = connectionStatus.value;

    // DLQ badge animation tracking.
    // What it does: fires t3-badge-appear when the DLQ badge first appears (count was 0),
    //   and t3-counter-bump when the count increases. The key increments each time to force
    //   Preact to remount the badge span, which re-triggers the CSS animation.
    const prevDlqRef = useRef(dlqCount);
    const badgeAnimKey = useRef(0);
    const badgeAnimClass = useRef('');

    if (dlqCount !== prevDlqRef.current) {
        if (prevDlqRef.current === 0 && dlqCount > 0) {
            badgeAnimClass.current = 't3-badge-appear';
        } else if (dlqCount > prevDlqRef.current) {
            badgeAnimClass.current = 't3-counter-bump';
        } else {
            badgeAnimClass.current = '';
        }
        badgeAnimKey.current += 1;
        prevDlqRef.current = dlqCount;
    }

    return (
        <aside class="layout-sidebar">
            {/* Aggregate health chip — replaces old daemon-only status display */}
            <div style={{ borderBottom: '1px solid var(--border-subtle)', flexShrink: 0 }}>
                <SystemHealthChip
                    daemonState={daemonState?.state || 'idle'}
                    dlqCount={dlqCount}
                    ram={latestHealth?.ram_pct}
                    vram={latestHealth?.vram_pct}
                    load={latestHealth?.load_avg}
                    swap={latestHealth?.swap_pct}
                    settings={sett}
                    connectionStatus={connStatus}
                />
            </div>

            <div class="sidebar-summary">
                <SystemSummaryLine />
                <EvalWinnerChip />
            </div>

            {/* Nav items */}
            <nav style="flex: 1; padding: 0.5rem 0; overflow-y: auto;">
                {TAB_CONFIG.map(item => {
                    const isActive = active === item.id;
                    const badge = item.id === 'history' && dlqCount > 0 ? dlqCount : null;
                    return (
                        <button
                            key={item.id}
                            title={item.tooltip}
                            onClick={() => onNavigate(item.id)}
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '0.75rem',
                                width: '100%',
                                padding: '0.625rem 0.75rem',
                                textAlign: 'left',
                                background: isActive ? 'var(--accent-glow)' : 'transparent',
                                color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                                fontSize: 'var(--type-body)',
                                fontWeight: isActive ? 600 : 400,
                                cursor: 'pointer',
                                border: 'none',
                                borderLeft: isActive ? '3px solid var(--accent)' : '3px solid transparent',
                                transition: 'background 0.15s ease, color 0.15s ease',
                                position: 'relative',
                                whiteSpace: 'nowrap',
                            }}
                        >
                            <img
                                src={item.icon}
                                width={18}
                                height={18}
                                alt=""
                                aria-hidden="true"
                                style={{ imageRendering: 'pixelated', opacity: isActive ? 1.0 : 0.55, flexShrink: 0 }}
                            />
                            <span class="sidebar-label">{item.label}</span>
                            {item.id === 'plan' && scheduledEvalCount.value > 0 && (
                                <span class="nav-badge nav-badge--eval" title={`${scheduledEvalCount.value} eval run(s) in next 4h`}>EVAL</span>
                            )}
                            {badge && (
                                <span
                                    key={badgeAnimKey.current}
                                    class={badgeAnimClass.current}
                                    style={{
                                        marginLeft: 'auto',
                                        background: 'var(--status-error)',
                                        color: '#fff',
                                        fontSize: 'var(--type-micro)',
                                        fontFamily: 'var(--font-mono)',
                                        padding: '1px 5px',
                                        borderRadius: 10,
                                        fontWeight: 700,
                                        flexShrink: 0,
                                    }}>
                                    {badge}
                                </span>
                            )}
                        </button>
                    );
                })}
            </nav>
            {/* [+ Submit] button — permanently visible so users can queue a job from any tab */}
            {onSubmitRequest && daemonState?.state !== 'error' && (
                <button
                    class="t-btn"
                    onClick={onSubmitRequest}
                    title="Submit a new job to the queue"
                    style="width:100%;margin-top:auto;font-size:var(--type-label);padding:8px;"
                >
                    <span class="sidebar-label">+ Submit</span>
                </button>
            )}
            {/* Theme toggle — dark/light mode switcher */}
            <button
                class="theme-toggle"
                onClick={onToggleTheme}
                title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
                <span style="font-size: 1rem; flex-shrink: 0;">{theme === 'dark' ? '☀' : '◗'}</span>
                <span class="sidebar-label">{theme === 'dark' ? 'Light' : 'Dark'}</span>
            </button>
        </aside>
    );
}
