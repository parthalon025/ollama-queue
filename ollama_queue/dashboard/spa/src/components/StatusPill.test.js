import { h } from 'preact';
import _StatusPill from './StatusPill.jsx';
const StatusPill = _StatusPill.default || _StatusPill;

describe('StatusPill', () => {
  test('queued status', () => {
    const vnode = StatusPill({ status: 'queued' });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('status-queued');
    expect(vnode.props.children).toBe('queued');
  });

  test('running status has both status-running and status-running-active', () => {
    const vnode = StatusPill({ status: 'running' });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('status-running');
    expect(vnode.props.class).toContain('status-running-active');
    expect(vnode.props.children).toBe('running');
  });

  test('complete status', () => {
    const vnode = StatusPill({ status: 'complete' });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('status-complete');
    expect(vnode.props.children).toBe('complete');
  });

  test('failed status has both status-failed and status-error', () => {
    const vnode = StatusPill({ status: 'failed' });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('status-failed');
    expect(vnode.props.class).toContain('status-error');
    expect(vnode.props.children).toBe('failed');
  });

  test('deferred status', () => {
    const vnode = StatusPill({ status: 'deferred' });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('status-deferred');
    expect(vnode.props.children).toBe('deferred');
  });

  test('cancelled status', () => {
    const vnode = StatusPill({ status: 'cancelled' });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('status-cancelled');
    expect(vnode.props.children).toBe('cancelled');
  });

  test('unknown status fallback uses passed string as label and status-unknown class', () => {
    const vnode = StatusPill({ status: 'weird' });
    expect(vnode.type).toBe('span');
    expect(vnode.props.class).toContain('status-unknown');
    expect(vnode.props.children).toBe('weird');
  });

  test('always returns a span for any status', () => {
    const statuses = ['queued', 'running', 'complete', 'failed', 'deferred', 'cancelled', 'anything'];
    for (const status of statuses) {
      const vnode = StatusPill({ status });
      expect(vnode.type).toBe('span');
    }
  });
});
