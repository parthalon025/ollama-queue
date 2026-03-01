import { h } from 'preact';

// NOTE: callback params use descriptive names — never 'h'
const NAV_ITEMS = [
    { id: 'now',      icon: '●', label: 'Now' },
    { id: 'plan',     icon: '◫', label: 'Plan' },
    { id: 'history',  icon: '◷', label: 'History' },
    { id: 'models',   icon: '⊞', label: 'Models' },
    { id: 'settings', icon: '⚙', label: 'Settings' },
];

export default function BottomNav({ active, onNavigate, dlqCount }) {
    return (
        <nav
            class="mobile-bottom-nav"
            style={{
                display: 'none', /* shown via CSS on mobile */
                position: 'fixed',
                bottom: 0, left: 0, right: 0,
                background: 'var(--bg-surface)',
                borderTop: '1px solid var(--border-subtle)',
                zIndex: 50,
            }}
        >
            {NAV_ITEMS.map(item => {
                const isActive = active === item.id;
                const badge = item.id === 'history' && dlqCount > 0 ? dlqCount : null;
                return (
                    <button
                        key={item.id}
                        onClick={() => onNavigate(item.id)}
                        style={{
                            flex: 1,
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            gap: '2px',
                            padding: '0.5rem 0.25rem',
                            color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                            fontSize: 'var(--type-micro)',
                            cursor: 'pointer',
                            background: 'transparent',
                            border: 'none',
                            position: 'relative',
                        }}
                    >
                        <span style="font-size: 1.1rem;">{item.icon}</span>
                        <span>{item.label}</span>
                        {badge && (
                            <span style={{
                                position: 'absolute',
                                top: 4, right: '18%',
                                background: 'var(--status-error)',
                                color: '#fff',
                                fontSize: '0.5rem',
                                padding: '1px 3px',
                                borderRadius: 8,
                                fontFamily: 'var(--font-mono)',
                                fontWeight: 700,
                            }}>
                                {badge}
                            </span>
                        )}
                    </button>
                );
            })}
        </nav>
    );
}
