# Forge v2 — Evaluation Engine Design

**Status:** Phase 1 COMPLETE, Phase 2 COMPLETE — Phase 3 (Learn) pending
**Date:** 2026-03-17
**Owner:** ollama-queue (generic engine, lessons-db is first consumer)
**Research:** `~/Documents/research/2026-03-17-eval-oracle-darwinism-range-research.md`
**Prior art:** `~/Documents/research/2026-03-11-llm-eval-best-practices-gap-analysis.md`

---

## What Is Forge?

### Plain Language

Forge is a quality checker for AI-generated content. Think of it like a teacher grading essays:

1. **A student** (cheap AI model) writes answers about your data
2. **A teaching assistant** (another cheap model) grades those answers
3. **The professor** (expensive, smart model like Claude) spot-checks a sample of the TA's grades
4. If the TA is grading fairly — trust the TA's scores going forward
5. If the TA is giving everyone A's — retrain or replace the TA
6. Over time, the system **tries different grading rubrics** and keeps the ones that actually catch bad work

The first "student" is lessons-db. But Forge doesn't care what the student is — it works with any data source that can serve items and groups.

### Problem Statement

The current eval pipeline is broken in three compounding ways:

| Failure | Symptom | Root Cause |
|---------|---------|------------|
| Judge acquiescence | All scores = 3, F1 = 0.0 | qwen3:14b rubber-stamps everything |
| Inverted precision | Precision measures specificity, not standard precision | Formula at `judge.py:138-149` is backwards |
| APO stuck | 24 identical learnings in learnings.jsonl | Single-loop learning — never questions the measurement |

Forge fixes all three and adds the ability to discover better strategies autonomously.

### Technical Foundation

```
ForgeDataSource (Protocol/HTTP)
    | items (+ optional group labels for reporting)
    v
ForgeEngine (orchestrator)
    |-> Embedder: embed items via nomic-embed-text (or data source provides)
    |-> Pair Selector: stratified sample across embedding distance spectrum
    |-> Generator: creates principles from items using prompt variants
    |-> Judge: cheap LLM scores principle-target pairs (blind — no group info)
    |-> Oracle: expensive LLM re-scores a sample (IS the ground truth)
    |-> Calibrator: isotonic regression maps judge scale -> oracle scale
    |-> Evolver: MAP-Elites maintains archive of prompt strategies (Phase 2)
    '-> Feedback: structured results -> DataSource.on_feedback() (Phase 3)
```

---

## The Flow

### Plain Language

Every Forge cycle follows the same loop. Read top-to-bottom — each step feeds the next:

```
 1. GET DATA        "What am I evaluating?"
                    Pull lessons (or whatever) from the data source

 2. GENERATE        "What rules can I extract from this data?"
                    AI writes principles from lessons

 3. JUDGE           "Are these rules actually useful?"
                    Cheap AI scores each rule against test cases

 4. VERIFY          "Can I trust the judge?"
                    Expensive AI re-grades a sample. Compare: do they agree?

 5. CALIBRATE       "How do I correct the judge's bias?"
                    Math that maps cheap grades -> accurate grades

 6. DECIDE          "Is this variant better than what we have?"
                    If yes -> promote. If no -> try something new.

 7. EVOLVE          "What should I try next?" (Phase 2)
                    Combine best strategies, mutate, explore

 8. FEEDBACK        "What should the data source fix?" (Phase 3)
                    Tell lessons-db which principles are weak
```

### What Each Step Tells You / What You Decide

| Step | What You See | Decision It Drives |
|------|-------------|-------------------|
| 1. Get Data | Item count, embedding coverage | "Do I have enough data to evaluate?" |
| 2. Generate | Principles per variant | "Are the prompts producing reasonable output?" |
| 3. Judge | Score distribution, Spearman correlation | "Is this variant discriminating well?" |
| 4. Verify | Kappa (judge-oracle agreement), disagreement list | "Can I trust the judge, or is it rubber-stamping?" |
| 5. Calibrate | Corrected F1, calibration curve | "What are the REAL scores after bias correction?" |
| 6. Decide | Winner variant, gate pass/fail | "Should this become the new production prompt?" |
| 7. Evolve | Archive coverage, QD-score, new niches | "Is the system still finding better strategies?" |
| 8. Feedback | Weak principles, per-group accuracy, merge candidates | "What upstream data needs fixing?" |

### Technical: Key Step Details

