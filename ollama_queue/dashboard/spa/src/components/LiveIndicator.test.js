import _LiveIndicator from './LiveIndicator.jsx';
const LiveIndicator = _LiveIndicator.default || _LiveIndicator;

function findClass(vnode) {
  if (!vnode) return '';
  if (vnode.props?.class) return vnode.props.class;
  if (vnode.props?.className) return vnode.props.className;
  return '';
}

test('renders running state', () => { expect(findClass(LiveIndicator({ state: 'running' }))).toMatch(/running/); });
test('renders queued state', () => { expect(findClass(LiveIndicator({ state: 'queued' }))).toMatch(/queued/); });
test('renders in-eval state', () => { expect(findClass(LiveIndicator({ state: 'in-eval' }))).toMatch(/eval/); });
test('includes pulse class by default', () => { expect(findClass(LiveIndicator({}))).toMatch(/pulse/); });
test('omits pulse when pulse=false', () => { expect(findClass(LiveIndicator({ pulse: false }))).not.toMatch(/pulse/); });
