// What it shows: A Cmd+K command palette for fast keyboard navigation and actions.
// Decision it drives: Power users can jump to any tab, submit a job, or trigger
//   an eval run without touching the mouse.

import { useState, useEffect } from 'preact/hooks';

export default function ShCommandPaletteNative({ open, onClose, items = [] }) {
  const [query, setQuery] = useState('');

  // Reset query on open
  useEffect(() => {
    if (open) setQuery('');
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKey(e) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const q = query.toLowerCase();
  const filtered = q
    ? items.filter(it => it.label.toLowerCase().includes(q) || (it.group || '').toLowerCase().includes(q))
    : items;

  function execute(item) {
    onClose();
    if (item.action) item.action();
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.7)',
        zIndex: 500,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '10vh',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: 480,
          maxWidth: '90vw',
          background: 'var(--bg-surface)',
          border: '1px solid var(--sh-phosphor, var(--accent))',
          borderRadius: 'var(--radius)',
          overflow: 'hidden',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Search input */}
        <div style={{ display: 'flex', alignItems: 'center', padding: '0.75rem 1rem', borderBottom: '1px solid var(--border-subtle)' }}>
          <span class="data-mono" style={{ color: 'var(--sh-phosphor, var(--accent))', marginRight: '0.5rem', fontSize: 'var(--type-label)' }}>⌘K</span>
          <input
            autoFocus
            value={query}
            onInput={e => setQuery(e.target.value)}
            placeholder="Search commands..."
            style={{
              flex: 1,
              background: 'none',
              border: 'none',
              outline: 'none',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--type-body)',
            }}
          />
        </div>

        {/* Results */}
        <div style={{ maxHeight: '60vh', overflowY: 'auto' }}>
          {filtered.length === 0 && (
            <div class="data-mono" style={{ padding: '1rem', color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', textAlign: 'center' }}>
              No commands match "{query}"
            </div>
          )}
          {filtered.map((item, i) => (
            <button
              key={i}
              onClick={() => execute(item)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
                width: '100%',
                padding: '0.625rem 1rem',
                background: 'transparent',
                border: 'none',
                borderBottom: '1px solid var(--border-subtle)',
                cursor: 'pointer',
                textAlign: 'left',
                color: 'var(--text-primary)',
              }}
              class="palette-item"
            >
              {item.icon && <span style={{ fontSize: '1rem', color: 'var(--sh-phosphor, var(--accent))' }}>{item.icon}</span>}
              <span style={{ flex: 1 }}>
                <span class="data-mono" style={{ fontSize: 'var(--type-body)' }}>{item.label}</span>
                {item.group && (
                  <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', marginLeft: '0.5rem' }}>{item.group}</span>
                )}
              </span>
              {item.shortcut && (
                <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)' }}>{item.shortcut}</span>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
