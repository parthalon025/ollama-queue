import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import { currentTab, startPolling, stopPolling } from './store';
import Dashboard from './pages/Dashboard.jsx';
import Settings from './pages/Settings.jsx';

export function App() {
    useEffect(() => {
        startPolling();
        return () => stopPolling();
    }, []);

    return (
        <div class="min-h-screen" style="background: var(--bg-root); color: var(--text-primary);">
            {/* Desktop: top tab bar */}
            <nav class="hidden md:flex border-b" style="border-color: var(--border);">
                <TabButton tab="dashboard" label="Dashboard" />
                <TabButton tab="settings" label="Settings" />
            </nav>

            <main class="p-4 pb-20 md:pb-4 max-w-5xl mx-auto">
                {currentTab.value === 'dashboard' ? <Dashboard /> : <Settings />}
            </main>

            {/* Mobile: bottom tab bar */}
            <nav class="md:hidden fixed bottom-0 left-0 right-0 flex border-t"
                 style="background: var(--bg-card); border-color: var(--border);">
                <TabButton tab="dashboard" label="Dashboard" mobile />
                <TabButton tab="settings" label="Settings" mobile />
            </nav>
        </div>
    );
}

function TabButton({ tab, label, mobile }) {
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
            onClick={() => currentTab.value = tab}
        >
            {label}
        </button>
    );
}

