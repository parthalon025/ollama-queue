// stores/index.js is mapped to stores.cjs via jest.config.cjs moduleNameMapper.
import storesMock from '../stores/index.js';

import _ActiveJobStrip from './ActiveJobStrip.jsx';
const ActiveJobStrip = _ActiveJobStrip.default || _ActiveJobStrip;

beforeEach(() => {
  storesMock.currentJob.value = null;
  storesMock.queueDepth.value = 0;
});

test('returns null when no active job', () => {
  storesMock.currentJob.value = null;
  expect(ActiveJobStrip()).toBeNull();
});

test('renders when job is active', () => {
  storesMock.currentJob.value = { model: 'deepseek-r1:8b', status: 'running', started_at: null };
  const vnode = ActiveJobStrip();
  expect(vnode).toBeTruthy();
  expect(vnode.type).toBe('div');
});

test('shows queue depth when jobs waiting', () => {
  storesMock.currentJob.value = { model: 'qwen2.5:7b', started_at: null };
  storesMock.queueDepth.value = 3;
  const vnode = ActiveJobStrip();
  // traverse children to find "3 waiting" text
  function findText(v) {
    if (!v) return '';
    if (typeof v === 'string' || typeof v === 'number') return String(v);
    if (Array.isArray(v)) return v.map(findText).join('');
    if (v.props?.children) return findText(v.props.children);
    return '';
  }
  expect(findText(vnode)).toContain('3');
});
