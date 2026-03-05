import { h } from 'preact';
import { useEffect } from 'preact/hooks';
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
            case 'plan':     return <Plan />;
            case 'history':  return <History />;
            case 'models':   return <ModelsTab />;
            case 'settings': return <Settings />;
            case 'eval':     return <Eval />;
            default:         return <Now />;
        }
    }

    const daemonState = status.value?.daemon ?? null;

    return (
        <div class="layout-root" style="background: var(--bg-base); color: var(--text-primary);">
            <Sidebar
                active={currentTab.value}
                onNavigate={handleNavigate}
                daemonState={daemonState}
                dlqCount={dlqCount.value}
            />
            <main class="layout-main animate-page-enter">
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
