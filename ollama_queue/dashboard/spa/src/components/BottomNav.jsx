import { h, Fragment } from 'preact';

// NOTE: callback params use descriptive names — never 'h'
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

// What it shows: Mobile bottom tab bar for navigating between views, plus a floating action
//   button (FAB) above the bar when onSubmitRequest is wired in.
// Decision it drives: User can switch tabs and submit a new job with one tap on mobile
//   without needing to know about the desktop Sidebar.
export default function BottomNav({ active, onNavigate, dlqCount, onSubmitRequest }) {
    return (
        <Fragment>
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
            {/* Composite issue count — aggregates actionable signals into a single badge on the Now tab.
                Currently uses DLQ count as the primary signal; extend with stall/error counts as needed. */}
            {(() => {
                const issueCount = dlqCount || 0;
                return NAV_ITEMS.map(item => {
                    const isActive = active === item.id;
                    const showBadge = item.id === 'now' && issueCount > 0;
                    return (
                        <button
                            key={item.id}
                            title={item.tooltip}
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
                            {showBadge && (
                                <span style="position:absolute;top:4px;right:4px;background:var(--status-error);color:#fff;border-radius:50%;width:16px;height:16px;font-size:10px;display:flex;align-items:center;justify-content:center;font-weight:600;">
                                    {issueCount > 9 ? '9+' : issueCount}
                                </span>
                            )}
                        </button>
                    );
                });
            })()}
        </nav>
        {onSubmitRequest && (
            <button
                class="t-btn"
                onClick={onSubmitRequest}
                aria-label="Submit job"
                style="position:fixed;bottom:72px;right:16px;z-index:50;width:48px;height:48px;border-radius:50%;font-size:1.25rem;display:flex;align-items:center;justify-content:center;padding:0;"
            >
                +
            </button>
        )}
        </Fragment>
    );
}
