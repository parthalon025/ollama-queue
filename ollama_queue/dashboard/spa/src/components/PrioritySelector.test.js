import { jest } from '@jest/globals';
import _PrioritySelector from './PrioritySelector.jsx';
const PrioritySelector = _PrioritySelector.default || _PrioritySelector;

// Vnode helpers
function findAll(vnode, pred, acc = []) {
  if (!vnode) return acc;
  if (pred(vnode)) acc.push(vnode);
  if (typeof vnode === 'object' && vnode.props) {
    const ch = vnode.props.children;
    [].concat(ch || []).forEach(c => findAll(c, pred, acc));
  }
  return acc;
}
function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string' || typeof vnode === 'number') return String(vnode);
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode && vnode.props) {
    const ch = vnode.props.children;
    return Array.isArray(ch) ? ch.map(findText).join('') : findText(ch);
  }
  return '';
}

test('renders all 5 named priority levels', () => {
  const vnode = PrioritySelector({ value: 5, onChange: () => {} });
  const text = findText(vnode);
  expect(text).toMatch(/critical/i);
  expect(text).toMatch(/high/i);
  expect(text).toMatch(/normal/i);
  expect(text).toMatch(/low/i);
  expect(text).toMatch(/background/i);
});

test('calls onChange with numeric value on button click', () => {
  const onChange = jest.fn();
  const vnode = PrioritySelector({ value: 5, onChange });
  // Find the Critical button (value=1) and simulate click
  const buttons = findAll(vnode, n => n?.type === 'button');
  const critBtn = buttons.find(b => findText(b).match(/critical/i));
  expect(critBtn).toBeTruthy();
  critBtn.props.onClick();
  expect(onChange).toHaveBeenCalledWith(1);
});

test('marks selected level with distinct style', () => {
  const vnode = PrioritySelector({ value: 5, onChange: () => {} });
  // Normal (value=5) should be "selected" — find button with Normal text
  const buttons = findAll(vnode, n => n?.type === 'button');
  const normalBtn = buttons.find(b => findText(b).match(/^normal$/i));
  const critBtn = buttons.find(b => findText(b).match(/^critical$/i));
  // Selected button should have a different style/class than unselected
  const normalStyle = normalBtn?.props?.style || '';
  const critStyle = critBtn?.props?.style || '';
  expect(normalStyle).not.toEqual(critStyle);
});
