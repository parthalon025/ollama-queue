# Eval Enhancement Design — Self-Improving Multi-Provider Eval System

**Date:** 2026-03-11
**Status:** Approved (brainstorming complete)
**Scope:** ollama-queue eval pipeline (eval/, api/, dashboard/spa/, db/)
**Goal:** Transform the eval pipeline from a manual variant comparison tool into a self-improving, multi-provider evaluation system that guides users through systematic LLM output optimization.

## BLUF

16 design sections spanning schema changes, multi-provider support (Ollama + Claude + OpenAI), a self-improving suggestions engine with OPRO optimization, Unsloth fine-tuning integration, judge debiasing and caching, general-purpose task abstraction via YAML configs, and a Claude/OpenAI oracle for calibration gating. Research-backed by 12 framework comparisons (promptfoo, DSPy, DeepEval, TextGrad, etc.) and 25+ academic papers.

## Research Artifacts

- `~/Documents/research/2026-03-11-llm-eval-best-practices-gap-analysis.md` — 7 gaps from academic literature
- `~/Documents/research/2026-03-11-llm-eval-framework-comparison.md` — 6 gaps from 12 open-source frameworks

---

## Section 1: Schema Changes

### New Columns on `eval_variants`

```sql
ALTER TABLE eval_variants ADD COLUMN system_prompt TEXT;
ALTER TABLE eval_variants ADD COLUMN params TEXT;           -- JSON: Ollama options bag
ALTER TABLE eval_variants ADD COLUMN training_config TEXT;  -- JSON: Unsloth/fine-tune provenance
ALTER TABLE eval_variants ADD COLUMN provider TEXT DEFAULT 'ollama';  -- 'ollama' | 'claude' | 'openai'
```