**Step 3 (Judge) — pair selection uses embeddings, not clusters:**
- All items are embedded (nomic-embed-text locally, or data source provides embeddings)
- Pair matrix: `cosine_sim(item_i, item_j)` for candidate pairs
- Stratified sample across 4 distance quartiles:
  - Q1 (top 25% similarity): "likely applies" — tests recall
  - Q2 (25-50%): "might apply" — tests nuance
  - Q3 (50-75%): "probably doesn't" — tests specificity
  - Q4 (bottom 25%): "definitely doesn't" — tests baseline discrimination
- Judge scores all pairs blind — no group labels, no same/diff flag
- Diagnostic: Spearman rank correlation(judge_scores, embedding_similarity)
  - Spearman ~ 0.0 = judge acquiescing (all same scores)
  - Spearman > 0.4 = judge tracking similarity appropriately

**Step 4 (Verify) — oracle IS the ground truth:**
- Oracle receives the exact same prompt the judge saw
- Oracle is a stronger model (Claude Sonnet/Opus via existing `providers.py` EvalProvider)
- Sample size: `min(ceil(total_pairs * oracle_fraction), oracle_budget)`
- Default oracle_fraction: 0.2 (20%)
- Agreement: `|oracle_score - judge_score| <= 1` (proven in current oracle.py)
- Output: Cohen's kappa globally + per-group breakdown (when groups available)
- F1 computed with oracle as ground truth: oracle score >= 3 = positive class
- This replaces the old cluster-based F1 entirely

**Step 5 (Calibrate):**
- Isotonic regression: monotonic function `f(judge_score) -> calibrated_score`
- Requires >= 10 oracle-judge pairs to fit (below that, raw scores with warning)
- Per-judge-model — switching models resets calibrator
- Stored as `calibration_json` on the run row (new column)

---

## Pair Selection — Embedding-Stratified Sampling

### Plain Language (why not clusters?)

The old eval system grouped lessons by category (e.g. "error handling", "async patterns") and tested whether a principle worked within its group but not outside it. This had three problems:

1. **Punished useful principles.** A rule like "always log errors" works across many categories. The old system counted that as a false positive — too general. That's backwards.
2. **Garbage in, garbage out.** If categories were badly defined, the metrics were meaningless. The eval was measuring category quality, not principle quality.
3. **Killed genericity.** Every data source had to provide cluster labels. A new data source with no labels couldn't use Forge at all.

The new approach: **use embedding similarity to pick test pairs, and the oracle to define what's correct.** No labels needed. A principle that genuinely applies to distant items gets credit, not penalized.

### What It Shows / What You Decide

| Element | Shows | Decision |
|---------|-------|---------|
| Embedding coverage | % of items successfully embedded | "Is my data embeddable? Any items too short?" |
| Pair distance histogram | Distribution of similarity in sampled pairs | "Am I testing enough hard cases?" |
| Spearman correlation | Do judge scores track similarity? | "Is the judge discriminating at all?" (Spearman ~ 0 = acquiescing) |
| Quartile breakdown | Judge accuracy per difficulty tier | "Where does the judge struggle — easy pairs or hard ones?" |

### Technical: How It Works

```
1. Embed all items
   - Default: nomic-embed-text via Ollama (768-dim, local, free)
   - Data source can provide pre-computed embeddings via get_embeddings()
   - Cached in eval_embeddings table (item_hash -> vector), refreshed on content change

2. Build pairwise similarity matrix
   - cosine_sim(item_i, item_j) for all candidate pairs
   - For N items: N*(N-1)/2 pairs. At N=100: 4,950 pairs.

3. Stratified sample (default: 20 pairs per quartile = 80 total)
   - Q1 (sim 0.75-1.0):  "likely applies"         — tests recall
   - Q2 (sim 0.50-0.75): "might apply"             — tests nuance
   - Q3 (sim 0.25-0.50): "probably doesn't apply"  — tests specificity
   - Q4 (sim 0.00-0.25): "definitely doesn't"      — tests baseline

4. Judge scores all 80 pairs (blind — no similarity info passed)

5. Oracle re-scores 20% sample (16 pairs) — oracle score IS ground truth

6. Metrics computed:
   - Primary: kappa (judge vs oracle agreement)
   - Secondary: calibrated F1 (oracle >= 3 = positive class)
   - Diagnostic: Spearman(judge_scores, embedding_similarity)
   - Per-quartile: accuracy within each difficulty band
   - Per-group (optional): if data source provides groups, break down by group
```

