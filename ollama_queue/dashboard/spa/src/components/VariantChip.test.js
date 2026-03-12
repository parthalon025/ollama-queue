import { h } from 'preact';
import _VariantChip from './VariantChip.jsx';
const VariantChip = _VariantChip.default || _VariantChip;

// Helper: flatten children to an array (handles single child or array)
function childArray(vnode) {
  const c = vnode.props.children;
  return Array.isArray(c) ? c : (c != null ? [c] : []);
}

// Helper: find a child span with a given class (ignores falsy entries)
function findSpan(children, cls) {
  return children.find(c => c && c.type === 'span' && c.props.class && c.props.class.includes(cls));
}

describe('VariantChip', () => {
  test('renders variantId in variant-id span', () => {
    const vnode = VariantChip({ variantId: 'variant-A', f1: null, isProduction: false, isRecommended: false });
    expect(vnode.type).toBe('div');
    const children = childArray(vnode);
    const idSpan = findSpan(children, 'variant-id');
    expect(idSpan).toBeTruthy();
    expect(idSpan.props.children).toBe('variant-A');
  });

  test('shows filled star when isProduction=true', () => {
    const vnode = VariantChip({ variantId: 'A', f1: null, isProduction: true, isRecommended: false });
    const children = childArray(vnode);
    const starSpan = findSpan(children, 'variant-star');
    expect(starSpan).toBeTruthy();
    expect(starSpan.props.children).toBe('★');
  });

  test('shows hollow star when isRecommended=true and isProduction=false', () => {
    const vnode = VariantChip({ variantId: 'B', f1: null, isProduction: false, isRecommended: true });
    const children = childArray(vnode);
    const starSpan = findSpan(children, 'variant-star');
    expect(starSpan).toBeTruthy();
    expect(starSpan.props.children).toBe('☆');
  });

  test('shows no star when both isProduction and isRecommended are false', () => {
    const vnode = VariantChip({ variantId: 'C', f1: null, isProduction: false, isRecommended: false });
    const children = childArray(vnode);
    const starSpan = findSpan(children, 'variant-star');
    expect(starSpan).toBeFalsy();
  });

  test('shows provider badge when provider is set', () => {
    const vnode = VariantChip({ variantId: 'D', f1: null, isProduction: false, isRecommended: false, provider: 'openai' });
    const children = childArray(vnode);
    const providerSpan = findSpan(children, 'provider-badge');
    expect(providerSpan).toBeTruthy();
    expect(providerSpan.props.children).toBe('openai');
  });
});