**Migration strategy:**
- `ALTER TABLE ADD COLUMN` in `schema.py` migrations
- Catch `sqlite3.OperationalError` specifically; check message for "duplicate column" (Lesson #1552)
- Backfill: `UPDATE eval_variants SET params = '{}' WHERE params IS NULL`
- System variants (A–H, M) get `params = '{}'`, `provider = 'ollama'`

### Ollama Params Validation Allowlist

```python
VALID_OLLAMA_PARAMS = frozenset({
    "top_k", "top_p", "mirostat", "mirostat_eta", "mirostat_tau",
    "repeat_penalty", "repeat_last_n", "presence_penalty", "frequency_penalty",
    "seed", "stop", "tfs_z", "typical_p", "num_predict", "num_keep",
    "num_batch", "num_thread", "num_gpu",
})
```

**Validation rules:**
- Parse `params` as JSON dict; reject non-dict
- Reject keys not in `VALID_OLLAMA_PARAMS`
- Reject `temperature` or `num_ctx` in params (use flat columns — prevents ambiguity)
- Fuzzy suggestion on typos: `difflib.get_close_matches()`
- `training_config` has no validation allowlist (freeform, display-only)

### Judge Result Cache Table

```sql
CREATE TABLE IF NOT EXISTS eval_cache (
    principle_hash TEXT NOT NULL,
    target_hash TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    judge_mode TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    reasoning TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (principle_hash, target_hash, judge_model, judge_mode)
);
```

### Cost Tracking Column

```sql
ALTER TABLE eval_runs ADD COLUMN cost_json TEXT;
```

### Oracle Results Column

```sql
ALTER TABLE eval_runs ADD COLUMN oracle_json TEXT;
```

### Suggestions Column

```sql
ALTER TABLE eval_runs ADD COLUMN suggestions_json TEXT;
```

---

## Section 2: Provider Abstraction

### Provider Interface

```python
class EvalProvider:
    """Unified interface for LLM calls across providers."""
    def generate(self, prompt: str, system: str | None,
                 model: str, temperature: float,
                 params: dict | None) -> tuple[str | None, dict]:
        """Returns (text, usage_metadata)."""
        ...

class OllamaProvider(EvalProvider):
    """Routes through ollama-queue proxy (existing _call_proxy)."""

class ClaudeProvider(EvalProvider):
    """Anthropic SDK — anthropic.Anthropic().messages.create()"""

class OpenAIProvider(EvalProvider):
    """OpenAI SDK — openai.OpenAI().chat.completions.create()"""
```

**Routing rules:**
- Ollama goes through existing `_call_proxy()` → ollama-queue serialization
- Claude/OpenAI go direct via SDKs (they handle concurrency server-side)
- All three return `(text, usage_metadata)` tuple

**Dependencies (optional):**
- `anthropic>=0.40.0` — Claude API
- `openai>=1.50.0` — OpenAI API
- If not installed, provider raises: "Install `anthropic` to use Claude provider"

### Provider Settings

```python
# Per-role provider selection (settings table)
"eval.generator_provider": "ollama",
"eval.generator_model": "qwen2.5-coder:14b",
"eval.judge_provider": "ollama",
"eval.judge_model": "deepseek-r1:8b",
"eval.optimizer_provider": "claude",
"eval.optimizer_model": "claude-sonnet-4-6",
"eval.oracle_provider": "claude",
"eval.oracle_model": "claude-sonnet-4-6",

# API keys (env vars take precedence — Lesson #1009)
"eval.claude_api_key": "",      # or ANTHROPIC_API_KEY env
"eval.openai_api_key": "",      # or OPENAI_API_KEY env
"eval.openai_base_url": "",     # for OpenAI-compatible servers (vLLM, LM Studio)

# Budget guard
"eval.max_cost_per_run_usd": 1.00,
```

**Security:**
- API keys masked as `"sk-...xxxx"` in GET responses (same as data_source_token)
- Env vars take precedence (12-factor)
- Keys never logged, never in error messages
- `openai_base_url` enables OpenAI-compatible local servers (free)

### Per-Variant Provider Override

The `provider` column on `eval_variants` overrides the global default, enabling direct cross-provider comparison (local qwen vs. gpt-4o-mini on same data).

---

## Section 3: Proxy Integration

### `_call_proxy()` Signature Change

```python
def _call_proxy(http_base, model, prompt, temperature, num_ctx, timeout, source,
                priority=2, extra_params=None, system_prompt=None)
```

### Options Merge

```python
options = {"temperature": temperature, "num_ctx": num_ctx}
if extra_params:
    for k, v in extra_params.items():
        if k not in ("temperature", "num_ctx"):  # flat columns always win
            options[k] = v
```

### System Prompt Injection

Ollama `/api/generate` has a `system` field (separate from `options`):

```python
body = {"model": model, "prompt": prompt, "stream": False, "options": options, ...}
if system_prompt:
    body["system"] = system_prompt
```

### Call Sites

- `generate.py:_generate_one_item()` — passes `extra_params` and `system_prompt` from variant
- `generate.py:_self_critique()` — same
- Judge calls do NOT get variant params (judge consistency matters)

---

## Section 4: API CRUD Changes

### Updated Endpoints

| Endpoint | Change |
|----------|--------|
| `POST /api/eval/variants` | Accept `system_prompt`, `params`, `training_config`, `provider`; validate params |
| `PUT /api/eval/variants/{id}` | Add new fields to `updatable_fields`; validate params |
| `POST /api/eval/variants/{id}/clone` | Copy all new columns from original |
| `POST /api/eval/variants/generate` | Accept optional `params`, `system_prompt`, `provider` |
| `POST /api/eval/variants/import` | Read new columns from imported data |
| `GET /api/eval/variants/export` | `SELECT *` — new columns included automatically |

### Validation Helper

Shared function for all variant write paths:

```python
def _validate_variant_params(params_raw) -> str:
    """Parse, validate, return JSON string. Raises HTTPException(400)."""
```

### New Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /api/eval/variants/sweep` | Bulk-create from base config + dimension range |
| `GET /api/eval/variants/sweep/preview` | Preview count + labels before creating |
| `POST /api/eval/providers/test` | Validate API key + model works |
| `GET /api/eval/providers/models` | List available models per provider |
| `POST /api/eval/runs/{id}/oracle` | Store oracle results for a run |
| `GET /api/eval/runs/{id}/oracle` | Retrieve oracle calibration + gate results |
| `GET /api/eval/runs/{id}/suggestions` | Computed suggestions for a run |
| `GET /api/eval/runs/{id}/cost` | Detailed cost breakdown |
| `POST /api/eval/oracle/optimize` | Trigger OPRO prompt generation |
| `GET /api/eval/oracle/calibration-trend` | Kappa over time |
| `POST /api/eval/fine-tune/export-dataset` | Export training data from eval results |
| `POST /api/eval/fine-tune/generate-script` | Generate Unsloth script from config |
| `POST /api/eval/fine-tune/import` | Create variant from fine-tuned model |
| `GET /api/eval/optimization-timeline` | Timeline data across all runs |

---

## Section 5: Eval Pipeline Changes

### `analysis.py:describe_config_diff()`

Add diff blocks for: `system_prompt` (added/removed/changed), `params` (key-by-key diff with add/remove/change), `provider` (changed), `training_config` (present/absent).

### `metrics.py:render_report()`

Extend settings line with params, system prompt (truncated), provider name.

### `promote.py:do_promote_eval_run()`

Include new fields in promotion payload: `system_prompt`, `params`, `provider`, `training_config`.

### `generate.py`

Pass `extra_params` and `system_prompt` from variant to provider. Route through provider abstraction based on variant's `provider` field.

---

## Section 6: Deterministic Assertion Layer

Pre-judge smoke tests, configurable per task (via YAML):

```python
BUILTIN_ASSERTIONS = {
    "word_count":      lambda text, min=1, max=500: min <= len(text.split()) <= max,
    "regex_match":     lambda text, pattern: bool(re.search(pattern, text, re.I)),
    "no_code_blocks":  lambda text: "```" not in text,
    "no_json":         lambda text: not text.strip().startswith("{"),
    "no_repetition":   lambda text: len(set(text.split())) / max(len(text.split()), 1) > 0.5,
}
```

Runs after generation, before judge. Failures recorded as `assertion_failed`. Cost savings: ~15% of judge calls.

---

## Section 7: Judge Debiasing

### Position-Bias Dual-Ordering

For tournament/paired mode, run each comparison twice with A/B swapped. If disagreement, mark "neither." Report `position_consistency` rate per run. Below 0.7 = judge unreliable.

### Judge Result Cache

Check `eval_cache` by `(SHA-256(principle), SHA-256(target), judge_model, mode)` before every judge call. Invalidate when judge model or prompt changes.

---

## Section 8: Self-Improving Suggestions Engine

### After Each Eval Run: `compute_suggestions()`

Analyzes historical data, generates ranked next steps stored as `suggestions_json`:

**Suggestion types:**
- **explore** — dimension has single value across all variants → create sweep
- **amplify** — winner outperforms by >0.05 on one dimension → push further
- **escalate** — plateau detected (≤0.02 F1 change over 3+ runs) → next ladder level
- **revert** — F1 dropped >0.05 → clone best historical variant
- **fine-tune** — Level 5 escalation → launch Unsloth workflow

### Escalation Ladder

```
Level 1: Sampling params (top_k, top_p, mirostat)
Level 2: System prompt variations
Level 3: Prompt template changes
Level 4: Model swap (larger/different)
Level 5: Fine-tune (Unsloth)
```

Each level is cheaper than the next — exhaust before escalating.

---

## Section 9: Reasoning Feedback Loop (TextGrad Pattern)

Judge already returns `reasoning` text — currently display-only. Use it as optimization signal:

```python
def build_optimization_context(db, run_id: int) -> str:
    """Build rich context for optimizer from judge reasoning."""
    # Extract reasoning from top successes and worst failures
    # Format as diagnostic context, not just scores
