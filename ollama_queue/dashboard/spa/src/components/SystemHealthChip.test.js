import _SystemHealthChip from './SystemHealthChip.jsx';
const SystemHealthChip = _SystemHealthChip.default || _SystemHealthChip;

// Helper to find text in a vnode tree
function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) {
    const children = vnode.props.children;
    return Array.isArray(children) ? children.map(findText).join('') : findText(children);
  }
  return '';
}

const baseProps = {
  daemonState: 'idle',
  dlqCount: 0,
  ram: 30, vram: 40, load: 1.2, swap: 0,
  settings: { pause_ram_pct: 85, pause_vram_pct: 90, pause_load_avg: 8 },
};

test('shows Healthy when no issues', () => {
  const vnode = SystemHealthChip(baseProps);
  expect(findText(vnode)).toMatch(/healthy/i);
});

test('shows Warning when DLQ has 1-3 entries', () => {
  const vnode = SystemHealthChip({ ...baseProps, dlqCount: 2 });
  expect(findText(vnode)).toMatch(/warning/i);
});

test('shows Issues when daemon is paused', () => {
  const vnode = SystemHealthChip({ ...baseProps, daemonState: 'paused_health' });
  expect(findText(vnode)).toMatch(/issue/i);
});

test('shows Issues when resource exceeds pause threshold', () => {
  const vnode = SystemHealthChip({ ...baseProps, ram: 90 });
  expect(findText(vnode)).toMatch(/issue/i);
});

test('shows Issues when daemon is in error state', () => {
  const vnode = SystemHealthChip({ ...baseProps, daemonState: 'error' });
  expect(findText(vnode)).toMatch(/issue/i);
});
