// Minimal stores mock for jest — stubs signal values and fetch functions used by components.
// GanttChart.test.js only tests pure helper functions; this stub satisfies the import.
const signal = (v) => ({ value: v });
module.exports = {
    API: 'http://localhost:7683/api',
    queue: signal([]),
    queueEtas: signal([]),
    currentJob: signal(null),
    heatmapData: signal([]),
    dlqSchedulePreview: signal([]),
    modelPerformance: signal(null),
    performanceCurve: signal(null),
    fetchJobRuns: async () => [],
    fetchModelPerformance: () => {},
    fetchPerformanceCurve: () => {},
    refreshQueue: () => {},
    retryJob: async () => {},
};
