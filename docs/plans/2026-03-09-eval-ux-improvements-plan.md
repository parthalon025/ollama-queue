# Eval UI User-Friendliness Pass — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 10 broken/misleading/jargon-heavy elements in the eval pipeline UI — remove dead buttons, replace alert() popups, add variant descriptions, fix a permanently-stuck checklist, render analysis markdown, and improve labels throughout.

**Architecture:** Backend adds a `description` TEXT column to `eval_variants` and populates it for all 9 system variants. Frontend makes 4 surgical edits: RunRow.jsx, RunTriggerPanel.jsx, SetupChecklist.jsx, VariantRow.jsx. No new dependencies. No new components.

**Tech Stack:** Python/FastAPI (backend), Preact 10 + @preact/signals (frontend), SQLite (db.py schema), pytest (backend tests), npm run build (frontend verification).

---

### Task 1: Add `description` column to eval_variants schema

**Files:**
- Modify: `ollama_queue/db.py:311-323` (CREATE TABLE eval_variants)
- Modify: `ollama_queue/db.py:524-542` (system variant seed data)
- Test: `tests/test_db.py`

**Step 1: Write the failing test**

In `tests/test_db.py`, add to the eval variants section:

```python
def test_eval_variants_have_description(tmp_path):
    db = Database(tmp_path / "q.db")
    db.initialize()
    with db._lock:
        conn = db._connect()
        variants = conn.execute("SELECT id, description FROM eval_variants WHERE is_system = 1").fetchall()
    assert len(variants) == 9
    for row in variants:
        assert row["description"] is not None and len(row["description"]) > 10, \
            f"Variant {row['id']} has missing or empty description"
```

**Step 2: Run test to verify it fails**

```bash
cd /home/justin/Documents/projects/ollama-queue
source .venv/bin/activate
pytest tests/test_db.py::test_eval_variants_have_description -v
```
Expected: FAIL — `description` column does not exist.

**Step 3: Add `description` column to the CREATE TABLE statement**

In `ollama_queue/db.py`, find the `eval_variants` CREATE TABLE block (~line 311). Add the column after `is_active`:

```sql
-- Before (last two lines of the CREATE TABLE):
                is_active           INTEGER DEFAULT 1,
                created_at          TEXT NOT NULL

-- After:
                is_active           INTEGER DEFAULT 1,
                description         TEXT,
                created_at          TEXT NOT NULL
```

