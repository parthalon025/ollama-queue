// What it shows: Stack of transient notification toasts in the top-right corner.
// Decision it drives: Replaces inline action-feedback text — every action (submit,
//   cancel, retry, dismiss) pushes a toast. Error toasts persist until dismissed.
//   Success/info toasts auto-dismiss after 3s (managed in addToast).

import { ShShatter } from 'superhot-ui/preact';
import { toasts, removeToast } from '../stores/health.js';

export default function ShToastContainer() {
  const items = toasts.value;
  if (!items.length) return null;

  return (
    <div
      style={{
        position: 'fixed',
        top: '1rem',
        right: '1rem',
        zIndex: 200,
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        maxWidth: 320,
        pointerEvents: 'none',
      }}
    >
      {items.map(toast => {
        const colorMap = {
          error: 'var(--sh-threat, var(--status-error))',
          warn:  'var(--status-warning)',
          info:  'var(--sh-phosphor, var(--accent))',
        };
        const color = colorMap[toast.type] || colorMap.info;
        return (
          <ShShatter key={toast.id} onDismiss={() => removeToast(toast.id)}>
            <div
              class="data-mono"
              style={{
                background: 'var(--bg-surface)',
                border: `1px solid ${color}`,
                borderLeft: `3px solid ${color}`,
                borderRadius: 'var(--radius)',
                padding: '0.5rem 0.75rem',
                fontSize: 'var(--type-label)',
                color: 'var(--text-primary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '0.75rem',
                pointerEvents: 'all',
                boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
              }}
            >
              <span style={{ flex: 1 }}>{toast.msg}</span>
              <button
                onClick={() => removeToast(toast.id)}
                style={{
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  color: 'var(--text-tertiary)',
                  padding: 0,
                  fontSize: 'var(--type-label)',
                  flexShrink: 0,
                }}
                aria-label="Dismiss"
              >
                ×
              </button>
            </div>
          </ShShatter>
        );
      })}
    </div>
  );
}
