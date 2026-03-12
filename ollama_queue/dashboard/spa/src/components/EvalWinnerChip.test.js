// stores/eval.js is mapped to stores-eval.cjs via jest.config.cjs moduleNameMapper.
import evalStoreMock from '../stores/eval.js';

import _EvalWinnerChip from './EvalWinnerChip.jsx';
const EvalWinnerChip = _EvalWinnerChip.default || _EvalWinnerChip;

beforeEach(() => { evalStoreMock.evalWinner.value = null; });

test('returns null when no winner', () => {
  evalStoreMock.evalWinner.value = null;
  expect(EvalWinnerChip()).toBeNull();
});

test('renders when winner exists', () => {
  evalStoreMock.evalWinner.value = { id: 'C', label: 'variant-C', latest_f1: 0.87, is_production: true, is_recommended: false };
  const vnode = EvalWinnerChip();
  expect(vnode).toBeTruthy();
  expect(vnode.type).toBe('button');
});

test('shows gold star for production winner', () => {
  evalStoreMock.evalWinner.value = { id: 'C', label: 'variant-C', latest_f1: 0.87, is_production: true, is_recommended: false };
  function findText(v) {
    if (!v) return '';
    if (typeof v === 'string') return v;
    if (Array.isArray(v)) return v.map(findText).join('');
    if (v.props?.children) return findText(v.props.children);
    return '';
  }
  expect(findText(EvalWinnerChip())).toContain('★');
});

test('shows silver star for recommended-only winner', () => {
  evalStoreMock.evalWinner.value = { id: 'D', label: 'variant-D', latest_f1: 0.75, is_production: false, is_recommended: true };
  function findText(v) {
    if (!v) return '';
    if (typeof v === 'string') return v;
    if (Array.isArray(v)) return v.map(findText).join('');
    if (v.props?.children) return findText(v.props.children);
    return '';
  }
  expect(findText(EvalWinnerChip())).toContain('☆');
});
