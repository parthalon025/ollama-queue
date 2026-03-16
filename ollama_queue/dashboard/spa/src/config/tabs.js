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
    subtitle:  "LIVE COMMAND CENTER",
  },
  {
    id:        'plan',
    icon:      '◫',
    label:     'Schedule',
    tooltip:   'Recurring jobs and upcoming run times',
    namespace: 'QUEUE',
    page:      'PLAN',
    subtitle:  "RECURRING JOBS AND RUN TIMES",
  },
  {
    id:        'history',
    icon:      '◷',
    label:     'History',
    tooltip:   'Completed and failed jobs',
    namespace: 'QUEUE',
    page:      'HISTORY',
    subtitle:  "COMPLETED AND FAILED JOBS",
  },
  {
    id:        'models',
    icon:      '⊞',
    label:     'Models',
    tooltip:   'Installed AI models and downloads',
    namespace: 'QUEUE',
    page:      'MODELS',
    subtitle:  "INSTALLED MODELS AND DOWNLOADS",
  },
  {
    id:        'settings',
    icon:      '⚙',
    label:     'Settings',
    tooltip:   'Configure queue thresholds and defaults',
    namespace: 'QUEUE',
    page:      'CONFIG',
    subtitle:  "THRESHOLDS, DEFAULTS, DAEMON CONTROLS",
  },
  {
    id:        'eval',
    icon:      '⊡',
    label:     'Eval',
    tooltip:   'Test and compare AI model configurations',
    namespace: 'QUEUE',
    page:      'EVAL',
    subtitle:  "TEST AND COMPARE MODEL CONFIGURATIONS",
  },
  {
    id:        'consumers',
    icon:      '⇄',
    label:     'Consumers',
    tooltip:   'Detected Ollama consumers and routing',
    namespace: 'QUEUE',
    page:      'CONSUMERS',
    subtitle:  "CONSUMER DETECTION AND ROUTING",
  },
  {
    id:        'performance',
    icon:      '⊘',
    label:     'Perf',
    tooltip:   'Model performance stats and system health',
    namespace: 'QUEUE',
    page:      'PERF',
    subtitle:  "MODEL THROUGHPUT AND SYSTEM HEALTH",
  },
  {
    id:        'backends',
    icon:      '⊟',
    label:     'Backends',
    tooltip:   'Multi-backend fleet management and routing intelligence',
    namespace: 'QUEUE',
    page:      'BACKENDS',
    subtitle:  "MULTI-GPU FLEET MANAGEMENT AND ROUTING",
  },
];