```

The optimizer sees "This principle failed because it was too generic — it matches any async pattern" instead of just "F1=0.68." This is the TextGrad insight: textual feedback is more informative than numeric scores.

---

## Section 10: Claude/OpenAI Oracle Integration

### Three Oracle Modes

1. **Calibration** — sample N scored pairs, re-judge with Claude/OpenAI, compute Cohen's Kappa. Block auto-promote if Kappa < 0.4.
2. **OPRO Optimizer** — feed past results + failure reasoning to Claude/OpenAI, propose new prompt variants. Per-failure diagnosis (Self-Evolving Agent pattern).
3. **Promotion Gate** — before auto-promote, backtest winning principles with Claude/OpenAI. Block if >30% rejected.

### Settings

```python
"eval.oracle_enabled": False,
"eval.oracle_mode": "all",          # calibrate | optimize | gate | all
"eval.oracle_sample_size": 10,
"eval.oracle_kappa_threshold": 0.4,
"eval.oracle_gate_reject_threshold": 0.3,
```

### Gate Flow

```
Eval run completes
  → compute_metrics()
  → compute_suggestions()      (rule-based, free)
  → generate_eval_analysis()   (local LLM, free)
  → run_oracle()               (Claude/OpenAI, ~$0.08)
      ├── Kappa OK? → continue
      ├── Kappa low? → block promote
      └── Gate OK? → continue
  → check_auto_promote()       (3 existing gates + oracle Gate 4)
  → do_promote_eval_run()      (only if all gates pass)
