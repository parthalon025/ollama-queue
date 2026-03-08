import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
// What it shows: A combo-box for picking an LLM model name — shows installed
//   Ollama models (or a curated OpenAI list) with size, loaded status, and
//   type tag. Accepts freetext for custom/unlisted model names.
// Decision it drives: User can discover installed models and select one without
//   memorising exact model tags.

import { models, fetchModels } from '../store.js';

const OPENAI_MODELS = [
  { name: 'gpt-4o',        tier: 'flagship' },
  { name: 'gpt-4o-mini',   tier: 'mini' },
  { name: 'gpt-4-turbo',   tier: 'flagship' },
  { name: 'gpt-4',         tier: 'flagship' },
  { name: 'gpt-3.5-turbo', tier: 'mini' },
  { name: 'o1',            tier: 'reasoning' },
  { name: 'o1-mini',       tier: 'reasoning' },
  { name: 'o3-mini',       tier: 'reasoning' },
];

function formatBytes(bytes) {
  if (!bytes) return '';
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
  return (bytes / 1e6).toFixed(0) + ' MB';
}

export default function ModelSelect({ value, onChange, backend = 'ollama', placeholder, class: extraClass, disabled }) {
  const [open, setOpen]           = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const wrapRef  = useRef(null);
  const inputRef = useRef(null);

  // Read signal at render top to subscribe
  const installedModels = models.value;

  // Fetch installed models on first open if not yet loaded
  useEffect(() => {
    if (open && backend === 'ollama' && installedModels.length === 0) {
      fetchModels();
    }
  }, [open, backend]);

  // Pre-highlight matching row when dropdown opens
  useEffect(() => {
    if (!open) { setActiveIdx(-1); return; }
    const list = filteredList();
    const idx = list.findIndex(m => m.name === value);
    setActiveIdx(idx >= 0 ? idx : -1);
  }, [open]);

  // Close on outside mousedown
  useEffect(() => {
    if (!open) return;
    function handleMouseDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleMouseDown);
    return () => document.removeEventListener('mousedown', handleMouseDown);
  }, [open]);

  function getList() {
    if (backend === 'openai') return OPENAI_MODELS;
    return installedModels.map(m => ({ name: m.name, size_bytes: m.size_bytes, loaded: m.loaded, type_tag: m.type_tag }));
  }

  function filteredList() {
    const q = (value || '').toLowerCase();
    if (!q) return getList();
    return getList().filter(m => m.name.toLowerCase().includes(q));
  }

  function select(name) {
    onChange(name);
    setOpen(false);
    setActiveIdx(-1);
  }

  function handleKeyDown(e) {
    if (disabled) return;
    const list = filteredList();
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        setOpen(true);
        e.preventDefault();
      }
      return;
    }
    if (e.key === 'Escape') {
      setOpen(false);
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx(i => Math.min(i + 1, list.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && activeIdx >= 0 && list[activeIdx]) {
      e.preventDefault();
      select(list[activeIdx].name);
    }
  }

  const list = filteredList();
  const wrapClass = ['model-select', extraClass].filter(Boolean).join(' ');

  return (
    <div class={wrapClass} ref={wrapRef}>
      <div class="model-select__input-wrap" style={{ flex: 1 }}>
        <input
          ref={inputRef}
          class="t-input"
          style={{ paddingRight: '24px', width: '100%', boxSizing: 'border-box' }}
          type="text"
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          onInput={e => { onChange(e.currentTarget.value); if (!open) setOpen(true); }}
          onFocus={() => { if (!disabled) setOpen(true); }}
          onBlur={() => {
            // Delay close so row mousedown fires first
            setTimeout(() => {
              if (!wrapRef.current?.contains(document.activeElement)) setOpen(false);
            }, 150);
          }}
          onKeyDown={handleKeyDown}
        />
        <span class={`model-select__chevron${open ? ' model-select__chevron--open' : ''}`}>▼</span>
      </div>

      {open && (
        <div class="model-select__dropdown">
          {backend === 'ollama' && installedModels.length === 0 ? (
            <div class="model-select__loading">Loading models…</div>
          ) : list.length === 0 ? (
            <div class="model-select__empty">No matching models</div>
          ) : list.map((m, i) => (
            <div
              key={m.name}
              class={`model-select__row${i === activeIdx ? ' model-select__row--active' : ''}`}
              onMouseDown={e => { e.preventDefault(); select(m.name); }}
              onMouseEnter={() => setActiveIdx(i)}
            >
              <span class="model-select__name">{m.name}</span>
              <span class="model-select__meta">
                {backend === 'ollama' ? (
                  <>
                    {m.size_bytes ? <span>{formatBytes(m.size_bytes)}</span> : null}
                    <span class={`model-select__dot${m.loaded ? ' model-select__dot--loaded' : ' model-select__dot--unloaded'}`}>
                      {m.loaded ? '●' : '○'}
                    </span>
                    {m.type_tag ? <span class="model-select__badge">{m.type_tag}</span> : null}
                  </>
                ) : (
                  <span class="model-select__badge">{m.tier}</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
