// EmptyState.test.js
// Tests for EmptyState component — uses node+mock environment (same as HeroCard.test.js).
// h() mock returns plain objects: { type, props } for structural assertions.
import { jest } from '@jest/globals';
import { h } from 'preact';
import _EmptyState from './EmptyState.jsx';
const EmptyState = _EmptyState.default || _EmptyState;

/**
 * Walk a vnode tree and collect all nodes matching predicate.
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

/** Return all text leaf nodes reachable from vnode. */
function collectText(vnode) {
  if (typeof vnode === 'string' || typeof vnode === 'number') return [String(vnode)];
  if (!vnode || typeof vnode !== 'object') return [];
  const texts = [];
  const children = vnode.props?.children;
  if (Array.isArray(children)) {
    for (const c of children) texts.push(...collectText(c));
  } else if (children !== undefined && children !== null) {
    texts.push(...collectText(children));
  }
  return texts;
}

test('renders headline and body', () => {
  const vnode = EmptyState({ headline: 'Queue is empty', body: 'Jobs you submit will appear here.' });
  const texts = collectText(vnode);
  expect(texts.some(t => t.includes('Queue is empty'))).toBe(true);
  expect(texts.some(t => t.includes('Jobs you submit will appear here.'))).toBe(true);
});

test('renders action button when action prop provided', () => {
  const onClick = jest.fn();
  const vnode = EmptyState({ headline: 'Empty', body: 'Nothing here.', action: { label: '+ Submit a job', onClick } });
  const buttons = findAll(vnode, n => n.type === 'button');
  expect(buttons.length).toBeGreaterThan(0);
  const btnTexts = collectText(buttons[0]);
  expect(btnTexts.some(t => t.includes('Submit a job'))).toBe(true);
  // Verify onClick is wired
  buttons[0].props.onClick();
  expect(onClick).toHaveBeenCalled();
});

test('renders without action button when action not provided', () => {
  const vnode = EmptyState({ headline: 'Empty', body: 'Nothing.' });
  const buttons = findAll(vnode, n => n.type === 'button');
  expect(buttons.length).toBe(0);
});
