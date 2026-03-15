// TAB_CONFIG — single source of truth for tab metadata.
// Each entry mirrors the Sidebar tab list and adds namespace/page/subtitle
// for ShPageBanner (namespace = left word, page = right word in pixel banner).
//
// NOTE: Keep in sync with Sidebar.jsx ITEMS array (id, icon, label, tooltip).

export const TAB_CONFIG = [
  {
    id:        'now',
    icon:      '●',
    label:     'Now',
    tooltip:   "Live view — what's running right now",
    namespace: 'QUEUE',
    page:      'NOW',
    subtitle:  "live command center",
  },
  {
    id:        'plan',
    icon:      '◫',
    label:     'Schedule',
    tooltip:   'Recurring jobs and upcoming run times',
    namespace: 'QUEUE',
    page:      'PLAN',
    subtitle:  "recurring jobs and upcoming run times",
  },
  {
    id:        'history',
    icon:      '◷',
    label:     'History',
    tooltip:   'Completed and failed jobs',
    namespace: 'QUEUE',
    page:      'HISTORY',
    subtitle:  "completed and failed jobs",
  },
  {
    id:        'models',
    icon:      '⊞',
    label:     'Models',
    tooltip:   'Installed AI models and downloads',
    namespace: 'QUEUE',
    page:      'MODELS',
    subtitle:  "installed models and downloads",
  },
  {
    id:        'settings',
    icon:      '⚙',
    label:     'Settings',
    tooltip:   'Configure queue thresholds and defaults',
    namespace: 'QUEUE',
    page:      'CONFIG',
    subtitle:  "thresholds, defaults, and daemon controls",
  },
  {
    id:        'eval',
    icon:      '⊡',
    label:     'Eval',
    tooltip:   'Test and compare AI model configurations',
    namespace: 'QUEUE',
    page:      'EVAL',
    subtitle:  "test and compare ai model configurations",
  },
  {
    id:        'consumers',
    icon:      '⇄',
    label:     'Consumers',
    tooltip:   'Detected Ollama consumers and routing',
    namespace: 'QUEUE',
    page:      'CONSUMERS',
    subtitle:  "ollama consumer detection and routing",
  },
  {
    id:        'performance',
    icon:      '⊘',
    label:     'Perf',
    tooltip:   'Model performance stats and system health',
    namespace: 'QUEUE',
    page:      'PERF',
    subtitle:  "model throughput and system health",
  },
  {
    id:        'backends',
    icon:      '⊟',
    label:     'Backends',
    tooltip:   'Multi-backend fleet management and routing intelligence',
    namespace: 'QUEUE',
    page:      'BACKENDS',
    subtitle:  "multi-gpu fleet management and routing",
  },
];
