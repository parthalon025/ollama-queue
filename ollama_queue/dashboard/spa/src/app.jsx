import { Component } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { signal, useSignal } from '@preact/signals';
import { ShMantra } from 'superhot-ui/preact';

// Temporary debug boundary — catches Plan render errors that signals swallows silently
class PlanErrorBoundary extends Component {
    constructor() { super(); this.state = { error: null }; }
    componentDidCatch(err) {
        console.error('[PlanErrorBoundary] caught:', err);
        this.setState({ error: err ? (err.message || String(err)) : 'unknown error' });
    }
    render() {
        if (this.state.error) {
            return (
                <div style="color:red;padding:1rem;font-family:monospace;white-space:pre-wrap">
                    Plan render error:{'\n'}{this.state.error}
                </div>
            );
        }
        return this.props.children;
    }
}
import {
    currentTab, dlqCount, fetchModels, fetchSchedule,
    startPolling, stopPolling, stopEvalPoll, status, refreshQueue,
} from './stores';
import Sidebar from './components/Sidebar.jsx';
import CohesionHeader from './components/CohesionHeader.jsx';
import ActiveJobStrip from './components/ActiveJobStrip.jsx';
import ActiveEvalStrip from './components/ActiveEvalStrip.jsx';
import BottomNav from './components/BottomNav.jsx';
import SubmitJobModal from './components/SubmitJobModal.jsx';
import OnboardingOverlay from './components/OnboardingOverlay.jsx';
import ShToastContainer from './components/ShToastContainer.jsx';
import { ShCommandPalette } from 'superhot-ui/preact';
import { TAB_CONFIG } from './config/tabs.js';
import Now from './pages/Now.jsx';
import Plan from './pages/Plan';
import History from './pages/History.jsx';
import ModelsTab from './pages/ModelsTab.jsx';
import Settings from './pages/Settings.jsx';
import Eval from './pages/Eval.jsx';
import Consumers from './pages/Consumers.jsx';
import Performance from './pages/Performance.jsx';
import BackendsTab from './pages/BackendsTab.jsx';

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

// Tab list — tabs must be named with descriptive keys (not 'h', never 'tab' alone)
const ALL_TABS = TAB_CONFIG.map(t => t.id);

