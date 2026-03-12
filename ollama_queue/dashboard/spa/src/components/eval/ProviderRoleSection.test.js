// ProviderRoleSection.test.js
// Tests for ProviderRoleSection component — structural rendering checks.
// useState mock returns [initialValue, noop] so tests only cover initial render state.
// useActionFeedback is mapped to a no-op mock via jest.config.cjs moduleNameMapper.
import { h } from 'preact';
import _ProviderRoleSection from './ProviderRoleSection.jsx';
const ProviderRoleSection = _ProviderRoleSection.default || _ProviderRoleSection;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (typeof vnode.type === 'function') {
    try { return findText(vnode.type(vnode.props || {})); } catch (_) { return ''; }
  }
  if (vnode.props) {
    const c = vnode.props.children;
    return Array.isArray(c) ? c.map(findText).join('') : findText(c);
  }
  return '';
}

function findAll(vnode, pred) {
  if (!vnode || typeof vnode !== 'object') return [];
  const results = [];
  if (pred(vnode)) results.push(vnode);
  const c = vnode.props?.children;
  if (Array.isArray(c)) c.forEach(ch => results.push(...findAll(ch, pred)));
  else if (c) results.push(...findAll(c, pred));
  return results;
}

test('renders role title capitalized', () => {
  const vnode = ProviderRoleSection({ role: 'generator', settings: {} });
  expect(findText(vnode)).toMatch(/Generator/);
});

test('renders role description for judge', () => {
  const vnode = ProviderRoleSection({ role: 'judge', settings: {} });
  expect(findText(vnode)).toMatch(/Scores the test outputs/);
});

test('renders provider select with ollama/claude/openai options', () => {
  const vnode = ProviderRoleSection({ role: 'generator', settings: {} });
  const text = findText(vnode);
  expect(text).toMatch(/ollama/);
  expect(text).toMatch(/claude/);
  expect(text).toMatch(/openai/);
});

test('hides API Key field when provider defaults to ollama', () => {
  // useState mock returns initial value — provider defaults to 'ollama'
  const vnode = ProviderRoleSection({ role: 'generator', settings: {} });
  const inputs = findAll(vnode, n => n.props?.type === 'password');
  expect(inputs).toHaveLength(0);
});

test('shows API Key field when provider is claude (via settings)', () => {
  // useState is mocked: initial value = settings.provider = 'claude'
  const vnode = ProviderRoleSection({ role: 'generator', settings: { provider: 'claude' } });
  const inputs = findAll(vnode, n => n.props?.type === 'password');
  expect(inputs.length).toBeGreaterThan(0);
});

test('hides max_cost_per_run field when provider is ollama', () => {
  const vnode = ProviderRoleSection({ role: 'generator', settings: {} });
  const text = findText(vnode);
  expect(text).not.toMatch(/Max cost per run/);
});

test('shows max_cost_per_run field when provider is openai (via settings)', () => {
  const vnode = ProviderRoleSection({ role: 'generator', settings: { provider: 'openai' } });
  const text = findText(vnode);
  expect(text).toMatch(/Max cost per run/);
});

test('Test connection button is disabled when model is empty', () => {
  // useState returns ['', noop] for model (initial empty string)
  const vnode = ProviderRoleSection({ role: 'generator', settings: {} });
  const buttons = findAll(vnode, n => n.type === 'button' && findText(n).includes('Test connection'));
  expect(buttons.length).toBeGreaterThan(0);
  expect(buttons[0].props.disabled).toBe(true);
});
