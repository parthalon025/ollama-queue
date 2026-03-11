// HeroCard.test.js
// Tests for tooltip prop behavior — uses node+mock environment (same as GanttChart.test.js)
// h() mock returns plain objects: { type, props } for structural assertions.
import { h } from 'preact';
import _HeroCardModule from './HeroCard.jsx';
// babel-jest compiles ESM default exports to CJS exports.default — unwrap for Jest's ESM vm mode
const HeroCard = _HeroCardModule.default || _HeroCardModule;

/**
 * Walk a vnode tree and return all nodes matching predicate.
 */
function findAll(vnode, predicate) {
  if (!vnode || typeof vnode !== 'object') return [];
  const results = [];
  if (predicate(vnode)) results.push(vnode);
  const children = vnode.props?.children;
  if (Array.isArray(children)) {
    for (const child of children) results.push(...findAll(child, predicate));
  } else if (children) {
    results.push(...findAll(children, predicate));
  }
  return results;
}

test('renders tooltip on label when provided', () => {
  const vnode = HeroCard({ label: 'Jobs/24h', value: '42', tooltip: 'Total jobs completed in the last 24 hours.' });
  // Find any node with a title prop containing the tooltip text
  const withTitle = findAll(vnode, n => n.props?.title && n.props.title.includes('Total jobs completed'));
  expect(withTitle.length).toBeGreaterThan(0);
});

test('renders without tooltip icon when not provided', () => {
  const vnode = HeroCard({ label: 'Jobs/24h', value: '42' });
  // When no tooltip, no node should have aria-label
  const withAriaLabel = findAll(vnode, n => n.props?.['aria-label'] !== undefined);
  expect(withAriaLabel).toHaveLength(0);
  // Also verify no '?' text node appears (the icon is absent, not just unlabelled)
  const withQuestionMark = findAll(vnode, n => n === '?' || n?.props?.children === '?');
  expect(withQuestionMark).toHaveLength(0);
});
