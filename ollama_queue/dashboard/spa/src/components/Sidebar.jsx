import { h } from 'preact';

// NOTE: callback params use descriptive names (item, etc.) — never 'h' (shadows JSX factory)
const NAV_ITEMS = [
    { id: 'now',      icon: '●', label: 'Now' },
    { id: 'plan',     icon: '◫', label: 'Plan' },
    { id: 'history',  icon: '◷', label: 'History' },
    { id: 'models',   icon: '⊞', label: 'Models' },
    { id: 'settings', icon: '⚙', label: 'Settings' },
];

export default function Sidebar({ active, onNavigate, daemonState, dlqCount }) {
    const state = daemonState?.state || 'idle';
    const isRunning = state === 'running';
    const isPaused  = state.startsWith('paused');

    const chipColor = isRunning ? 'var(--status-healthy)'
        : isPaused ? 'var(--status-warning)'
        : 'var(--text-tertiary)';

    const chipDot  = isRunning ? '▶' : isPaused ? '⏸' : '○';
    const chipText = isRunning
        ? (daemonState?.current_job_source || 'running')
        : isPaused ? 'paused' : 'idle';

    return (
        <aside class="layout-sidebar">
            {/* Daemon status chip */}
            <div style={{
                padding: '1rem 0.75rem 0.75rem',
                borderBottom: '1px solid var(--border-subtle)',
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                color: chipColor,
                fontFamily: 'var(--font-mono)',
                fontSize: 'var(--type-label)',
                fontWeight: 600,
                overflow: 'hidden',
                flexShrink: 0,
            }}>
                <span style="flex-shrink: 0;">{chipDot}</span>
                <span class="sidebar-label" style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    {chipText}
                </span>
            </div>

            {/* Nav items */}
            <nav style="flex: 1; padding: 0.5rem 0;">
                {NAV_ITEMS.map(item => {
                    const isActive = active === item.id;
                    const badge = item.id === 'history' && dlqCount > 0 ? dlqCount : null;
                    return (
                        <button
                            key={item.id}
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
        </aside>
    );
}
