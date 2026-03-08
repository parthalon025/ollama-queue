# Model Select Dropdown — Design

**Date:** 2026-03-08
**Status:** Approved
**Scope:** ollama-queue SPA — eval settings, variant creation

---

## Problem

Three (now five) places in the Eval UI accept model names as free-text `<input type="text">`. Users must know exact model names, including tags. No discovery, no feedback on whether a model is installed or loaded.

## Goal

Replace all model text inputs with a combo-box component: shows installed models (with metadata) as suggestions, accepts freetext for custom names, and adapts its list to the selected backend (Ollama vs. OpenAI).

---

## Component: `ModelSelect`

### File
`ollama_queue/dashboard/spa/src/components/ModelSelect.jsx`

### API

```jsx
<ModelSelect
  value={judgeModel}
  onChange={val => setJudgeModel(val)}
  backend="ollama"              // "ollama" | "openai", default "ollama"
  placeholder="deepseek-r1:8b"
  class="eval-settings-input"  // merged with .model-select wrapper class
  disabled={isSaving}
/>
```

| Prop | Type | Required | Description |
|------|------|----------|-------------|
| `value` | string | yes | Controlled value |
| `onChange(val)` | function | yes | Called on every change (typing or selection) — receives string, not event |
| `backend` | `"ollama"` \| `"openai"` | no | Controls model list. Default: `"ollama"` |
| `placeholder` | string | no | Input placeholder |
| `class` | string | no | Extra CSS class merged onto wrapper |
| `disabled` | bool | no | Disables input + prevents dropdown open |

### Model Lists

**`backend="ollama"`:** Fetches from `GET /api/models` via existing `fetchModels()` / `models` signal in `store.js`. No new API calls.

**`backend="openai"`:** Static curated list (no network call):
```
gpt-4o        (flagship)
gpt-4o-mini   (mini)
gpt-4-turbo   (flagship)
gpt-4         (flagship)
gpt-3.5-turbo (mini)
o1            (reasoning)
o1-mini       (reasoning)
o3-mini       (reasoning)
```

---

## UX Behavior

### Opening/Closing
- Opens on **click** or **focus** if not disabled
- Closes on **Esc**, **Enter** after selection, **mousedown outside** (document listener, cleaned up on unmount), or **blur** (tab away)
- Chevron `▼` on input right edge; rotates to `▲` when open; clicking toggles

### Filtering
- Shows full list when input is empty or just focused
- Case-insensitive substring match on model name as user types
- No matches → "No matching models" non-selectable row
- Models not yet fetched (Ollama, `models.value` empty) → calls `fetchModels()`, shows loading spinner row

### Dropdown Rows

**Ollama backend:**
```
deepseek-r1:8b         4.7 GB  ● chat
qwen2.5-coder:14b      9.0 GB  ○ code
llama3.2:3b            2.0 GB  ● chat
```
- Name (left, bold monospace)
- Size (right-aligned, formatted: bytes → GB/MB)
- Loaded dot: `●` green if `loaded === true`, `○` grey if not
- `type_tag` badge: chat / code / embed / etc.

**OpenAI backend:**
```
gpt-4o        flagship
gpt-4o-mini   mini
o1            reasoning
```
- Name (left, bold)
- Tier label (right: flagship / mini / reasoning)
- No size or loaded dot (not applicable)

### Scroll & Keyboard
- `max-height: 240px`, `overflow-y: auto` on dropdown panel
- `↑` / `↓` — move highlight through filtered list
- `Enter` — select highlighted item, close
- `Esc` — close without selecting
- Mouse click on row — select, close

### Pre-highlight
When dropdown opens and current `value` matches an installed model name, that row is highlighted and scrolled into view.

### Positioning
Renders below input, full input width, elevated `z-index`. No upward-flip (eval settings are mid-page).

---

## CSS Additions (`index.css`)

New rules to add:

| Selector | Purpose |
|----------|---------|
| `.model-select` | Wrapper — `position: relative` |
| `.model-select__chevron` | Absolute right icon, `transform: rotate(180deg)` when open |
| `.model-select__dropdown` | Absolute panel — `max-height: 240px`, `overflow-y: auto`, `z-index`, border, bg |
| `.model-select__row` | Flex row — hover + keyboard highlight state |
| `.model-select__row--active` | Highlighted row (keyboard nav) |
| `.model-select__loaded-dot` | Green / grey dot |
| `.model-select__type-badge` | type_tag pill |
| `.model-select__loading` | Loading spinner row |
| `.model-select__empty` | "No matching models" row |

Follow existing token conventions: `var(--text-primary)`, `var(--bg-surface)`, `var(--border-subtle)`, `var(--type-label)`, `var(--font-mono)`.

---

## Integration Points

### 1. `JudgeDefaultsForm.jsx` (line 67)
- Replace `<input type="text">` for `judge_model` with `<ModelSelect>`
- Pass `backend={judgeBackend}` (reads existing local state)
- When `judgeBackend` changes to `"openai"`, model list switches immediately

### 2. `RunTriggerPanel.jsx` (line 268)
- Replace `<input type="text">` for per-run `judge_model` override with `<ModelSelect>`
- Pass `backend={evalSettings.value['eval.judge_backend'] ?? 'ollama'}`

### 3. `GeneralSettings.jsx` — add `analysis_model` field
- New field: `analysis_model` (backend setting exists, no UI yet)
- Add state: `const [analysisModel, setAnalysisModel] = useState(settings['eval.analysis_model'] ?? '')`
- Add to `saveEvalSettings()` call
- `<ModelSelect backend="ollama" placeholder="Leave blank to use judge model">`
- Empty string is valid — backend interprets it as "use judge model"

### 4. `VariantToolbar.jsx` (line 180)
- Replace `<input type="text">` for `model` in the "create new variant" form
- Always `backend="ollama"` — variants are Ollama-model-specific configs

### 5. New file: `components/ModelSelect.jsx`

---

## Data Flow

```
ModelSelect mounts
  └─ if backend="ollama" AND models.value is empty
       └─ fetchModels() → models.value = [...installed models]
  └─ if backend="openai"
       └─ use static OPENAI_MODELS constant (no fetch)

User focuses/clicks input
  └─ dropdown opens
  └─ filtered list renders from models.value or OPENAI_MODELS

User types
  └─ onChange(typed_value) → caller updates value prop
  └─ dropdown filters by typed_value

User selects row
  └─ onChange(model.name) → caller updates value prop
  └─ dropdown closes
```

---

## Error Handling

- `fetchModels()` already has try/catch in store.js — silently fails, dropdown shows empty
- If models fail to load: dropdown shows "No models available" and user can still type freetext
- No validation in `ModelSelect` itself — caller decides if value is required

---

## Testing

- Unit: render with `backend="ollama"` / `backend="openai"`, verify correct list
- Keyboard nav: ↑↓ highlight movement, Enter select, Esc close
- Freetext: type non-installed name, verify `onChange` fires with typed value
- Disabled: verify dropdown does not open when `disabled={true}`
- Outside click: verify dropdown closes
- Pre-highlight: open with value matching installed model, verify row is highlighted
- Integration: JudgeDefaultsForm — switch backend dropdown, verify model list changes

---

## Files Changed

| File | Type |
|------|------|
| `ollama_queue/dashboard/spa/src/components/ModelSelect.jsx` | New |
| `ollama_queue/dashboard/spa/src/index.css` | Modified — new `.model-select*` rules |
| `ollama_queue/dashboard/spa/src/components/eval/JudgeDefaultsForm.jsx` | Modified |
| `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx` | Modified |
| `ollama_queue/dashboard/spa/src/components/eval/GeneralSettings.jsx` | Modified |
| `ollama_queue/dashboard/spa/src/components/eval/VariantToolbar.jsx` | Modified |