Then add an `_add_column_if_missing` call so live DBs get the column on restart. Find the block near line 131 where other `_add_column_if_missing` calls are made (there's already one for `recurring_jobs.description`). Add:

```python
self._add_column_if_missing(conn, "eval_variants", "description", "TEXT")
```

**Step 4: Add descriptions to the system variant seed tuples**

The seed data (~line 524) currently has tuples of 8 fields. Add a `description` string as the 9th field, then update the INSERT statement.

Change the tuple unpacking line:
```python
# Before:
for var_id, label, tmpl_id, model, temperature, num_ctx, is_recommended, is_system in variants:

# After:
for var_id, label, tmpl_id, model, temperature, num_ctx, is_recommended, is_system, description in variants:
```

Change the INSERT:
```python
# Before:
conn.execute(
    """INSERT OR IGNORE INTO eval_variants
       (id, label, prompt_template_id, model, temperature, num_ctx,
        is_recommended, is_system, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (var_id, label, tmpl_id, model, temperature, num_ctx, is_recommended, is_system, created_at),
)

# After:
conn.execute(
    """INSERT OR IGNORE INTO eval_variants
       (id, label, prompt_template_id, model, temperature, num_ctx,
        is_recommended, is_system, description, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (var_id, label, tmpl_id, model, temperature, num_ctx, is_recommended, is_system, description, created_at),
)
```

Update the seed tuples to include a description as the 9th element:

```python
variants = [
    ("A", "Baseline", "fewshot", "deepseek-r1:8b", 0.7, 4096, 0, 1,
     "Control config — few-shot examples anchor the output format. Smallest context window. Compare all others against this."),
    ("B", "Causal reasoning", "zero-shot-causal", "deepseek-r1:8b", 0.6, 8192, 0, 1,
     "Asks the model to reason about why a failure happened, not just what happened. No examples — pure reasoning."),
    ("C", "Grouped context", "chunked", "deepseek-r1:8b", 0.6, 8192, 0, 1,
     "Splits each lesson into small chunks before generating. Prevents the model from losing context in long lessons."),
    ("D", "Causal + large model", "zero-shot-causal", "qwen3:14b", 0.6, 8192, 1, 1,
     "Same causal reasoning as B but with a 14B model. Tests whether more model capacity improves principle quality."),
    ("E", "Grouped + large model", "chunked", "qwen3:14b", 0.6, 8192, 1, 1,
     "Chunked input with the 14B model — combines the focused context of C with the capacity of D."),
    ("F", "Contrastive", "contrastive", "deepseek-r1:8b", 0.6, 8192, 1, 1,
     "Asks the model to state when the principle does NOT apply. Sharper scope reduces false positives."),
    ("G", "Contrastive + large model", "contrastive", "qwen3:14b", 0.6, 8192, 1, 1,
     "Contrastive prompt with the 14B model. Tests whether a bigger model follows scope constraints more precisely."),
    ("H", "Contrastive + self-critique", "contrastive-multistage", "deepseek-r1:8b", 0.6, 8192, 1, 1,
     "Two-pass: first extract the abstract pattern, then distill a principle. Most deliberate output, slowest (2× LLM calls)."),
    ("M", "Mechanism extraction", "mechanism", "qwen3:8b", 0.6, 8192, 0, 1,
     "Captures root-cause mechanisms (trigger → failure → consequence) instead of surface rules. Orthogonal approach."),
]
```

Note: variant M is new — it was not in the original seed list. Add it here for the first time.

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_db.py::test_eval_variants_have_description -v
pytest tests/test_db.py -v -q
```
Expected: all pass.

**Step 6: Commit**

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add description column to eval_variants + seed 9 system variants with plain-English descriptions"
```

---

### Task 2: Include `description` in GET /api/eval/variants response

The `list_eval_variants()` endpoint in `api.py` already does `SELECT * FROM eval_variants`, so `description` will be included automatically once the column exists. This task adds a test to verify it appears in the API response.

**Files:**
- Test: `tests/test_api_eval_variants.py`

**Step 1: Write the failing test**

In `tests/test_api_eval_variants.py`, add:

```python
def test_list_variants_includes_description(client):
    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    system_variants = [v for v in variants if v.get("is_system")]
    assert len(system_variants) >= 9
    for v in system_variants:
        assert "description" in v, f"Variant {v['id']} missing description key"
        assert v["description"] and len(v["description"]) > 10, \
            f"Variant {v['id']} has empty description in API response"
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_api_eval_variants.py::test_list_variants_includes_description -v
```
Expected: FAIL (column not returned or not populated).

**Step 3: Verify implementation is already correct**

`SELECT * FROM eval_variants` in `list_eval_variants()` automatically includes the new column. No code change needed — once Task 1 is done, this test should pass.

**Step 4: Run tests**

```bash
pytest tests/test_api_eval_variants.py -v -q
```
Expected: all pass.

**Step 5: Commit**

```bash
git add tests/test_api_eval_variants.py
git commit -m "test: verify description field included in GET /api/eval/variants response"
```

---

### Task 3: Remove stub buttons from RunRow.jsx

Two buttons ("Score again", "Export") have no `onClick` handlers. They visually imply functionality that doesn't exist.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx:372-377`

**Step 1: Find and remove the two stub buttons**

In `RunRow.jsx`, find these two buttons (around line 372):

```jsx
<button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}>
  Score again
</button>
<button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}>
  Export
</button>
```

Delete both `<button>` elements entirely.

**Step 2: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```
Expected: Build succeeds, no errors.

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx
git commit -m "fix: remove non-functional Score again and Export stub buttons from RunRow"
```

---

### Task 4: Replace alert() tooltips with inline reveal in RunTriggerPanel.jsx

Three `?` buttons call `alert(EVAL_TRANSLATIONS.*.tooltip)`. Replace with a single state variable that shows tooltip text inline.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`

**Step 1: Add activeTooltip state**

After the existing state declarations (around line 33), add:

```jsx
const [activeTooltip, setActiveTooltip] = useState(null);
```

**Step 2: Replace the three alert() calls**

Each `?` button currently looks like:
```jsx
<button
  type="button"
  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}
  onClick={() => alert(EVAL_TRANSLATIONS.per_cluster.tooltip)}
  aria-label="Info about items per group"
