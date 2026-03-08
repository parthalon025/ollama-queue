# Model Select Dropdown Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace all free-text model name inputs in the Eval UI with a combo-box component that shows installed Ollama models (or a curated OpenAI list) with metadata, while still accepting arbitrary freetext.

**Architecture:** New shared `ModelSelect` Preact component reads from the existing `models` signal in `store.js` (no new API calls). It wraps a controlled `<input>` with a custom dropdown panel. Five integration points: JudgeDefaultsForm, RunTriggerPanel, GeneralSettings (new `analysis_model` field), VariantToolbar. CSS rules added to `index.css`.

**Tech Stack:** Preact 10, @preact/signals, esbuild JSX (`h` factory — never shadow `h` in callbacks), existing `fetchModels()`/`models` signal from `store.js`.

---

## Critical Gotchas (read before touching JSX)

1. **Never use `h` or `Fragment` as a callback parameter name** — esbuild injects `h` as JSX factory. `.map(h => ...)` silently breaks all JSX rendering. Use descriptive names like `m`, `item`, `def` instead.
2. **JSX silently drops wrong prop names** — verify every prop name against the component's actual parameter destructuring.
3. **All hook calls before any `return null`** — if `useEffect`/`useState` appear after a conditional `return`, Preact throws "rendered fewer hooks than previous render".
4. **`models` signal is `models.value` (array)** — read `.value` at render top to subscribe; never call `.value` inside a callback.
5. **`onInput` vs `onChange`** — native elements use `onInput` for text changes; the `ModelSelect` component prop is `onChange` (receives a string, not an event).

---

## Task 1: CSS — Add `.model-select*` rules to `index.css`

**File:**
- Modify: `ollama_queue/dashboard/spa/src/index.css`

**Step 1: Find the insertion point**

Open `index.css` and locate the `.eval-settings-input` rule block (search for `eval-settings-input`). Add the new rules immediately after it.

**Step 2: Add CSS rules**

Append after `.eval-settings-input`:

```css
/* ── Model Select combo-box ─────────────────────────────────────────── */
.model-select {
  position: relative;
  display: flex;
  align-items: center;
}

.model-select__input-wrap {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
}

.model-select__chevron {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  pointer-events: none;
  color: var(--text-tertiary);
  font-size: 10px;
  transition: transform 0.15s ease;
  line-height: 1;
}

.model-select__chevron--open {
  transform: translateY(-50%) rotate(180deg);
}

.model-select__dropdown {
  position: absolute;
  top: calc(100% + 3px);
  left: 0;
  right: 0;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  max-height: 240px;
  overflow-y: auto;
  z-index: 200;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.model-select__row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 6px 10px;
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: var(--type-label);
  color: var(--text-primary);
  transition: background 0.1s;
}

.model-select__row:hover,
.model-select__row--active {
  background: var(--bg-surface-raised);
}

.model-select__name {
  font-weight: 600;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-select__meta {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: var(--type-label);
}

.model-select__dot {
  font-size: 10px;
}

.model-select__dot--loaded {
  color: #4caf50;
}

.model-select__dot--unloaded {
  color: var(--text-tertiary);
}

.model-select__badge {
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--bg-surface-raised);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  text-transform: lowercase;
  letter-spacing: 0.02em;
}

.model-select__empty,
.model-select__loading {
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-size: var(--type-label);
  color: var(--text-tertiary);
  font-style: italic;
}
```

**Step 3: Build to verify no CSS syntax errors**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

Expected: build completes with no errors.

**Step 4: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(spa): add model-select CSS rules"
```

---

## Task 2: Create `ModelSelect.jsx`

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/ModelSelect.jsx`

**Step 1: Write the component**

Create `ollama_queue/dashboard/spa/src/components/ModelSelect.jsx`:

```jsx
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
  const [open, setOpen]         = useState(false);
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
          onBlur={e => {
            // Delay close so row click fires first
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
```

**Step 2: Build to verify no syntax errors**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```

Expected: build completes, no errors. `dist/` updated.

**Step 3: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/ModelSelect.jsx
git commit -m "feat(spa): add ModelSelect combo-box component"
```

---

## Task 3: Wire `JudgeDefaultsForm.jsx`

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/JudgeDefaultsForm.jsx`

**Step 1: Add the import**

At the top of `JudgeDefaultsForm.jsx`, after the existing imports, add:

```js
import ModelSelect from '../ModelSelect.jsx';
```

**Step 2: Replace the judge_model text input**

Find and replace this block (lines 67–73):

```jsx
        <input
          class="t-input eval-settings-input"
          type="text"
          value={judgeModel}
          onInput={evt => setJudgeModel(evt.currentTarget.value)}
          placeholder="deepseek-r1:8b"
        />
