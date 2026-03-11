import { h, Component } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { signal, useSignal } from '@preact/signals';

// Temporary debug boundary — catches Plan render errors that signals swallows silently
class PlanErrorBoundary extends Component {
    constructor() { super(); this.state = { error: null }; }
    componentDidCatch(err) {
        console.error('[PlanErrorBoundary] caught:', err);
        this.setState({ error: err ? (err.message || String(err)) : 'unknown error' });
    }
    render() {
        if (this.state.error) {
            return h('div', { style: 'color:red;padding:1rem;font-family:monospace;white-space:pre-wrap' },
                'Plan render error:\n' + this.state.error);
        }
        return this.props.children;
    }
}
import {
    currentTab, dlqCount, fetchModels, fetchSchedule,
    startPolling, stopPolling, stopEvalPoll, status, refreshQueue,
} from './stores';
import Sidebar from './components/Sidebar.jsx';
import BottomNav from './components/BottomNav.jsx';
import SubmitJobModal from './components/SubmitJobModal.jsx';
import Now from './pages/Now.jsx';
import Plan from './pages/Plan';
import History from './pages/History.jsx';
import ModelsTab from './pages/ModelsTab.jsx';
import Settings from './pages/Settings.jsx';
import Eval from './pages/Eval.jsx';
import Consumers from './pages/Consumers.jsx';
import Performance from './pages/Performance.jsx';

// What it shows: A thin persistent strip at the top of the content area whenever an eval
//   session is running — shows eval run #, model name, and current phase.
// Decision it drives: User always knows Ollama is busy with eval regardless of which tab
//   they're viewing. Clicking jumps to the Eval tab for live progress details.
function EvalActivityBanner({ activeEval, onNavigate }) {
    return (
        <div
            onClick={() => onNavigate('eval')}
            title="Eval session in progress — click to view"
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.25rem 0.75rem',
                marginBottom: '0.75rem',
                background: 'rgba(74,222,128,0.07)',
                border: '1px solid var(--status-healthy)',
                borderRadius: 'var(--radius)',
                cursor: 'pointer',
                flexWrap: 'wrap',
            }}
        >
            <span class="animate-pulse-amber" style={{
                display: 'inline-block',
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: 'var(--status-healthy)',
                flexShrink: 0,
            }} />
            <span class="data-mono" style="font-size: var(--type-label); color: var(--status-healthy);">
                eval #{activeEval.id}
            </span>
            {activeEval.judge_model && (
                <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
                    {activeEval.judge_model}
                </span>
            )}
            <span class="data-mono" style="font-size: var(--type-label); color: var(--text-tertiary);">
                · {activeEval.status}
            </span>
            <span style="margin-left: auto; font-size: var(--type-micro); color: var(--text-tertiary);">
                view →
            </span>
        </div>
    );
}

export function App() {
    // Component-scoped signal — controls the app-wide SubmitJobModal.
    // Sidebar [+ Submit] and BottomNav FAB both set this to true; the modal resets it on close.
    // Scoped here (not module-level) so it resets cleanly on each component mount, preventing
    // HMR state leaks where the modal stays open after a hot reload.
    const showSubmitModal = useSignal(false);

    // Theme: read from localStorage, default dark. Writes to <html data-theme="...">
    const [theme, setTheme] = useState(() => {
        const saved = localStorage.getItem('queue-theme');
        return (saved === 'light' || saved === 'dark') ? saved : 'dark';
    });

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('queue-theme', theme);
    }, [theme]);

    function handleToggleTheme() {
        setTheme(t => t === 'dark' ? 'light' : 'dark');
    }

    useEffect(() => {
        startPolling();
        return () => stopPolling();
    }, []);

    // Keyboard shortcuts: 1-5 to switch tabs
    useEffect(() => {
        const TABS = ['now', 'plan', 'history', 'models', 'settings'];
        function onKeyDown(e) {
            // Skip if modifier keys held (Ctrl, Alt, Meta)
            if (e.ctrlKey || e.altKey || e.metaKey) return;
            // Skip if focus is inside a text input, textarea, select, or contenteditable
            const tag = (document.activeElement?.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select' ||
                document.activeElement?.isContentEditable) return;
            const idx = parseInt(e.key, 10) - 1;
            if (idx >= 0 && idx < TABS.length) {
                currentTab.value = TABS[idx];
            }
        }
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, []);  // empty deps — handler captures currentTab via signal write, no closure issue

    function handleNavigate(viewId) {
        if (viewId !== 'eval') stopEvalPoll();  // stop eval poll when leaving eval tab
        currentTab.value = viewId;
        if (viewId === 'models') fetchModels();
        if (viewId === 'plan') fetchSchedule();
    }

    function handleSubmitRequest() { showSubmitModal.value = true; }

    function renderView() {
        switch (currentTab.value) {
            case 'plan':     return <PlanErrorBoundary><Plan /></PlanErrorBoundary>;
            case 'history':  return <History />;
            case 'models':   return <ModelsTab />;
            case 'settings': return <Settings />;
            case 'eval':      return <Eval />;
            case 'consumers': return <Consumers />;
            case 'performance': return <Performance />;
            default:          return <Now onSubmitRequest={handleSubmitRequest} />;
        }
    }

    const daemonState = status.value?.daemon ?? null;
    const activeEval = status.value?.active_eval ?? null;

    return (
        <div class="layout-root sh-crt" style="background: var(--bg-base); color: var(--text-primary);">
            <Sidebar
                active={currentTab.value}
                onNavigate={handleNavigate}
                daemonState={daemonState}
                dlqCount={dlqCount.value}
                theme={theme}
                onToggleTheme={handleToggleTheme}
                onSubmitRequest={handleSubmitRequest}
            />
            <main class="layout-main animate-page-enter">
                {/* Banner only on tabs without a dedicated eval panel — Now has CurrentJob, Eval has ActiveRunProgress */}
                {activeEval && currentTab.value !== 'eval' && currentTab.value !== 'now' && <EvalActivityBanner activeEval={activeEval} onNavigate={handleNavigate} />}
                {renderView()}
            </main>
            <BottomNav
                active={currentTab.value}
                onNavigate={handleNavigate}
                dlqCount={dlqCount.value}
                onSubmitRequest={handleSubmitRequest}
            />
            {/* App-level SubmitJobModal — opened by Sidebar button and BottomNav FAB from any tab.
                onJobSubmitted calls refreshQueue so the queue list updates immediately after submit. */}
            <SubmitJobModal
                open={showSubmitModal.value}
                onClose={() => { showSubmitModal.value = false; }}
                onJobSubmitted={() => refreshQueue()}
            />
        </div>
    );
}
