// stores/eval.js and hooks/useActionFeedback.js are mapped to CJS mocks via jest.config.cjs moduleNameMapper.
import _EvalNextStepsCard from './EvalNextStepsCard.jsx';
const EvalNextStepsCard = _EvalNextStepsCard.default || _EvalNextStepsCard;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  // Invoke component functions so nested components are rendered
  if (typeof vnode.type === 'function') {
    try { return findText(vnode.type(vnode.props || {})); } catch (_) { return ''; }
  }
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}

test('returns null when no suggestions', () => { expect(EvalNextStepsCard({ suggestions: [] })).toBeNull(); });
test('renders suggestion titles', () => {
  const vnode = EvalNextStepsCard({ suggestions: [{ title: 'Clone variant-C', action_type: 'clone_variant', action_label: 'Clone' }] });
  expect(findText(vnode)).toMatch(/Clone variant-C/);
});
test('shows max 3 suggestions', () => {
  const suggestions = [1,2,3,4,5].map(i => ({ title: `Step ${i}`, action_type: 'run_oracle' }));
  const vnode = EvalNextStepsCard({ suggestions });
  const text = findText(vnode);
  expect(text).toMatch(/Step 1/);
  expect(text).not.toMatch(/Step 4/);
});