```

Replace with:

```jsx
        <ModelSelect
          value={judgeModel}
          onChange={val => setJudgeModel(val)}
          backend={judgeBackend}
          placeholder="deepseek-r1:8b"
          class="eval-settings-input"
          disabled={saving}
        />
```

**Step 3: Build**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```

Expected: no errors.

**Step 4: Smoke test**
1. Open `/queue/ui/` → Eval → Settings
2. Click the Scorer AI input → dropdown should appear with installed models
3. Switch backend to `openai` → dropdown should switch to GPT model names
4. Switch back to `ollama` → Ollama models return

**Step 5: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/JudgeDefaultsForm.jsx
git commit -m "feat(spa): use ModelSelect for judge_model in JudgeDefaultsForm"
```

---

## Task 4: Wire `RunTriggerPanel.jsx`

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`

**Step 1: Check existing imports**

Find the import block at the top of `RunTriggerPanel.jsx`. Check if `evalSettings` is already imported from `../../store.js`. It likely is — confirm before adding.

**Step 2: Add ModelSelect import**

Add after existing imports:

```js
import ModelSelect from '../ModelSelect.jsx';
```

**Step 3: Replace the judge model text input**

Find and replace (lines 268–275):

```jsx
            <input
              type="text"
              value={judgeModel}
              onInput={e => setJudgeModel(e.target.value)}
              class="t-input"
              style={{ padding: '4px 8px', fontSize: 'var(--type-label)', flex: 1 }}
              placeholder="deepseek-r1:8b"
            />
```

Replace with:

```jsx
            <ModelSelect
              value={judgeModel}
              onChange={val => setJudgeModel(val)}
              backend={evalSettings.value['eval.judge_backend'] ?? 'ollama'}
              placeholder="deepseek-r1:8b"
              class="t-input"
              disabled={false}
            />
```

Note: `evalSettings` is already read as a signal in RunTriggerPanel — confirm it's imported; if not, add `import { evalSettings } from '../../store.js'` (but do not duplicate an existing import).

**Step 4: Build**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```

**Step 5: Smoke test**

Open Eval → Runs tab → expand the run trigger panel → the judge model field should show a dropdown.

**Step 6: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx
git commit -m "feat(spa): use ModelSelect for judge_model override in RunTriggerPanel"
```

---

## Task 5: Add `analysis_model` field to `GeneralSettings.jsx`

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/GeneralSettings.jsx`

**Step 1: Add ModelSelect import**

After existing imports, add:

```js
import ModelSelect from '../ModelSelect.jsx';
```

**Step 2: Add `analysisModel` state**

Inside `GeneralSettings()`, after the `[saveOk, setSaveOk]` line, add:

```js
  const [analysisModel, setAnalysisModel] = useState(settings['eval.analysis_model'] ?? '');
```

**Step 3: Include `analysis_model` in the save payload**

In `handleSave()`, find the `const payload = {}` block. After `IMPROVEMENT_DEFS.forEach(...)`, add:

```js
      payload['eval.analysis_model'] = analysisModel;
