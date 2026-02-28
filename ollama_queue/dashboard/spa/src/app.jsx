import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import { currentTab, dlqCount, fetchModels, startPolling, stopPolling } from './store';
import Dashboard from './pages/Dashboard.jsx';
import ScheduleTab from './pages/ScheduleTab.jsx';
import DLQTab from './pages/DLQTab.jsx';
import Settings from './pages/Settings.jsx';
import ModelsTab from './pages/ModelsTab.jsx';

const TABS = [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'schedule',  label: 'Schedule' },
    { id: 'dlq',       label: 'DLQ' },
    { id: 'models',    label: 'Models' },
    { id: 'settings',  label: 'Settings' },
];

export function App() {
    useEffect(() => {
        startPolling();
        return () => stopPolling();
    }, []);

    function renderPage() {
        switch (currentTab.value) {
            case 'schedule': return <ScheduleTab />;
            case 'dlq':      return <DLQTab />;
            case 'models':   return <ModelsTab />;
            case 'settings': return <Settings />;
            default:         return <Dashboard />;
        }
    }

    function handleTabClick(tabId) {
        currentTab.value = tabId;
        if (tabId === 'models') fetchModels();
    }

    return (
        <div class="min-h-screen" style="background: var(--bg-root); color: var(--text-primary);">
            {/* Desktop: top tab bar */}
            <nav class="hidden md:flex border-b" style="border-color: var(--border);">
                {TABS.map(tab => (
                    <TabButton key={tab.id} tab={tab.id} label={tabLabel(tab)} onActivate={handleTabClick} />
                ))}
            </nav>

            <main class="p-4 pb-20 md:pb-4 max-w-5xl mx-auto">
                {renderPage()}
            </main>

            {/* Mobile: bottom tab bar */}
            <nav class="md:hidden fixed bottom-0 left-0 right-0 flex border-t"
                 style="background: var(--bg-card); border-color: var(--border);">
                {TABS.map(tab => (
                    <TabButton key={tab.id} tab={tab.id} label={tabLabel(tab)} onActivate={handleTabClick} mobile />
                ))}
            </nav>
        </div>
    );
}

function tabLabel(tab) {
    if (tab.id === 'dlq' && dlqCount.value > 0) {
        return `DLQ (${dlqCount.value})`;
    }
    return tab.label;
}

function TabButton({ tab, label, mobile, onActivate }) {
    const active = currentTab.value === tab;
    const baseClass = mobile
        ? "flex-1 py-3 text-center text-sm"
        : "px-6 py-3 text-sm font-medium";
    return (
        <button
            class={baseClass}
            style={{
                color: active ? 'var(--accent)' : 'var(--text-secondary)',
                borderBottom: !mobile && active ? '2px solid var(--accent)' : 'none',
            }}
            onClick={() => onActivate(tab)}
        >
            {label}
        </button>
    );
}