>
  ?
</button>
```

Replace each `onClick={() => alert(...)}` with `onClick={() => setActiveTooltip(activeTooltip === 'per_cluster' ? null : 'per_cluster')}`.

Do this for all three fields: `per_cluster`, `judge_model`, `judge_mode_selector`.

**Step 3: Add tooltip reveal divs below each field group**

After the per-cluster `<div>` row, add:
```jsx
{activeTooltip === 'per_cluster' && (
  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', marginTop: '0.25rem', lineHeight: 1.5 }}>
    {EVAL_TRANSLATIONS.per_cluster.tooltip}
  </div>
)}
```

Repeat the same pattern for `judge_model` and `judge_mode_selector`.

**Step 4: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```
Expected: Build succeeds.

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx
git commit -m "fix: replace alert() tooltip popups with inline reveal in RunTriggerPanel"
```

---

### Task 5: Show variant descriptions in RunTriggerPanel checkboxes

Add a secondary dim description line under each variant checkbox. Description comes from the `description` field on the variant object (populated by Task 1).

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`

**Step 1: Find the variant checkbox rendering loop**

In RunTriggerPanel.jsx, the variant checkboxes render in two loops (systemVariants and userVariants). Each `<label>` row currently looks like:

```jsx
<label key={variant.id} class="eval-checkbox-row">
  <input
    type="checkbox"
    checked={selectedVariants.includes(variant.id)}
    onChange={() => toggleVariant(variant.id)}
  />
  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
    {variant.id} — {variant.label}
  </span>
  {variant.is_recommended ? (
    <span class="eval-badge eval-badge-recommended">★ Recommended</span>
  ) : null}
  {variant.latest_f1 != null && (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
      Score: {Math.round(variant.latest_f1 * 100)}%
    </span>
  )}
</label>
```

**Step 2: Wrap in a flex column and add description line**

Replace the `<label>` content with a two-line layout. The label becomes a `<div>` that wraps the checkbox + first row + optional description:

```jsx
<label key={variant.id} class="eval-checkbox-row" style={{ alignItems: 'flex-start' }}>
  <input
    type="checkbox"
    checked={selectedVariants.includes(variant.id)}
    onChange={() => toggleVariant(variant.id)}
    style={{ marginTop: '2px' }}
  />
  <div style={{ display: 'flex', flexDirection: 'column', flex: 1, gap: '2px' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
        {variant.id} — {variant.label}
      </span>
      {variant.is_recommended ? (
        <span class="eval-badge eval-badge-recommended">★ Recommended</span>
      ) : null}
      {variant.latest_f1 != null && (
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
          Score: {Math.round(variant.latest_f1 * 100)}%
        </span>
      )}
    </div>
    {variant.description && (
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', lineHeight: 1.4 }}>
        {variant.description}
      </span>
    )}
  </div>
</label>
```

Apply the same change to both the `systemVariants.map(...)` and `userVariants.map(...)` loops.