**Why stratified?** Random sampling over-represents the middle of the similarity distribution. Stratification ensures Forge tests the extremes — the easy cases AND the hard cases. Hard negatives (Q3: moderately similar but principle shouldn't apply) are where judges fail most often. Without stratification, you'd rarely sample these.

**Why not just use embedding similarity as ground truth directly?** Because embedding similarity measures "topic overlap," not "does this principle apply." Two lessons can discuss the same library but have completely different failure modes. The oracle captures this nuance — embeddings are for pair selection, not scoring.

---

## Oracle Design

### Plain Language

Imagine you're grading 200 essays. You can't afford to have a professor read all 200. But you CAN have the professor read 20 of them, then check if your TA graded those same 20 the same way. If the TA agrees with the professor on 18/20 — you can probably trust the TA on the other 180.

The "professor" is Claude or GPT-4. The "TA" is the cheap local model.

### What It Shows / What You Decide

| Observation | Meaning | Action |
|------------|---------|--------|
| High kappa (>= 0.8) | Judge is reliable | Trust scores. Auto-promote is safe. |
| Medium kappa (0.6-0.8) | Judge mostly right, has blind spots | Check per-category breakdown |
| Low kappa (< 0.6) | Judge can't be trusted | Switch model, check prompt, increase oracle budget |
| Kappa dropping | Judge drifting | Data may have changed, model may have degraded |

### Technical: Model Hierarchy

```
Tier 1 (Judge):     qwen3:14b or similar local model       ~$0/run
Tier 2 (Oracle):    Claude Sonnet via providers.py          ~$0.10-0.50/run
Tier 3 (Arbiter):   Claude Opus (Phase 3, disputed cases)  ~$0.50-2.00/run
```

- Oracle runs every cycle (not just initial calibration)
- Budget setting: `eval.oracle_budget` (max pairs per run, default 20)
- Oracle fraction: `eval.oracle_fraction` (0.0-1.0, default 0.2)
- Provider: `eval.oracle_provider` (claude/openai/ollama)
- Thompson Sampling (from ARIA) allocates budget: uncertain categories get more oracle checks

---

## Autonomy Levels

### Plain Language

You choose how much you trust Forge to act on its own:

**Level 1 — OBSERVER** (training wheels)
> "Show me what you found. I'll decide what to do."
> Forge runs evaluations and produces reports. You read them and manually promote.

**Level 2 — ADVISOR** (cruise control)
> "If you're confident, go ahead and switch to the better prompt."
> Auto-promotes winning variants when all quality gates pass. Only changes its OWN config.

**Level 3 — OPERATOR** (autopilot)
> "Fix problems you find, not just report them."
> Everything in Level 2, PLUS sends structured feedback to the data source.

### What Each Level Shows / What You Decide

| Level | You See | You Decide |
|-------|---------|-----------|
| Observer | Reports, metrics, disagreements | Everything |
| Advisor | + promotion notifications | Whether to override, when to investigate |
| Operator | + feedback sent log | Which feedback types to allow |

### Technical: Gate Conditions (Level 2+)

All must pass:

1. **F1 gate:** Winner F1 >= `eval.f1_threshold` (default 0.7)
2. **Improvement gate:** Winner F1 > production F1 + `eval.auto_promote_min_improvement` (0.05)
3. **Oracle gate (NEW):** Kappa >= `eval.oracle_min_kappa` (default 0.6)
4. **Stability gate:** If `eval.stability_window > 0`, winner must pass in last N runs

Gate 3 prevents the current failure mode where a broken judge auto-promotes garbage.

---

## UI/UX Design

### Design Principles

All UI follows the queue dashboard's established patterns:

- **Fire-and-forget confidence** — user configures Forge, trusts it to run
- **Progressive disclosure** — L1 glanceable, L2 investigable, L3 debuggable
- **Terminal voice** — piOS ALL CAPS, mood-driven atmosphere
- **"What it shows / What you decide"** on every element
- **15-year-old first** — plain language before technical

### Sub-view Structure (Eval Tab)

| Sub-view | Phase | Purpose | Decision It Drives |
|----------|-------|---------|-------------------|
| Runs | 1 | Run list + progress + oracle badge | "What's happening now?" |
| Calibration | 1 | Kappa gauge, per-category, disagreements | "Can I trust the judge?" |
| Archive | 2 | MAP-Elites grid, QD-score, coverage | "What strategies exist?" |
| Variants | 1 | Prompt variant CRUD + lineage | "What prompt variants exist?" |
| Trends | 1 | F1 chart + Goodhart composite | "Is the system improving?" |
| Settings | 1 | Autonomy, oracle, evolution params | "How is Forge configured?" |

### Atmosphere Integration

| Forge State | Mood | Effect |
|------------|------|--------|
| Eval running, judge healthy | dawn | ShPipeline progress |
| Oracle agreement dropping | nostalgic | ShGlitch on kappa crossing |
| Judge acquiescing | dread | ShThreatPulse on Calibration |
| New archive niche discovered | dawn | ShShatter (earned, 7 fragments) |
| Auto-promote succeeded | dawn | ShShatter (complete, 6 fragments) |
| Forge stuck (F1=0.0 3+ runs) | dread | Escalation >= 2, mantra overlay |

### Now Page Integration (HostCard)

```
+-- HostCard (RTX 5080) -------------------------+
|  FORGE CYCLE #14                               |
|  ==================  JUDGING (12/20)           |
|  Oracle: k=0.73 ^  |  Archive: 34/100 cells   |
|  VRAM ========-- 78%  CPU ===------- 34%       |
+------------------------------------------------+
```

### Calibration Sub-view (Phase 1)

```
+-- CALIBRATION ---------------------------------+
|                                                |
|         k = 0.73                               |
|     SUBSTANTIAL AGREEMENT                       |
|     _.-'``'-._  (last 10 runs sparkline)       |
|                                                |
|  +- PER-CATEGORY ----------------------------+ |
|  |  async-patterns    k=0.89  ========- 22pr | |
|  |  error-handling    k=0.71  ======--- 18pr | |
|  |  schema-migration  k=0.34  ===------ 8pr  | |  <- ShThreatPulse
|  |  testing           k=0.81  =======-- 15pr | |
|  +-------------------------------------------+ |
|                                                |
|  +- DISAGREEMENTS (worst 5) -----------------+ |
|  |  "Always log before returning fallback"    | |
|  |  Judge: 4/5  Oracle: 1/5  delta=3         | |
|  |  > SHOW REASONING                         | |
|  +-------------------------------------------+ |
+------------------------------------------------+
```

### Archive Sub-view (Phase 2)

```
+-- ARCHIVE ------------------------------------+
|  QD-SCORE: 847  |  COVERAGE: 34/100 (34%)     |
|                                                |
|  SPECIFICITY ->                                |
|  +--+--+--+--+--+--+--+--+--+--+             |
|  |  |  |##|  |  |  |  |  |  |  | broad       |
|  |  |##|XX|##|  |  |  |  |  |  |             |
|  |  |##|XX|XX|##|  |  |  |  |  |             |
|  |  |  |##|XX|XX|##|  |  |  |  |             |
|  |  |  |  |##|XX|XX|##|  |  |  | narrow      |
|  +--+--+--+--+--+--+--+--+--+--+             |
|  ^ domain coverage ->                          |
|                                                |
|  . empty  # occupied  X elite (top quartile)   |
|                                                |
|  SELECTED: specificity=high, coverage=med      |
|  Variant: X07-contrastive  F1: 0.82            |
|  > VIEW PROMPT  > COMPARE WITH PRODUCTION      |
+------------------------------------------------+
```

### Autonomy Settings

```
+-- AUTONOMY -----------------------------------+
|                                                |
|  lessons-db (http://127.0.0.1:7685)           |
|                                                |
|  ( ) OBSERVER    Reports only                  |
|  (*) ADVISOR     Auto-promote when gates pass  |
|  ( ) OPERATOR    Auto-promote + feedback       |
|                  ! requires on_feedback         |
|                                                |
|  Oracle: claude-sonnet-4-20250514              |
|  Budget: 20 pairs/cycle                        |
|  Schedule: every 6h                            |
+------------------------------------------------+
```

---

## Success Metrics

### Plain Language

Five things to watch, most important to least:

1. **Is the judge trustworthy?** (kappa >= 0.6) — if no, nothing else matters
2. **Are the scores meaningful?** (F1 > 0, variance > 0) — if all the same, system is broken
3. **Is the best strategy improving?** (F1 trending up) — the whole point
4. **Finding new strategies?** (archive coverage growing) — prevents getting stuck
5. **Is downstream data improving?** (source quality metric) — the real goal

### What They Show / What You Decide

| Metric | Healthy | Sick | Action When Sick |
|--------|---------|------|-----------------|
| Kappa | >= 0.6 | < 0.6 | Switch judge model, check prompt, increase oracle budget |
| Score variance | sigma > 0.5 | sigma ~ 0 | Judge acquiescing — change model or temperature |
| F1 trend | Positive over 5+ runs | Flat/negative | Check data source, try different variants |
| Archive coverage | Growing or stable >30% | Stuck <10% | Increase mutation rate, add templates |
| Source quality | Improving on source's metric | Flat | Check Level 3 feedback, investigate upstream |

### Technical: Goodhart Protection

Composite monitoring score is NEVER the optimization target. Display-only in Trends.

```python
# For human observation only — NOT for optimizer
monitoring_composite = weighted_mean([
    (kappa, 0.3),
    (calibrated_f1, 0.3),
    (archive_coverage, 0.2),
    (score_variance, 0.2),
])
```

Optimizer sees only calibrated F1 on held-out validation set.

Train/validation/test split:
- Train (60%): Variants compete here
- Validation (20%): Gates check here
- Test (20%): Held out — manual check only ("CHECK HOLDOUT" button in Trends)

---

## ForgeDataSource Protocol

### Plain Language

Forge needs the data source to answer just two required questions:

1. "What items do you have?" — list of things to evaluate (REQUIRED)
2. "What do they look like as vectors?" — embeddings for pair selection (REQUIRED — but Forge can compute these itself)

And three optional questions that make results richer:

3. "How are they grouped?" — group labels for per-category reporting in Calibration UI (OPTIONAL)
4. "What does 'good' look like?" — behavior descriptors for MAP-Elites archive (OPTIONAL, Phase 2)
5. "Here's what I found" — accept feedback report (OPTIONAL, Level 3 only)

A data source with nothing but item text still works — Forge embeds items itself and uses the oracle as ground truth. Groups are a nice-to-have that improve the Calibration UI's per-category breakdown, nothing more.

### Technical: Protocol Definition

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ForgeDataSource(Protocol):
    """In-process data source for Forge evaluation."""

    # --- REQUIRED ---

    def get_items(self, *, limit: int = 100) -> list[dict]:
        """Return items with id, title, one_liner, description, tags."""
        ...

    # --- OPTIONAL (Forge computes if not provided) ---

    def get_embeddings(self) -> dict[str, list[float]]:
        """Return pre-computed embeddings keyed by item id.
        If not implemented, Forge embeds via nomic-embed-text.
        """
        ...

    def get_groups(self) -> list[dict]:
        """Return group definitions with id, label, item_ids.
        Used for per-category reporting in Calibration UI.
        NOT used for pair selection or metric computation.
        """
        ...

    # --- OPTIONAL (Phase 2+) ---

    def get_behavior_descriptors(self) -> dict:
        """Return axis definitions for MAP-Elites archive.
        Example: {"x": {"name": "specificity", "range": [0, 10]},
                  "y": {"name": "domain_coverage", "range": [0, 10]}}
        If not implemented, Forge uses generic descriptors
        (output_length x vocabulary_diversity).
        """
        ...

    # --- OPTIONAL (Phase 3, Level 3 only) ---

    def on_feedback(self, results: list[dict]) -> None:
        """Receive structured feedback from Forge.
        Each result: {item_id, score, diagnosis, recommendation}
        Data source decides what to do with it.
        """
        ...
```

### HTTP Contract (remote sources like lessons-db)

| Endpoint | Required? | Purpose |
|----------|-----------|---------|
| `GET /eval/items?limit=100` | YES | Item list with text fields |
| `GET /eval/embeddings` | No (Forge computes) | Pre-computed vectors keyed by id |
| `GET /eval/groups` | No | Group labels for reporting |
| `GET /eval/behavior-descriptors` | No (Phase 2) | MAP-Elites axis definitions |
| `POST /eval/feedback` | No (Phase 3) | Structured feedback intake |

Optional endpoints return 404 = Forge uses defaults. Existing lessons-db already serves items. Only `embeddings` and `groups` (renamed from clusters) need minor adaptation.

---

## ARIA Patterns Transferred

| ARIA Pattern | Source | Forge Application |
|-------------|--------|-------------------|
| Predict-Compare-Score loop | shadow_engine.py:383-986 | Core cycle: generate -> judge -> oracle -> feedback |
| Thompson Sampling | shadow_engine.py:64-201 | Adaptive oracle budget allocation |
| 3-class outcomes | shadow_engine.py:1095 | correct / disagreement / nothing |
| Drift detection | intelligence.py:27-80 | Judge-oracle agreement trend monitoring |
| Mean-of-halves trend | scoring.py:78-91 | Robust F1 trend (recent 3 vs earlier 3) |
| Per-context hit rates | shadow_engine.py:1111-1136 | Per-category judge accuracy |
| Temporal CV | evaluation.py:10-38 | Expanding-window train/test splits |

---

## Phase Plan

### Phase 1 — Calibrate (~18-22 tasks)

**Plain language:** Make the judge trustworthy. Add the professor (oracle) to every run. Build the Calibration page so you can see trust levels.

**Delivers:**

- Embedding-based pair selection (nomic-embed-text, stratified quartiles)
- Oracle runs every cycle with configurable budget + provider
- Oracle-as-ground-truth F1 (replaces cluster-based F1)
- Spearman diagnostic for acquiescence detection
- Calibration sub-view (kappa, per-group when available, disagreements)
- Oracle gate added to auto-promote
- Fix inverted precision metric in current eval
- Fix APO learnings dedup (no more 24 identical entries)
- Autonomy radio (Observer/Advisor) in Settings
- HostCard shows oracle kappa inline
- Train/validation split (test holdout in Phase 2)
- `eval_embeddings` table for caching item vectors

### Phase 2 — Evolve (~20-25 tasks)

**Plain language:** Instead of trying one rubric at a time, try many and keep the best from each category. Like a talent show with different categories instead of one winner.

**Delivers:**

- MAP-Elites archive with configurable behavior descriptors
- Default descriptors: output_length x vocabulary_diversity (no data source config needed)
- Archive sub-view (grid, QD-score, coverage)
- Tournament selection + crossover + mutation for new variants
- Thompson Sampling for oracle budget allocation (from ARIA)
- Desirable difficulty: interleaved groups, spaced eval
- Goodhart monitoring composite in Trends (display-only)
- Full train/validation/test split with CHECK HOLDOUT button

### Phase 3 — Learn (~12-15 tasks)

**Plain language:** The system doesn't just find better rubrics — it questions whether the rubric is even the right approach. Notices when something fundamental changed.

**Delivers:**

- Double-loop meta-eval ("is F1 the right metric for this data source?")
- Drift detection (from ARIA intelligence.py pattern)
- `on_feedback` protocol for Level 3 (Operator) autonomy
- Held-out test set with manual check + freshness counter
- Arbiter tier (Tier 3 — Claude Opus for disputed cases)
- ForgeDataSource protocol finalized + documented

### Total: ~50-62 tasks across 3 phases

Each phase is independently deployable. Phase 1 alone fixes the immediate crisis (F1=0.0).

---

## Resolved Design Decisions

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Grouping methodology | Embedding-stratified sampling, oracle as ground truth | Clusters punish general principles, couple to data source labels, add measurement noise. Embeddings work for any data source. Oracle IS the ground truth. |
| 2 | Cluster role | Optional reporting metadata | Groups enrich Calibration UI per-category breakdown but do not affect pair selection or metric computation |
| 3 | Oracle type | Stronger LLM (Claude/GPT-4), not human | Runs every cycle, no bottleneck, ~$0.10-0.50/run |
| 4 | Autonomy model | 3-tier (Observer/Advisor/Operator) with UI radio | User controls risk, progressive trust building |
| 5 | Data source abstraction | Hybrid Protocol (in-process) + HTTP (remote) | Lessons-db is remote, future sources may be in-process |
| 6 | Name | Forge | Evokes "forging" better tools through heat and pressure |

## Open Questions

1. **Behavior descriptor defaults** — what generic axes work when a data source doesn't provide custom descriptors? Current proposal: output_length x vocabulary_diversity. Needs validation in Phase 2.
2. **Oracle cost ceiling** — at 20% fraction with Claude Sonnet, ~16 pairs/run at ~$0.003/pair = ~$0.05/run. At 4 runs/day = ~$6/month. Acceptable?
3. **Embedding refresh strategy** — when items change, re-embed all or incremental? Incremental is cheaper but may miss similarity shifts from bulk updates.
