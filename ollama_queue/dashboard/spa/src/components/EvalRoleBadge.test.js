// ../stores/health.js is mapped to stores.cjs via jest.config.cjs moduleNameMapper.
// ../stores/eval.js is not imported by EvalRoleBadge directly; no extra mock needed.

import _EvalRoleBadge from './EvalRoleBadge.jsx';
const EvalRoleBadge = _EvalRoleBadge.default || _EvalRoleBadge;

function findText(v) {
  if (!v) return '';
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.map(findText).join('');
  if (v.props?.children) return findText(v.props.children);
  return '';
}

test('renders judge role', () => {
  const vnode = EvalRoleBadge({ role: 'judge', f1: 0.87 });
  expect(vnode.type).toBe('button');
  expect(findText(vnode)).toContain('judge');
});

test('renders generator role', () => {
  const vnode = EvalRoleBadge({ role: 'generator', f1: null });
  expect(findText(vnode)).toContain('generator');
});

test('applies role class', () => {
  const vnode = EvalRoleBadge({ role: 'judge', f1: null });
  expect(vnode.props.class).toContain('eval-role-badge--judge');
});

test('omits F1Score when f1 is null', () => {
  const vnode = EvalRoleBadge({ role: 'generator', f1: null });
  function findByClass(v, cls) {
    if (!v) return null;
    if (typeof v === 'string') return null;
    if (v.props?.class?.includes(cls)) return v;
    if (Array.isArray(v.props?.children)) {
      for (const c of v.props.children) { const r = findByClass(c, cls); if (r) return r; }
    }
    if (v.props?.children) return findByClass(v.props.children, cls);
    return null;
  }
  expect(findByClass(vnode, 'f1-score')).toBeNull();
});