**Step 3: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx
git commit -m "feat: show variant descriptions in run trigger checkbox list"
```

---

### Task 6: Add judge mode inline description (RunTriggerPanel.jsx)

A `<select>` with 4 modes gives no guidance on when to use each. Add a one-line description below the selector that updates with the selected value.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`

**Step 1: Add description map**

At the top of the file (after imports), add a small constant:

```jsx
const JUDGE_MODE_DESCRIPTIONS = {
  bayesian: 'Uses multiple signals (paired comparisons, embeddings, scope, mechanism) — most accurate. Recommended for final decisions.',
  tournament: 'Head-to-head comparisons between configs — good for ranking when testing 3+ variants.',
  binary: 'Simple YES/NO per principle — fastest, least compute. Use for quick sanity checks.',
  rubric: '1–5 scores — legacy mode. Less reliable than Bayesian; kept for backward compatibility.',
};
```

**Step 2: Add description line below the judge mode select**

After the judge mode `<select>` row (the row that ends with the `?` info button), add:

```jsx
{judgeMode && JUDGE_MODE_DESCRIPTIONS[judgeMode] && (
  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginTop: '0.25rem', lineHeight: 1.5 }}>
    {JUDGE_MODE_DESCRIPTIONS[judgeMode]}
  </div>
)}
```

**Step 3: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx
git commit -m "feat: add inline description for each judge mode option in RunTriggerPanel"
```

---

### Task 7: Add input hint for Lessons per Group (RunTriggerPanel.jsx)

Silent clamping of out-of-range values confuses users. Add a dim hint below the input.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx`

**Step 1: Add hint below the per-cluster row**

After the per-cluster `<div>` row (the one with the number input and `?` button), and after the `activeTooltip === 'per_cluster'` reveal div (from Task 4), add:

```jsx
<div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginTop: '0.15rem' }}>
  1–20 · higher = slower but more reliable results
</div>
```

**Step 2: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunTriggerPanel.jsx
git commit -m "fix: add 1–20 range hint below Lessons per Group input"
```

---

### Task 8: Fix SetupChecklist — simplify to 2 real gates

`step2Complete` is hardcoded `false`. Simplify to 2 gates: (1) data source connected, (2) first run exists.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/SetupChecklist.jsx`

**Step 1: Remove step 2 (models check) entirely**

In `SetupChecklist.jsx`, replace the step completion logic block:

```jsx
// BEFORE (~lines 54-65):
const step1Complete = step1Status === 'ok';
const step2Complete = false; // User must navigate to Models tab and confirm manually
const step3Complete = step2Complete && variants.length > 5;
const step4Complete = step3Complete && runs.length > 0;
```

With:

```jsx
// AFTER:
const step1Complete = step1Status === 'ok';
const step2Complete = step1Complete && runs.length > 0;
```

**Step 2: Remove the Step 2 JSX block and renumber**

Delete the entire Step 2 JSX:
```jsx
<Step
  number={2}
  complete={step2Complete}
  disabled={!step1Complete}
  title="Make sure the AI models are installed"
  detail={...}
  actionLabel="Go to Models tab →"
  onAction={handleStep2Action}
/>
```

Also delete the `handleStep2Action` function.

Renumber the remaining steps: what was step 3 becomes step 2, step 4 becomes step 3.

Update the renumbered steps:

```jsx
<Step
  number={2}
  complete={step2Complete}
  disabled={!step1Complete}
  title="Start your first quality test"
  detail={
    !step1Complete
      ? 'Complete step 1 first.'
      : runs.length === 0
        ? 'Start your first test to see which configurations perform best.'
        : `${runs.length} run${runs.length !== 1 ? 's' : ''} completed.`
  }
  actionLabel="Start first test →"
  onAction={() => { evalSubTab.value = 'runs'; }}
/>
```

Update the `useEffect` that auto-marks setup complete:
```jsx
useEffect(() => {
  if (step2Complete) {
    saveEvalSettings({ 'eval.setup_complete': true }).catch(() => {});
  }
}, [step2Complete]);
```

