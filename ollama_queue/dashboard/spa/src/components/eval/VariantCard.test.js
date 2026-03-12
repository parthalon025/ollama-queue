// VariantCard.test.js
// Tests for VariantCard component — structural rendering checks.
// useActionFeedback is mapped to a no-op mock via jest.config.cjs moduleNameMapper.
import { h } from 'preact';
import _VariantCard from './VariantCard.jsx';
const VariantCard = _VariantCard.default || _VariantCard;

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

const BASE = { id: 'variant-A', label: 'Baseline', provider: 'ollama', params: {} };

test('renders variant label', () => {
  const vnode = VariantCard({ variant: BASE });
  expect(findText(vnode)).toMatch(/Baseline/);
});

test('falls back to id when no label', () => {
  const vnode = VariantCard({ variant: { ...BASE, label: null } });
  expect(findText(vnode)).toMatch(/variant-A/);
});

test('shows Production badge when is_production', () => {
  const vnode = VariantCard({ variant: { ...BASE, is_production: true } });
  expect(findText(vnode)).toMatch(/Production/);
});

test('shows Recommended badge when is_recommended but not is_production', () => {
  const vnode = VariantCard({ variant: { ...BASE, is_recommended: true, is_production: false } });
  expect(findText(vnode)).toMatch(/Recommended/);
});

test('hides Recommended badge when is_production is also true', () => {
  const vnode = VariantCard({ variant: { ...BASE, is_recommended: true, is_production: true } });
  expect(findText(vnode)).not.toMatch(/Recommended/);
});

test('stability badge: f1_stdev < 0.03 → stable', () => {
  const vnode = VariantCard({ variant: { ...BASE, f1_stdev: 0.02 } });
  expect(findText(vnode)).toMatch(/stable/);
});

test('stability badge: 0.03 ≤ f1_stdev < 0.07 → variable', () => {
  const vnode = VariantCard({ variant: { ...BASE, f1_stdev: 0.05 } });
  expect(findText(vnode)).toMatch(/variable/);
});

test('stability badge: f1_stdev ≥ 0.07 → unstable', () => {
  const vnode = VariantCard({ variant: { ...BASE, f1_stdev: 0.10 } });
  expect(findText(vnode)).toMatch(/unstable/);
});

test('no stability badge when f1_stdev is null', () => {
  const vnode = VariantCard({ variant: { ...BASE, f1_stdev: null } });
  expect(findText(vnode)).not.toMatch(/stable|variable|unstable/);
});

test('truncates system_prompt at 60 chars with ellipsis', () => {
  const long = 'A'.repeat(80);
  const vnode = VariantCard({ variant: { ...BASE, system_prompt: long } });
  const text = findText(vnode);
  expect(text).toMatch(/A{60}…/);
  expect(text).not.toMatch(/A{61}/);
});

test('no prompt preview when system_prompt is absent', () => {
  const vnode = VariantCard({ variant: { ...BASE, system_prompt: null } });
  const divs = findAll(vnode, n => n.props?.class === 'variant-card__prompt-preview');
  expect(divs).toHaveLength(0);
});

test('shows up to 3 param pills', () => {
  const variant = { ...BASE, params: { a: 1, b: 2, c: 3, d: 4 } };
  const vnode = VariantCard({ variant });
  const pills = findAll(vnode, n => n.props?.class === 'param-pill');
  expect(pills.length).toBeLessThanOrEqual(3);
});

test('adds selected class when selected=true', () => {
  const vnode = VariantCard({ variant: BASE, selected: true });
  const root = findAll(vnode, n => typeof n.props?.class === 'string' && n.props.class.includes('variant-card--selected'));
  expect(root.length).toBeGreaterThan(0);
});
