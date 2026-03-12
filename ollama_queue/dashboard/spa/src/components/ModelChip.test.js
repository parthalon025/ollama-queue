import { h } from 'preact';

// ../stores/health.js is mapped to stores.cjs via jest.config.cjs moduleNameMapper.
// Under Jest's experimental ESM, CJS default export is the module.exports object.
import storesMock from '../stores/health.js';

import _ModelChip from './ModelChip.jsx';
const ModelChip = _ModelChip.default || _ModelChip;

describe('ModelChip', () => {
  beforeEach(() => {
    storesMock.currentTab.value = 'queue';
  });

  test('renders a button element', () => {
    const vnode = ModelChip({ model: 'qwen2.5:7b' });
    expect(vnode.type).toBe('button');
  });

  test('displays the model name', () => {
    const vnode = ModelChip({ model: 'qwen2.5:7b' });
    expect(vnode.props.children).toBe('qwen2.5:7b');
  });

  test('clicking sets currentTab to models', () => {
    const vnode = ModelChip({ model: 'qwen2.5:7b' });
    vnode.props.onClick();
    expect(storesMock.currentTab.value).toBe('models');
  });

  test('button has model-chip class', () => {
    const vnode = ModelChip({ model: 'llama3:8b' });
    expect(vnode.props.class).toBe('model-chip');
  });
});
