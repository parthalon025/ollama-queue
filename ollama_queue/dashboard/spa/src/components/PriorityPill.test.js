import { h } from 'preact';
import _PriorityPill from './PriorityPill.jsx';
const PriorityPill = _PriorityPill.default || _PriorityPill;

describe('PriorityPill', () => {
  test('priority 1 renders critical tier', () => {
    const vnode = PriorityPill({ priority: 1 });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('priority-critical');
    expect(vnode.props.children).toBe('critical');
  });

  test('priority 5 renders high tier', () => {
    const vnode = PriorityPill({ priority: 5 });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('priority-high');
    expect(vnode.props.children).toBe('high');
  });

  test('priority 4 is high', () => {
    const vnode = PriorityPill({ priority: 4 });
    expect(vnode.props.class).toContain('priority-high');
    expect(vnode.props.children).toBe('high');
  });

  test('priority 7 renders normal tier', () => {
    const vnode = PriorityPill({ priority: 7 });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('priority-normal');
    expect(vnode.props.children).toBe('normal');
  });

  test('priority 10 renders low tier', () => {
    const vnode = PriorityPill({ priority: 10 });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('priority-low');
    expect(vnode.props.children).toBe('low');
  });

  test('priority outside range renders unknown fallback', () => {
    const vnode = PriorityPill({ priority: 99 });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('priority-unknown');
    expect(vnode.props.children).toBe('?');
  });

  test('always returns a span element', () => {
    for (const p of [1, 3, 4, 6, 8, 10, 0, 11, -1]) {
      const vnode = PriorityPill({ priority: p });
      expect(vnode.type).toBe('span');
    }
  });
});
