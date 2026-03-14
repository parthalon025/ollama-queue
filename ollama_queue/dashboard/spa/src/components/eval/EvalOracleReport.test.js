// preact/hooks is mapped to preact-hooks.cjs via jest.config.cjs moduleNameMapper.
import _EvalOracleReport from './EvalOracleReport.jsx';
const EvalOracleReport = _EvalOracleReport.default || _EvalOracleReport;

function findText(vnode) {
  if (!vnode) return ''; if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}

test('returns null when no oracle data', () => { expect(EvalOracleReport({ oracle: null })).toBeNull(); });
test('renders toggle button', () => { expect(findText(EvalOracleReport({ oracle: { kappa: 0.85 } }))).toMatch(/reliable/i); });
