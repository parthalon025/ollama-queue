import { h, Component } from 'preact';
import { useEffect } from 'preact/hooks';

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
    startPolling, stopPolling, stopEvalPoll, status,
} from './store';
import Sidebar from './components/Sidebar.jsx';
import BottomNav from './components/BottomNav.jsx';
import Now from './pages/Now.jsx';
import Plan from './pages/Plan.jsx';
import History from './pages/History.jsx';
import ModelsTab from './pages/ModelsTab.jsx';
import Settings from './pages/Settings.jsx';
import Eval from './pages/Eval.jsx';

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
    useEffect(() => {
        startPolling();
        return () => stopPolling();
    }, []);

    function handleNavigate(viewId) {
        if (viewId !== 'eval') stopEvalPoll();  // stop eval poll when leaving eval tab
        currentTab.value = viewId;
        if (viewId === 'models') fetchModels();
        if (viewId === 'plan') fetchSchedule();
    }

    function renderView() {
        switch (currentTab.value) {
            case 'plan':     return <PlanErrorBoundary><Plan /></PlanErrorBoundary>;
            case 'history':  return <History />;
            case 'models':   return <ModelsTab />;
            case 'settings': return <Settings />;
            case 'eval':     return <Eval />;
            default:         return <Now />;
        }
    }

    const daemonState = status.value?.daemon ?? null;
    const activeEval = status.value?.active_eval ?? null;

    return (
        <div class="layout-root" style="background: var(--bg-base); color: var(--text-primary);">
            <Sidebar
                active={currentTab.value}
                onNavigate={handleNavigate}
                daemonState={daemonState}
                dlqCount={dlqCount.value}
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
            />
        </div>
    );
}