**Step 3: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/SetupChecklist.jsx
git commit -m "fix: simplify SetupChecklist to 2 real gates — removes permanently-stuck step2Complete=false"
```

---

### Task 9: Replace fake sparkline with real best-score line (VariantRow.jsx)

The hardcoded `▁▃▅▇█` string is never replaced with real data.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx`

**Step 1: Find and replace the sparkline div**

In `VariantRow.jsx`, find (~line 154):

```jsx
{/* Quality sparkline placeholder */}
<div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
  Quality over time: ▁▃▅▇█ (run history below)
</div>
```

Replace with:

```jsx
{/* Best quality score (latest_f1 is already in scope from variant props) */}
<div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
  {latest_f1 != null
    ? `Best quality score: ${Math.round(latest_f1 * 100)}% — expand below to see full run history`
    : 'No runs yet — include this config in a test run to see quality scores'}
</div>
```

**Step 2: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx
git commit -m "fix: replace hardcoded ASCII sparkline with real latest_f1 score in VariantRow"
```

---

### Task 10: Inline two-click delete confirm in VariantRow.jsx

Replace `confirm('Delete variant "X"?')` browser dialog with an inline confirmation flow.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx`

**Step 1: Add pendingDelete state**

After the existing state declarations at the top of `VariantRow`:

```jsx
const [pendingDelete, setPendingDelete] = useState(false);
```

**Step 2: Replace the confirm() call in handleDelete**

Currently `handleDelete` starts with:
```jsx
if (!confirm(`Delete variant "${label}"?`)) return;
```

Remove that line entirely. The confirmation will now be inline.

**Step 3: Replace the Delete button with two-state inline confirm**

Currently the Delete button is:
```jsx
{!is_system && (
  <div>
    <button
      class="t-btn t-btn-secondary"
      style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
      disabled={deleteFb.phase === 'loading'}
      onClick={handleDelete}
    >
      {deleteFb.phase === 'loading' ? 'Deleting…' : 'Delete'}
    </button>
    {deleteFb.msg && <div class={`action-fb action-fb--${deleteFb.phase}`}>{deleteFb.msg}</div>}
  </div>
)}
```

Replace with:
```jsx
{!is_system && (
  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
    {!pendingDelete ? (
      <button
        class="t-btn t-btn-secondary"
        style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
        disabled={deleteFb.phase === 'loading'}
        onClick={evt => { evt.stopPropagation(); setPendingDelete(true); }}
      >
        Delete
      </button>
    ) : (
      <>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)' }}>
          Delete "{label}"?
        </span>
        <button
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '3px 8px', color: 'var(--status-error)', borderColor: 'var(--status-error)' }}
          disabled={deleteFb.phase === 'loading'}
          onClick={handleDelete}
        >
          {deleteFb.phase === 'loading' ? 'Deleting…' : 'Yes, delete'}
        </button>
        <button
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '3px 8px' }}
          onClick={evt => { evt.stopPropagation(); setPendingDelete(false); }}
        >
          Cancel
        </button>
      </>
    )}
    {deleteFb.msg && <div class={`action-fb action-fb--${deleteFb.phase}`}>{deleteFb.msg}</div>}
  </div>
)}
```

**Step 4: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx
git commit -m "fix: replace confirm() delete dialog with inline two-click confirmation in VariantRow"
```

---

### Task 11: Structure-preserving markdown renderer in RunRow.jsx

`analysis_md` is displayed in a `<pre>` tag, showing raw `**bold**` and `## Header` syntax. Replace with a simple inline renderer.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx`

**Step 1: Add simpleRenderMd function**

At the top of `RunRow.jsx` (after imports, before the component), add:

