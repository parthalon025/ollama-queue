// stores/eval.js is mapped to stores-eval.cjs via jest.config.cjs moduleNameMapper.
import evalStoreMock from '../stores/eval.js';

import _ActiveEvalStrip from './ActiveEvalStrip.jsx';
const ActiveEvalStrip = _ActiveEvalStrip.default || _ActiveEvalStrip;

beforeEach(() => {
  evalStoreMock.evalActiveRun.value = null;
});

test('returns null when no active eval', () => {
  evalStoreMock.evalActiveRun.value = null;
  expect(ActiveEvalStrip()).toBeNull();
});

test('returns null when eval is complete', () => {
  evalStoreMock.evalActiveRun.value = { run_id: 1, status: 'complete', phase: 'done', progress_pct: 100 };
  expect(ActiveEvalStrip()).toBeNull();
});

test('renders when eval is running', () => {
  evalStoreMock.evalActiveRun.value = { run_id: 1, phase: 'judging', status: 'judging', progress_pct: 60 };
  const vnode = ActiveEvalStrip();
  expect(vnode).toBeTruthy();
  expect(vnode.type).toBe('div');
});

test('renders phase label text', () => {
  evalStoreMock.evalActiveRun.value = { run_id: 2, phase: 'generating', status: 'generating', progress_pct: 20 };
  function findText(v) {
    if (!v) return '';
    if (typeof v === 'string') return v;
    if (Array.isArray(v)) return v.map(findText).join('');
    if (v.props?.children) return findText(v.props.children);
    return '';
  }
  const vnode = ActiveEvalStrip();
  expect(findText(vnode)).toContain('Generating');
});