```

**Step 4: Add the UI field**

In the JSX `return`, after the auto-promote section's closing `</div>` (the one with `borderTop`) and before `<div class="eval-settings-form__footer">`, add:

```jsx
      {/* Analysis model */}
      <div style={{ marginTop: '1rem', borderTop: '1px solid var(--border-subtle)', paddingTop: '0.75rem' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
          Analysis model
        </div>
        <label class="eval-settings-label">
          <span>
            Analysis model
            <span class="eval-tooltip-trigger" title="Model used to generate run analysis. Leave blank to use the judge model." aria-label="Model used to generate run analysis. Leave blank to use the judge model."> ?</span>
          </span>
          <ModelSelect
            value={analysisModel}
            onChange={val => setAnalysisModel(val)}
            backend="ollama"
            placeholder="Leave blank to use judge model"
            class="eval-settings-input"
            disabled={saving}
          />
        </label>
      </div>
```

**Step 5: Build**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```

**Step 6: Smoke test**

Open Eval → Settings → General Settings panel. A new "Analysis model" field with ModelSelect should appear below auto-promote. Save with a selected model and verify it persists after reload.

**Step 7: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/GeneralSettings.jsx
git commit -m "feat(spa): add analysis_model field with ModelSelect to GeneralSettings"
```

---

## Task 6: Wire `VariantToolbar.jsx`

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/VariantToolbar.jsx`

**Step 1: Add ModelSelect import**

After existing imports, add:

```js
import ModelSelect from '../ModelSelect.jsx';
```

**Step 2: Extract the model field from the `.map()` loop**

The current code maps over a `[{ field: 'label', ... }, { field: 'model', ... }]` array. Since `model` needs a `ModelSelect` instead of a plain `<input>`, split the array: keep only `label` in the map, then render the model field separately.

Find this entire block:

```jsx
          {[
            { field: 'label', label: 'Name', placeholder: 'My custom config', type: 'text' },
            { field: 'model', label: 'Model', placeholder: 'qwen3:14b', type: 'text' },
          ].map(({ field, label, placeholder, type }) => (
            <div key={field} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '70px', flexShrink: 0 }}>
                {label}
              </label>
              <input
                type={type}
                class="t-input"
                style={{ padding: '4px 8px', fontSize: 'var(--type-label)', flex: 1 }}
                value={newVariant[field]}
                onInput={e => handleNewFieldChange(field, e.target.value)}
                placeholder={placeholder}
              />
            </div>
          ))}
```

Replace with:

```jsx
          {/* Name field */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '70px', flexShrink: 0 }}>
              Name
            </label>
            <input
              type="text"
              class="t-input"
              style={{ padding: '4px 8px', fontSize: 'var(--type-label)', flex: 1 }}
              value={newVariant.label}
              onInput={e => handleNewFieldChange('label', e.target.value)}
              placeholder="My custom config"
            />
          </div>

          {/* Model field */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '70px', flexShrink: 0 }}>
              Model
            </label>
            <ModelSelect
              value={newVariant.model}
              onChange={val => handleNewFieldChange('model', val)}
              backend="ollama"
              placeholder="qwen3:14b"
              class="t-input"
              disabled={false}
            />
          </div>
```

**Step 3: Build**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```

**Step 4: Smoke test**

Open Eval → Variants → click "New" or expand new-variant form → Model field should show a dropdown of installed models.

**Step 5: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/VariantToolbar.jsx
git commit -m "feat(spa): use ModelSelect for model field in new-variant form"
```

---

## Task 7: Final build, backend test run, and PR

**Step 1: Full production build**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build
```

Expected: clean build, no warnings about missing imports or unknown variables.

**Step 2: Run backend test suite (unchanged — confirms no regressions)**

```bash
cd /home/justin/Documents/projects/ollama-queue
source .venv/bin/activate
python3 -m pytest --timeout=120 -x -q 2>&1 | tail -10
```

Expected: all tests pass (no backend code was changed).

**Step 3: Manual end-to-end smoke test**

Restart the service so the new `dist/` is served:

```bash
systemctl --user restart ollama-queue
```

Open `/queue/ui/` → Eval → Settings:
- [ ] JudgeDefaultsForm: Scorer AI field shows installed models with size/loaded/type
- [ ] JudgeDefaultsForm: switching backend to `openai` shows GPT model list
- [ ] JudgeDefaultsForm: freetext works (type a name not in list, saves correctly)
- [ ] GeneralSettings: Analysis model field present, shows dropdown, empty saves as blank
- [ ] RunTriggerPanel: judge model override shows dropdown matching current backend setting
- [ ] VariantToolbar: new-variant model field shows dropdown

**Step 4: Create PR**

```bash
cd /home/justin/Documents/projects/ollama-queue
gh pr create --title "feat(spa): ModelSelect combo-box for all eval model inputs" --body "$(cat <<'EOF'
## Summary
- New shared `ModelSelect` component: combo-box input showing installed Ollama models (or curated OpenAI list) with size, loaded status, and type badge
- Wired into JudgeDefaultsForm, RunTriggerPanel, GeneralSettings, VariantToolbar
- Adds missing `analysis_model` UI field in GeneralSettings (backend setting existed but had no UI)
- Backend-aware: switches between Ollama and OpenAI model lists based on `judge_backend` setting
- Freetext always accepted; dropdown is a suggestion list, not a constraint

## Test plan
- [ ] Build passes: `npm run build`
- [ ] Backend tests pass: `pytest --timeout=120 -x -q`
- [ ] Scorer AI dropdown appears in Eval → Settings
- [ ] Backend toggle switches model list (ollama ↔ openai)
- [ ] Analysis model field visible and saveable
- [ ] RunTriggerPanel judge override shows dropdown
- [ ] VariantToolbar new-variant model field shows dropdown
- [ ] Freetext model name accepted at all 4 sites

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## File Summary

| File | Task | Type |
|------|------|------|
| `ollama_queue/dashboard/spa/src/index.css` | 1 | Modified |
| `ollama_queue/dashboard/spa/src/components/ModelSelect.jsx` | 2 | New |
| `ollama_queue/dashboard/spa/src/components/eval/JudgeDefaultsForm.jsx` | 3 | Modified |
| `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx` | 4 | Modified |
| `ollama_queue/dashboard/spa/src/components/eval/GeneralSettings.jsx` | 5 | Modified |
| `ollama_queue/dashboard/spa/src/components/eval/VariantToolbar.jsx` | 6 | Modified |