```

---

## Section 11: Unsloth Integration

**Guided workflow, not automation:**

1. **Dataset Export** — `POST /api/eval/fine-tune/export-dataset` exports lessons as Unsloth-compatible JSONL. Uses best principles from completed eval runs as output field.
2. **Config Generator** — UI generates Unsloth training script from user selections (base model, LoRA rank, quantization, epochs). Script downloaded/copied, not executed.
3. **Import Result** — `POST /api/eval/fine-tune/import` creates a new variant from the fine-tuned Ollama model name + training_config.
4. **Auto-Queue Eval** — after import, suggest running eval immediately.

---

## Section 12: Eval Set Rotation (Goodhart Prevention)

- Track `eval_usage_count` per item across runs
- Deprioritize items with count > 3
- Split samples into dev (70%) and holdout (30%)
- **Auto-promote gates on holdout score, not dev score**
- Prevents optimization overfitting to fixed sample

---

## Section 13: General-Purpose Task Abstraction

### YAML-Driven Eval Tasks

```yaml
# eval-tasks/my-task.yaml
task:
  name: "Task Name"
  description: "What this task evaluates"
data_source:
  type: http | file | sqlite
  url: "..."
input_schema:
  id: string
  text: string
  cluster_id: string     # optional
generation:
  instruction: "..."
  format: "..."
scoring:
  mode: transfer | quality | comparison | custom
assertions:
  - type: word_count
    min: 5
    max: 100
  - type: regex_match
    pattern: "..."
judge:
  rubric:
    - name: dimension_name
      description: "..."
      scale: [1, 5]