```jsx
// Converts AI-generated markdown prose to readable plain text.
// Handles: ## headers → bold label, **x** → x, - bullet → • bullet.
// No library needed — analysis_md is structured prose, not full markdown.
function simpleRenderMd(text) {
  if (!text) return '';
  return text
    .replace(/^#{1,3} (.+)$/gm, '[$1]')       // ## Header → [Header]
    .replace(/\*\*(.+?)\*\*/g, '$1')            // **bold** → bold
    .replace(/^- (.+)$/gm, '• $1')             // - item → • item
    .replace(/\n{3,}/g, '\n\n')                // collapse 3+ blank lines
    .trim();
}
```

**Step 2: Update the analysis panel**

Find the analysis panel `<pre>` element (~line 339):

```jsx
<pre style={{
  fontFamily: 'var(--font-body)',
  fontSize: 'var(--type-body)',
  color: 'var(--text-primary)',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  margin: 0,
  lineHeight: 1.6,
}}>
  {analysis_md}
</pre>
```

Replace `<pre>` with `<div>` and call `simpleRenderMd`:

```jsx
<div style={{
  fontFamily: 'var(--font-body)',
  fontSize: 'var(--type-body)',
  color: 'var(--text-primary)',
  whiteSpace: 'pre-line',
  wordBreak: 'break-word',
  margin: 0,
  lineHeight: 1.6,
}}>
  {simpleRenderMd(analysis_md)}
</div>
```

**Step 3: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx
git commit -m "fix: replace raw <pre> markdown display with structure-preserving simpleRenderMd in RunRow analysis panel"
```

---

### Task 12: Show winner variant label + model in RunRow

"Winner: Config B" is cryptic. Look up the variant label from `evalVariants` signal.

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx`

**Step 1: Import evalVariants signal**

At the top of `RunRow.jsx`, the import from `store.js` currently includes `evalActiveRun, evalSubTab, fetchEvalRuns, fetchEvalVariants, startEvalPoll`. Add `evalVariants`:

```jsx
import { API, evalActiveRun, evalSubTab, evalVariants, fetchEvalRuns, fetchEvalVariants, startEvalPoll } from '../../store.js';
```

**Step 2: Compute winner label near the top of the component**

After the `const variantIds = Object.keys(parsedMetrics);` line, add:

```jsx
// Look up winner variant label for display. Falls back to bare ID if variants not loaded yet.
const winnerVariantRow = winner_variant
  ? (evalVariants.value || []).find(v => v.id === winner_variant)
  : null;
const winnerLabel = winnerVariantRow
  ? `${winner_variant} — ${winnerVariantRow.label}`
  : winner_variant;
```

**Step 3: Update the L1 winner display**

Find the winner span in L1 (~line 180):
```jsx
{winner_variant && (
  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
    Winner: Config {winner_variant}
  </span>
)}
```

Replace with:
```jsx
{winner_variant && (
  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
    Winner: {winnerLabel}
  </span>
)}
```

**Step 4: Show model in L2**

In the L2 metrics section, after the `{judge_model && ...}` scorer info line, add:

```jsx
{winnerVariantRow?.model && (
  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.25rem' }}>
    Winner model: {winnerVariantRow.model}
  </div>
)}
```

**Step 5: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

**Step 6: Run full test suite**

```bash
cd /home/justin/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
```
Expected: all pass (592+).

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx
git commit -m "feat: show winner variant label and model in RunRow instead of cryptic Config ID"
```

---

### Task 13: Final build + full test suite

**Step 1: Full backend tests**

```bash
cd /home/justin/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
```
Expected: all pass.

**Step 2: Final SPA build**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```
Expected: no errors, `dist/` updated.

**Step 3: Restart service and verify live**

```bash
systemctl --user restart ollama-queue
sleep 2
curl -s http://127.0.0.1:7683/api/eval/variants | python3 -m json.tool | grep -A2 '"id": "A"'
```
Expected: JSON includes `"description": "Control config..."`.

**Step 4: Invoke finishing-a-development-branch skill**
