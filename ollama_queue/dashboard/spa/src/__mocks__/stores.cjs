// Minimal stores mock for jest — stubs signal values and fetch functions used by components.
// GanttChart.test.js only tests pure helper functions; this stub satisfies the import.
const signal = (v) => ({ value: v });
module.exports = {
    API: 'http://localhost:7683/api',
    queue: signal([]),
    queueEtas: signal([]),
    currentJob: signal(null),
    queueDepth: signal(0),
    currentTab: signal('queue'),
    modelFilter: signal(null),
    dlqCount: signal(0),
    heatmapData: signal([]),
    dlqSchedulePreview: signal([]),
    modelPerformance: signal(null),
    performanceCurve: signal(null),
    status: signal({ _poll_ts: null }),
    fetchJobRuns: async () => [],
    fetchModelPerformance: () => {},
    fetchPerformanceCurve: () => {},
    refreshQueue: () => {},
    retryJob: async () => {},
    // Eval barrel re-exports needed by eval/ components
    evalSettings: signal({}),
    evalTemplates: signal([]),
    fetchEvalVariants: async () => {},
    // Health store exports needed by eval/ components
    backendsData: signal({ backends: [] }),
    // Connection/status signals needed by CohesionHeader
    connectionStatus: signal('connected'),
};
