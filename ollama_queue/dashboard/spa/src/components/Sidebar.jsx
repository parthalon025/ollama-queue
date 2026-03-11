import { h } from 'preact';
import { healthData, settings, connectionStatus } from '../stores';
import SystemHealthChip from './SystemHealthChip.jsx';

// NOTE: callback params use descriptive names (item, etc.) — never 'h' (shadows JSX factory)
const NAV_ITEMS = [
    { id: 'now',      icon: '●', label: 'Now',      tooltip: "Live view — what's running right now" },
    { id: 'plan',     icon: '◫', label: 'Schedule', tooltip: 'Recurring jobs and upcoming run times' },
    { id: 'history',  icon: '◷', label: 'History',  tooltip: 'Completed and failed jobs' },
    { id: 'models',   icon: '⊞', label: 'Models',   tooltip: 'Installed AI models and downloads' },
    { id: 'settings', icon: '⚙', label: 'Settings', tooltip: 'Configure queue thresholds and defaults' },
    { id: 'eval',      icon: '⊡', label: 'Eval',      tooltip: 'Test and compare AI model configurations' },
    { id: 'consumers', icon: '⇄', label: 'Consumers', tooltip: 'Detected Ollama consumers and routing' },
    { id: 'performance', icon: '⊘', label: 'Perf', tooltip: 'Model performance stats and system health' },
];

// What it shows: Sidebar navigation + aggregate system health chip at the top.
// Decision it drives: User can navigate between tabs and see at a glance whether the
//   system is healthy, has warnings, or has issues requiring attention.
export default function Sidebar({ active, onNavigate, daemonState, dlqCount, theme, onToggleTheme }) {
    // Read health/settings/connection signals directly — avoids threading more props through App
    const latestHealth = healthData.value?.length > 0 ? healthData.value[0] : null;
    const sett = settings.value;
    const connStatus = connectionStatus.value;

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

            {/* Nav items */}
            <nav style="flex: 1; padding: 0.5rem 0; overflow-y: auto;">
                {NAV_ITEMS.map(item => {
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
                            <span style="font-size: 1rem; flex-shrink: 0;">{item.icon}</span>
                            <span class="sidebar-label">{item.label}</span>
                            {badge && (
                                <span style={{
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