export function App() {
    // Component-scoped signal — controls the app-wide SubmitJobModal.
    const showSubmitModal = useSignal(false);

    // Cmd+K command palette
    const paletteOpen = useSignal(false);

    // Theme: read from localStorage, default dark.
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

    // What it does: Flashes the main content area when connection recovers after an outage.
    // Design system §7.2 stage 4: "t2-tick-flash on data containers" on recovery.
    // Only fires on disconnected → ok transitions (not on initial load).
    useEffect(() => {
        let rafId = null;
        let timerId = null;
        function onRestored() {
            const main = document.querySelector('.layout-main');
            if (!main) return;
            // Remove + re-add class on next frame to re-trigger CSS animation
            main.classList.remove('t2-tick-flash');
            rafId = requestAnimationFrame(() => {
                main.classList.add('t2-tick-flash');
                rafId = null;
            });
            // Clean up after animation (0.4s animation + rAF lead-in = 500ms total)
            timerId = setTimeout(() => {
                main.classList.remove('t2-tick-flash');
                timerId = null;
            }, 500);
        }
        window.addEventListener('queue:connection-restored', onRestored);
        return () => {
            window.removeEventListener('queue:connection-restored', onRestored);
            if (rafId !== null) cancelAnimationFrame(rafId);
            if (timerId !== null) clearTimeout(timerId);
        };
    }, []);

    // Keyboard shortcuts: 1-8 switch tabs; Cmd/Ctrl+K opens palette
    useEffect(() => {
        function onKeyDown(e) {
            // Cmd+K / Ctrl+K — command palette
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                paletteOpen.value = !paletteOpen.value;
                return;
            }
            // Skip if modifier keys held (Ctrl, Alt, Meta) for non-palette shortcuts
            if (e.ctrlKey || e.altKey || e.metaKey) return;
            // Skip if focus is inside a text input, textarea, select, or contenteditable
            const tag = (document.activeElement?.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select' ||
                document.activeElement?.isContentEditable) return;
            const idx = parseInt(e.key, 10) - 1;
            if (idx >= 0 && idx < ALL_TABS.length) {
                currentTab.value = ALL_TABS[idx];
            }
        }
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, []);

    function handleNavigate(viewId) {
        if (viewId !== 'eval') stopEvalPoll();
        currentTab.value = viewId;
        if (viewId === 'models') fetchModels();
        if (viewId === 'plan') fetchSchedule();
    }

    function handleSubmitRequest() { showSubmitModal.value = true; }

    function renderView() {
        switch (currentTab.value) {
            case 'plan':        return <PlanErrorBoundary><Plan /></PlanErrorBoundary>;
            case 'history':     return <History />;
            case 'models':      return <ModelsTab />;
            case 'settings':    return <Settings />;
            case 'eval':        return <Eval />;
            case 'consumers':   return <Consumers />;
            case 'performance': return <Performance />;
            case 'backends':    return <BackendsTab />;
            default:            return <Now onSubmitRequest={handleSubmitRequest} />;
        }
    }

    const daemonState = status.value?.daemon ?? null;
    const activeEval = status.value?.active_eval ?? null;
    const isDaemonPaused = (daemonState?.state || '').startsWith('paused');

    // Build command palette items from current signals
    // DS ShCommandPalette requires an 'id' field per item for ARIA/key.
    const paletteItems = [
        { id: 'action-submit', icon: '●', label: 'Submit job', group: 'Actions', action: handleSubmitRequest },
        { id: 'action-eval', icon: '⊡', label: 'Trigger eval run', group: 'Actions', action: () => handleNavigate('eval') },
        ...TAB_CONFIG.map((tab, i) => ({
            id: `nav-${tab.id}`,
            icon: tab.icon,
            label: `Go to ${tab.label}`,
            group: 'Navigate',
            shortcut: `${i + 1}`,
            action: () => handleNavigate(tab.id),
        })),
    ];

    return (
        <div class="layout-root sh-crt" style="background: var(--bg-base); color: var(--text-primary);">
            {/* App-level SYSTEM PAUSED mantra — stamps watermark when daemon is paused */}
            <ShMantra text="SYSTEM PAUSED" active={isDaemonPaused} />
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
                {/* Banner only on tabs without a dedicated eval panel */}
                {activeEval && currentTab.value !== 'eval' && currentTab.value !== 'now' && <EvalActivityBanner activeEval={activeEval} onNavigate={handleNavigate} />}
                <CohesionHeader />
                {currentTab.value !== 'now' && <ActiveJobStrip />}
                {currentTab.value !== 'eval' && <ActiveEvalStrip />}
                <div key={currentTab.value} class="tab-content tab-enter" style="flex:1;overflow-y:auto;">
                    {renderView()}
                </div>
            </main>
            <BottomNav
                active={currentTab.value}
                onNavigate={handleNavigate}
                dlqCount={dlqCount.value}
                onSubmitRequest={handleSubmitRequest}
            />
            {/* App-level SubmitJobModal */}
            <SubmitJobModal
                open={showSubmitModal.value}
                onClose={() => { showSubmitModal.value = false; }}
                onJobSubmitted={() => refreshQueue()}
            />
            {/* OnboardingOverlay — self-manages visibility via localStorage */}
            <OnboardingOverlay />
            {/* Toast notification container — action feedback from any tab */}
            <ShToastContainer />
            {/* Cmd+K command palette */}
            <ShCommandPalette
                open={paletteOpen.value}
                onClose={() => { paletteOpen.value = false; }}
                onSelect={item => { paletteOpen.value = false; if (item.action) item.action(); }}
                items={paletteItems}
            />
        </div>
    );
}
