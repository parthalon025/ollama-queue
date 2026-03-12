// ../stores/index.js is mapped to stores.cjs via jest.config.cjs moduleNameMapper.
// ../stores/eval.js is mapped to stores-eval.cjs via jest.config.cjs moduleNameMapper.
import storeMock from '../stores/index.js';
import evalStoreMock from '../stores/eval.js';

import _SystemSummaryLine from './SystemSummaryLine.jsx';
const SystemSummaryLine = _SystemSummaryLine.default || _SystemSummaryLine;

beforeEach(() => {
  storeMock.currentJob.value = null;
  storeMock.queueDepth.value = 0;
  evalStoreMock.evalWinner.value = null;
});

test('renders idle when no job active', () => {
  const vnode = SystemSummaryLine();
  expect(vnode).toBeTruthy();
  expect(vnode.type).toBe('div');
  function findText(v) {
    if (!v) return '';
    if (typeof v === 'string') return v;
    if (Array.isArray(v)) return v.map(findText).join('');
    if (v.props?.children) return findText(v.props.children);
    return '';
  }
  expect(findText(vnode)).toContain('idle');
});

test('renders model chip when job is active', () => {
  storeMock.currentJob.value = { model: 'qwen2.5:7b', id: 42 };
  const vnode = SystemSummaryLine();
  function findText(v) {
    if (!v) return '';
    if (typeof v === 'string') return v;
    if (Array.isArray(v)) return v.map(findText).join('');
    if (typeof v === 'object') {
      // handle Fragment (type is a function) — recurse into props.children
      if (v.props?.children != null) return findText(v.props.children);
    }
    return '';
  }
  const text = findText(vnode);
  // ModelChip receives model='qwen2.5:7b' — verify the prop reached it
  function findProp(v, propName) {
    if (!v || typeof v !== 'object') return null;
    if (v.props?.[propName]) return v.props[propName];
    if (Array.isArray(v.props?.children)) {
      for (const c of v.props.children) { const r = findProp(c, propName); if (r) return r; }
    } else if (v.props?.children) {
      return findProp(v.props.children, propName);
    }
    return null;
  }
  expect(findProp(vnode, 'model')).toBe('qwen2.5:7b');
});
