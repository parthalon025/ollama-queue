import { h } from 'preact';
import _F1Score from './F1Score.jsx';
const F1Score = _F1Score.default || _F1Score;

describe('F1Score', () => {
  test('value=0.85 renders with f1-good class', () => {
    const vnode = F1Score({ value: 0.85 });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('f1-good');
    expect(vnode.props.class).toContain('f1-score');
  });

  test('value=0.70 renders with f1-warn class', () => {
    const vnode = F1Score({ value: 0.70 });
    expect(vnode.props.class).toContain('f1-warn');
    expect(vnode.props.class).toContain('f1-score');
  });

  test('value=0.45 renders with f1-bad class', () => {
    const vnode = F1Score({ value: 0.45 });
    expect(vnode.props.class).toContain('f1-bad');
    expect(vnode.props.class).toContain('f1-score');
  });

  test('value=null renders em dash with f1-null class', () => {
    const vnode = F1Score({ value: null });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('f1-null');
    expect(vnode.props.children).toBe('—');
  });

  test('value=0.80 boundary renders with f1-good class', () => {
    const vnode = F1Score({ value: 0.80 });
    expect(vnode.props.class).toContain('f1-good');
  });

  test('positive delta renders f1-delta-pos span with + prefix', () => {
    const vnode = F1Score({ value: 0.75, delta: 0.05 });
    // children is [formatted, deltaEl]
    const children = Array.isArray(vnode.props.children)
      ? vnode.props.children
      : [vnode.props.children];
    const deltaSpan = children.find(c => c && c.type === 'span');
    expect(deltaSpan).toBeTruthy();
    expect(deltaSpan.props.class).toContain('f1-delta-pos');
    expect(deltaSpan.props.children).toBe('+0.05');
  });

  test('negative delta renders f1-delta-neg span', () => {
    const vnode = F1Score({ value: 0.65, delta: -0.10 });
    const children = Array.isArray(vnode.props.children)
      ? vnode.props.children
      : [vnode.props.children];
    const deltaSpan = children.find(c => c && c.type === 'span');
    expect(deltaSpan).toBeTruthy();
    expect(deltaSpan.props.class).toContain('f1-delta-neg');
    expect(deltaSpan.props.children).toBe('-0.10');
  });
});
