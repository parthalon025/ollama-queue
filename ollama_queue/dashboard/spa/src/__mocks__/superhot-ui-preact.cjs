// Mock for superhot-ui/preact — all Preact components are pass-through wrappers
// that return their children. This lets POJO tests traverse the vnode tree without
// hitting real DOM/canvas dependencies.
const { h } = require('./preact.cjs');
const passthrough = (props) => h('div', null, props.children);

module.exports = {
    ShMantra: passthrough,
    ShCommandPalette: passthrough,
    ShFrozen: passthrough,
    ShEmptyState: passthrough,
    ShPageBanner: passthrough,
    ShTimeChart: passthrough,
    ShThreatPulse: passthrough,
    ShCrtToggle: passthrough,
    ShDataTable: passthrough,
    ShCollapsible: passthrough,
    ShStatCard: passthrough,
    ShStatsGrid: passthrough,
    ShSkeleton: passthrough,
    ShGlitch: passthrough,
    ShShatter: passthrough,
    ShStatusBadge: passthrough,
    ShPipeline: passthrough,
};
