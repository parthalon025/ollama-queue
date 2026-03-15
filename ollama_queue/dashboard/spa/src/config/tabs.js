// What it shows: The single authoritative definition for all tab-aware components.
// Decision it drives: ALL_TABS (keyboard shortcuts), Sidebar nav, BottomNav, ShPageBanner
//   props, and ShCommandPalette nav items all derive from this one array — no more
//   three separate NAV_ITEMS constants that can get out of sync.

import nowIcon        from '../assets/icons/now.png';
import planIcon       from '../assets/icons/plan.png';
import historyIcon    from '../assets/icons/history.png';
import modelsIcon     from '../assets/icons/models.png';
import settingsIcon   from '../assets/icons/settings.png';
import evalIcon       from '../assets/icons/eval.png';
import consumersIcon  from '../assets/icons/consumers.png';
import perfIcon       from '../assets/icons/performance.png';
import backendsIcon   from '../assets/icons/backends.png';

export const TAB_CONFIG = [
    { id: 'now',         icon: nowIcon,       label: 'Now',       tooltip: "Live view — what's running right now",           namespace: 'QUEUE',  page: 'NOW',         subtitle: 'live job status' },
    { id: 'plan',        icon: planIcon,      label: 'Schedule',  tooltip: 'Recurring jobs and upcoming run times',           namespace: 'QUEUE',  page: 'PLAN',        subtitle: 'recurring schedule' },
    { id: 'history',     icon: historyIcon,   label: 'History',   tooltip: 'Completed and failed jobs',                       namespace: 'QUEUE',  page: 'HISTORY',     subtitle: 'completed jobs' },
    { id: 'models',      icon: modelsIcon,    label: 'Models',    tooltip: 'Installed AI models and downloads',               namespace: 'OLLAMA', page: 'MODELS',      subtitle: 'installed models' },
    { id: 'settings',    icon: settingsIcon,  label: 'Settings',  tooltip: 'Configure queue thresholds and defaults',         namespace: 'QUEUE',  page: 'SETTINGS',    subtitle: 'thresholds + defaults' },
    { id: 'eval',        icon: evalIcon,      label: 'Eval',      tooltip: 'Test and compare AI model configurations',        namespace: 'EVAL',   page: 'RUNS',        subtitle: 'prompt evaluation' },
    { id: 'consumers',   icon: consumersIcon, label: 'Consumers', tooltip: 'Detected Ollama consumers and routing',           namespace: 'SYSTEM', page: 'CONSUMERS',   subtitle: 'ollama-calling services' },
    { id: 'performance', icon: perfIcon,      label: 'Perf',      tooltip: 'Model performance stats and system health',       namespace: 'SYSTEM', page: 'PERFORMANCE', subtitle: 'throughput + load' },
    { id: 'backends',    icon: backendsIcon,  label: 'Backends',  tooltip: 'Multi-backend fleet management and routing intelligence', namespace: 'SYSTEM', page: 'BACKENDS', subtitle: 'inference hosts' },
];