```

Adding a new eval task requires zero Python code.

---

## Section 14: Cost Tracking

```json
{
  "generation_calls": 40,
  "judge_calls": 80,
  "oracle_calls": 10,
  "total_tokens": 245000,
  "estimated_cost_usd": 0.08,
  "cache_hits": 12,
  "cache_hit_rate": 0.13,
  "assertions_filtered": 6,
  "assertion_filter_rate": 0.15,
  "wall_time_s": 1847
}
```

Budget guard: `eval.max_cost_per_run_usd` pauses if exceeded.

---

## Section 15: SPA Redesign

### Variant View

**Three-level progressive disclosure:**
- **L1 (scan):** Card grid with model, score, param pills, provider badge, training hint
- **L2 (understand):** Detail panel with Config / Training / History tabs
- **L3 (decide):** Compare matrix for 2+ selected variants

### New Interactions

- **Sweep Generator** — pick base variant + dimension + range → bulk create
- **Compare Mode** — checkbox selection → side-by-side matrix
- **After-Run Suggestions** — "Next Steps" card with one-click actions
- **Oracle Report** — Kappa score, disagreements, OPRO suggestions
- **Optimization Timeline** — horizontal timeline showing escalation levels + F1 trajectory
- **Fine-Tune Wizard** — dataset export → config generator → script copy → import

### Provider Configuration

Settings view gains provider section: per-role provider/model dropdowns, API key inputs with test buttons, budget control.

### Variant Form

Provider dropdown (Ollama/Claude/OpenAI), model auto-populated per provider, system prompt textarea, params JSON editor with validation, training config (structured form + raw JSON toggle).

---

## Section 16: Testing

| Layer | Test Count | Coverage |
|-------|-----------|----------|
| Validation (params, assertions) | ~8 | Invalid/valid params, fuzzy suggestions, overlap rejection |
| Schema migration | ~4 | Column add, backfill, cache table, cost column |
| Provider abstraction | ~6 | Ollama/Claude/OpenAI routing, fallback, missing SDK |
| Proxy integration | ~5 | extra_params merge, system_prompt, flat column precedence |
| API CRUD | ~8 | Create/update/clone/import with new columns |
| Judge cache | ~4 | Hit/miss/invalidation/clear |
| Judge debiasing | ~3 | Dual ordering, consistency rate, disagreement detection |
| Assertions | ~5 | Each builtin assertion + pipeline integration |
| Suggestions engine | ~5 | Plateau, explore, amplify, revert, escalate |
| Cost tracking | ~3 | Token counting, budget guard, cost report |
| Config diff | ~3 | Params diff, system_prompt diff, provider diff |
| Promote | ~3 | New fields in payload, oracle gate |
| **Total** | **~57** | |

---

## Lessons Applied

| Lesson | Application |
|--------|------------|
| #107, #1552 | Migration catches `sqlite3.OperationalError` specifically, re-raises non-"duplicate column" errors |
| #1268 | All SQL paths updated when columns added (not just schema) |
| #405 | Every UI element has corresponding backend endpoint |
| #1475 | Provider/model settings read from config at runtime, never hardcoded constants |
| #1009 | API keys from env vars; settings are fallback |
| #1388 | UI text for key masking reflects actual state dynamically |
| CLAUDE.md | `INSERT OR IGNORE` paired with `UPDATE WHERE IS NULL` backfill |
| CLAUDE.md | `score_transfer=0` checked with `is not None`, not truthiness |
| CLAUDE.md | `db._lock` wraps every `db._connect()` call |

---

## Phasing Recommendation

| Phase | Sections | Value | Risk |
|-------|----------|-------|------|
| **Phase 1: Foundation** | 1 (Schema), 2 (Providers), 3 (Proxy), 4 (API CRUD) | Unlocks all params + multi-provider | Low — additive schema changes |
| **Phase 2: Quality** | 6 (Assertions), 7 (Judge Debias + Cache), 14 (Cost) | Cheaper, more reliable evals | Low — no breaking changes |
| **Phase 3: Intelligence** | 8 (Suggestions), 9 (Reasoning Loop), 10 (Oracle) | Self-improving loop | Medium — new orchestration |
| **Phase 4: Generalization** | 13 (Task Abstraction), 12 (Eval Set Rotation) | General-purpose system | Medium — refactors existing code |
| **Phase 5: Fine-Tuning** | 11 (Unsloth) | Closes the weight optimization loop | Low — UI + export only |
| **Phase 6: Frontend** | 15 (SPA Redesign) | New UX | Medium — full rewrite of Variants view |

---

## Decision Log

| Decision | Rationale | Alternatives Considered |
|----------|-----------|------------------------|
| Hybrid schema (flat + JSON bag) | Most-queried params stay flat, long tail in JSON | All-flat (migration per param), All-JSON (breaks existing queries) |
| Ollama `options` dict for params | 1:1 mapping to API payload, zero transformation | Modelfile reference (out-of-band), custom abstraction |
| Provider per-variant, not per-run | Enables direct cross-provider comparison in same eval | Per-run (limits comparison), global-only (no flexibility) |
| Claude/OpenAI as oracle, not primary | Cost efficiency: local does bulk, cloud validates | Cloud-only (expensive), Local-only (no calibration) |
| YAML task config, not code | Zero-code new eval tasks | Python DSL (DSPy-style), DB-stored configs |
| TextGrad reasoning loop | Richest optimization signal, already captured | Scores-only OPRO (loses diagnosis), TextGrad library (heavy dep) |
| Unsloth guided, not automated | Training automation is a separate project | Full AutoML (scope explosion), No Unsloth (misses weight optimization) |
