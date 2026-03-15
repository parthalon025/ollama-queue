import { Fragment } from 'preact';
import { useRef } from 'preact/hooks';
import { scheduledEvalCount } from '../stores/eval.js';
import { TAB_CONFIG } from '../config/tabs.js';

// NOTE: callback params use descriptive names — never 'h'

// What it shows: Mobile bottom tab bar for navigating between views, plus a floating action
//   button (FAB) above the bar when onSubmitRequest is wired in.
// Decision it drives: User can switch tabs and submit a new job with one tap on mobile
//   without needing to know about the desktop Sidebar.
export default function BottomNav({ active, onNavigate, dlqCount, onSubmitRequest }) {
    // Composite issue count — aggregates actionable signals into a single badge on the Now tab.
    // Currently uses DLQ count as the primary signal; extend with stall/error counts as needed.
    const issueCount = dlqCount || 0;

    // DLQ badge animation tracking.
    // What it does: fires t3-badge-appear when the badge first appears (count was 0),
    //   and t3-counter-bump when the count increases. The key increments each time to force
    //   Preact to remount the badge span, which re-triggers the CSS animation.
    const prevDlqRef = useRef(issueCount);
    const badgeAnimKey = useRef(0);
    const badgeAnimClass = useRef('');

    if (issueCount !== prevDlqRef.current) {
        if (prevDlqRef.current === 0 && issueCount > 0) {
            badgeAnimClass.current = 't3-badge-appear';
        } else if (issueCount > prevDlqRef.current) {
            badgeAnimClass.current = 't3-counter-bump';
        } else {
            badgeAnimClass.current = '';
        }
        badgeAnimKey.current += 1;
        prevDlqRef.current = issueCount;
    }

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
            {TAB_CONFIG.map(item => {
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
                        <img
                            src={item.icon}
                            width={18}
                            height={18}
                            alt=""
                            aria-hidden="true"
                            style={{ imageRendering: 'pixelated', opacity: isActive ? 1.0 : 0.55 }}
                        />
                        <span>{item.label}</span>
                        {item.id === 'plan' && scheduledEvalCount.value > 0 && (
                            <span class="nav-badge nav-badge--eval" title={`${scheduledEvalCount.value} eval run(s) in next 4h`}>EVAL</span>
                        )}
                        {showBadge && (
                            <span
                                key={badgeAnimKey.current}
                                class={badgeAnimClass.current}
                                style="position:absolute;top:4px;right:4px;background:var(--status-error);color:#fff;border-radius:50%;width:16px;height:16px;font-size:10px;display:flex;align-items:center;justify-content:center;font-weight:600;">
                                {issueCount > 9 ? '9+' : issueCount}
                            </span>
                        )}
                    </button>
                );
            })}
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
