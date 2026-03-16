// Stubs for superhot-ui Preact components — each renders its children with
// a data-sh-effect attribute that tests can assert on via tree traversal.
// Uses the same h() from preact.cjs so returned values are traversable POJOs.
const { h } = require('./preact.cjs');

module.exports = {
    // Renders children with data-sh-status so tests can assert badge variant
    ShStatusBadge: ({ status, label, children }) =>
        h('span', { 'data-sh-effect': 'status-badge', 'data-sh-status': status, 'data-sh-label': label }, children),

    // data-sh-active lets tests assert whether the pulse is triggered
    ShThreatPulse: ({ active, persistent, children }) =>
        h('div', {
            'data-sh-effect': 'threat-pulse',
            'data-sh-active': String(active),
            'data-sh-persistent': String(!!persistent),
        }, children),

    // data-sh-ts lets tests verify the timestamp multiplier (seconds → ms)
    ShFrozen: ({ timestamp, children }) =>
        h('span', { 'data-sh-effect': 'frozen', 'data-sh-ts': timestamp }, children),

    // data-sh-active lets tests verify edge-trigger logic is wired in
    ShGlitch: ({ active, intensity, children }) =>
        h('span', {
            'data-sh-effect': 'glitch',
            'data-sh-active': String(active),
            'data-sh-intensity': intensity || '',
        }, children),

    // onClick is forwarded from onDismiss so shatter cancel tests can call it
    ShShatter: ({ onDismiss, children }) =>
        h('div', { 'data-sh-effect': 'shatter', onClick: onDismiss }, children),

    // Pass-through layout stubs — needed so imports don't silently resolve to undefined
    ShPageBanner: ({ namespace, page, subtitle }) =>
        h('header', { 'data-sh': 'page-banner', 'data-sh-ns': namespace, 'data-sh-page': page }),
    ShStatsGrid: ({ stats, children }) =>
        h('div', { 'data-sh': 'stats-grid' }, children),
    ShStatCard: ({ label, value, children }) =>
        h('div', { 'data-sh': 'stat-card', 'data-sh-label': label }),
    ShCollapsible: ({ summary, children }) =>
        h('details', { 'data-sh': 'collapsible' }, children),
    ShDataTable: ({ rows, columns, children }) =>
        h('div', { 'data-sh': 'data-table' }, children),
    ShTimeChart: ({ data }) =>
        h('div', { 'data-sh': 'time-chart' }),
    ShCrtToggle: ({ checked, onChange }) =>
        h('button', { 'data-sh': 'crt-toggle', onClick: onChange }),
    ShPipeline: ({ stages, children }) =>
        h('div', { 'data-sh': 'pipeline' }, children),
};
