// ../stores/health.js is mapped to stores.cjs via jest.config.cjs moduleNameMapper.
import storeMock from '../stores/health.js';

import _CohesionHeader from './CohesionHeader.jsx';
const CohesionHeader = _CohesionHeader.default || _CohesionHeader;

beforeEach(() => {
  storeMock.dlqCount.value = 0;
});

test('renders a header element', () => {
  const vnode = CohesionHeader();
  expect(vnode).toBeTruthy();
  expect(vnode.type).toBe('header');
});
