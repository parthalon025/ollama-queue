# Forge v2 Phase 2 (Evolve) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add MAP-Elites quality-diversity search, Thompson Sampling oracle allocation, train/validation/test splits, and Goodhart monitoring to the Forge engine. Phase 2 enables Forge to autonomously discover diverse prompt strategies rather than evaluating a fixed set.

**Architecture:** Extends the Phase 1 `ollama_queue/forge/` package with 6 new modules. Reuses Phase 1's engine, oracle, metrics, and DB patterns. MAP-Elites archive stored in `forge_archive` table. Thompson state in `forge_thompson_state`. Engine gains an `evolve` step after `calibrate`. All new modules are pure functions testable without DB or HTTP.

**Tech Stack:** Python 3.12, SQLite (WAL), pytest. No new dependencies — all algorithms are pure Python.

**Design doc:** `docs/plans/2026-03-17-forge-v2-design.md`

**Depends on:** Phase 1 complete (types, settings, embedder, pairs, judge, oracle, calibrator, metrics, engine, DB, API all exist).

**Reference code:**
- `ha-aria/aria/modules/shadow_engine.py:64-201` — Thompson Sampling (alpha/beta, discount, window)
- `ha-aria/aria/modules/shadow_engine.py:923-986` — Outcome scoring (correct/disagreement/nothing)
- `ha-aria/aria/engine/predictions/scoring.py:78-91` — Mean-of-halves trend
- `ollama_queue/eval/promote.py` — Auto-promote gate structure
- `ollama_queue/eval/analysis.py` — Bootstrap CI, stability

---

## Code Conventions

These conventions are **mandatory** for every task in this plan. The executing agent MUST follow them.

### Module Design (GitHub Best Practices)

- **One primary responsibility per module.** Each `.py` file does one job. If a module grows past 150 lines, split it.
- **Functions ≤ 30 lines.** If a function exceeds 30 lines, extract a helper. Name the helper after what it computes, not what step it is.
- **Pure functions by default.** No DB or HTTP imports in algorithm modules (splits, descriptors, archive, thompson, evolver, goodhart). All I/O happens in engine or DB layers.
- **Engine extensions go in `engine_evolve.py`, NOT in `engine.py`.** `engine.py` already exceeds ruff complexity limits (C901: 16>10, PLR0915: 74>50). Phase 2 orchestration lives in `engine_evolve.py`. `engine.py` gains one new import and one function call — nothing more.

### Timestamp Convention

- **Always use `time.time()`** (float epoch) for `created_at`, `updated_at`, and all temporal columns. This matches Phase 1's convention in `db/forge.py`. Do NOT use `datetime.now(UTC).isoformat()`.

### Git Discipline

- **One logical change per commit.** Each Step 5 ("Commit") is one commit with one purpose.
- **Stage only your files.** `git add <file1> <file2>`, never `git add -A`. Other agents may have unstaged work in the repo.
- **Run tests before every commit.** The test command in Step 4 must pass before Step 5 runs.
- **Commit messages follow Conventional Commits.** `feat(forge):`, `test(forge):`, `fix(forge):`.

### Ruff Compliance

- Run `ruff check --fix` after writing any Python file. Fix all errors before committing.
- Per-function complexity: ≤ 10 branches, ≤ 6 return statements, ≤ 50 statements.
- Line length ≤ 120 characters.

---

## Module Map

```
ollama_queue/forge/           # Phase 2 additions
  splits.py                   # Hash-based train/validation/test splitting (~50 lines)
  descriptors.py              # Behavior descriptor computation (~70 lines)
  archive.py                  # MAP-Elites archive operations (~100 lines)
  thompson.py                 # Thompson Sampling oracle budget allocation (~120 lines)
  evolver.py                  # Tournament selection + crossover + mutation (~130 lines)
  goodhart.py                 # Composite monitoring score (display-only) (~80 lines)
  engine_evolve.py            # Phase 2 engine orchestration — splits, archive, Thompson, evolution (~100 lines)

ollama_queue/db/
  forge.py                    # Extend ForgeMixin: archive + thompson tables (~+100 lines)

ollama_queue/api/
  forge_archive.py            # Archive grid + cell endpoints (~80 lines)

tests/
  test_forge_splits.py
  test_forge_descriptors.py
  test_forge_archive.py
  test_forge_thompson.py
  test_forge_evolver.py
  test_forge_goodhart.py
  test_forge_db_phase2.py
  test_api_forge_archive.py
  test_forge_engine_evolve.py # All Phase 2 engine integration tests in one file
```

**Separation of concerns:** Algorithm modules (splits, descriptors, archive, thompson, evolver, goodhart) are pure computation — no DB, no HTTP, no imports from `engine.py`. Engine orchestration lives in `engine_evolve.py`, which coordinates the algorithm modules with DB and settings. `engine.py` gains only a one-line call to `engine_evolve.run_evolve_phase()`.

---

## Batch 1: Train/Validation/Test Splits

**PRD:** Deterministic item splitting using hash-based assignment. Items always land in the same split regardless of ordering or new additions. Train (60%) for variant competition, validation (20%) for gate checks, test (20%) held out for manual inspection only.

### Task 1: Hash-based split assignment

**Files:**

- Create: `ollama_queue/forge/splits.py`
- Test: `tests/test_forge_splits.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_splits.py
"""Tests for Forge train/validation/test splitting."""
from ollama_queue.forge.splits import assign_split, split_items, TRAIN, VALIDATION, TEST


def test_assign_split_deterministic():
    """Same item + seed always gets same split."""
    a = assign_split("item-42", seed=0)
    b = assign_split("item-42", seed=0)
    assert a == b


def test_assign_split_different_seeds():
    """Different seeds may produce different assignments."""
    results = {assign_split("item-1", seed=s) for s in range(20)}
    # With 20 different seeds, very likely to see at least 2 different splits
    assert len(results) >= 2


def test_assign_split_returns_valid_split():
    for i in range(100):
        split = assign_split(f"item-{i}", seed=42)
        assert split in (TRAIN, VALIDATION, TEST)


def test_split_items_approximate_ratios():
    items = [{"id": str(i)} for i in range(1000)]
    splits = split_items(items, seed=42)
    # Allow ±5% tolerance on 60/20/20
    assert 550 <= len(splits[TRAIN]) <= 650
    assert 150 <= len(splits[VALIDATION]) <= 250
    assert 150 <= len(splits[TEST]) <= 250


def test_split_items_no_overlap():
    items = [{"id": str(i)} for i in range(100)]
    splits = split_items(items, seed=42)
    all_ids = set()
    for split_items_list in splits.values():
        ids = {item["id"] for item in split_items_list}
        assert ids.isdisjoint(all_ids), "Items appear in multiple splits"
        all_ids.update(ids)


def test_split_items_stable_on_addition():
    """Adding items doesn't reshuffle existing assignments."""
    items_small = [{"id": str(i)} for i in range(50)]
    items_large = [{"id": str(i)} for i in range(100)]

    splits_small = split_items(items_small, seed=42)
    splits_large = split_items(items_large, seed=42)

    # Every item from small set should be in the same split in large set
    for split_name in (TRAIN, VALIDATION, TEST):
        small_ids = {item["id"] for item in splits_small[split_name]}
        large_ids = {item["id"] for item in splits_large[split_name]}
        assert small_ids.issubset(large_ids)


def test_split_items_custom_ratios():
    items = [{"id": str(i)} for i in range(1000)]
    splits = split_items(items, seed=42, train_frac=0.8, val_frac=0.1)
    assert len(splits[TRAIN]) > len(splits[VALIDATION])
    assert len(splits[TRAIN]) > len(splits[TEST])
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_splits.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/splits.py
"""Forge splits — deterministic train/validation/test assignment.

Items are assigned to splits using SHA-256 hash of (seed, item_id),
ensuring stable assignment: adding items doesn't reshuffle existing ones.
Default ratio: 60% train / 20% validation / 20% test.
"""
from __future__ import annotations

import hashlib

TRAIN = "train"
VALIDATION = "validation"
TEST = "test"


def assign_split(
    item_id: str,
    *,
    seed: int = 0,
    train_frac: float = 0.6,
    val_frac: float = 0.2,
) -> str:
    """Deterministic split assignment for one item.

    Uses SHA-256 hash mapped to [0, 1) for uniform distribution.
    """
    h = hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()
    bucket = int(h[:8], 16) / 0xFFFFFFFF
    if bucket < train_frac:
        return TRAIN
    if bucket < train_frac + val_frac:
        return VALIDATION
    return TEST


def split_items(
    items: list[dict],
    *,
    seed: int = 0,
    train_frac: float = 0.6,
    val_frac: float = 0.2,
) -> dict[str, list[dict]]:
    """Partition items into train/validation/test splits."""
    result: dict[str, list[dict]] = {TRAIN: [], VALIDATION: [], TEST: []}
    for item in items:
        split = assign_split(item["id"], seed=seed, train_frac=train_frac, val_frac=val_frac)
        result[split].append(item)
    return result
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_splits.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/splits.py tests/test_forge_splits.py
git commit -m "feat(forge): add hash-based train/validation/test splits"
```

---

## Batch 2: Behavior Descriptors

**PRD:** Compute quantitative behavior properties of judge outputs to place variants in the MAP-Elites grid. Default axes: output_length (normalized response verbosity) and vocabulary_diversity (unique word ratio). Data sources can provide custom axes via `get_behavior_descriptors()`.

### Task 2: Default descriptor computation

**Files:**

- Create: `ollama_queue/forge/descriptors.py`
- Test: `tests/test_forge_descriptors.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_descriptors.py
"""Tests for Forge behavior descriptors."""
from ollama_queue.forge.descriptors import (
    compute_output_length,
    compute_vocabulary_diversity,
    compute_default_descriptors,
    normalize_to_bin,
    get_descriptor_axes,
    DEFAULT_GRID_SIZE,
)


def test_compute_output_length_empty():
    assert compute_output_length([]) == 0.0


def test_compute_output_length_normalized():
    texts = ["hello world", "this is a test sentence with more words"]
    val = compute_output_length(texts, max_length=500)
    assert 0.0 < val < 1.0


def test_compute_output_length_capped_at_one():
    texts = ["x" * 1000]
    assert compute_output_length(texts, max_length=100) == 1.0


def test_vocabulary_diversity_empty():
    assert compute_vocabulary_diversity([]) == 0.0


def test_vocabulary_diversity_all_unique():
    val = compute_vocabulary_diversity(["alpha bravo charlie delta echo"])
    assert val == 1.0


def test_vocabulary_diversity_all_same():
    val = compute_vocabulary_diversity(["the the the the the"])
    assert val == 0.2  # 1 unique / 5 total


def test_compute_default_descriptors():
    results = [
        {"judge_reasoning": "This principle clearly applies because the error handling pattern matches."},
        {"judge_reasoning": "Partial match — the lesson addresses a different concern but shares the approach."},
    ]
    desc = compute_default_descriptors(results)
    assert "output_length" in desc
    assert "vocabulary_diversity" in desc
    assert 0.0 <= desc["output_length"] <= 1.0
    assert 0.0 <= desc["vocabulary_diversity"] <= 1.0


def test_normalize_to_bin_edges():
    assert normalize_to_bin(0.0, 0.0, 1.0, 10) == 0
    assert normalize_to_bin(1.0, 0.0, 1.0, 10) == 9  # clamped
    assert normalize_to_bin(0.5, 0.0, 1.0, 10) == 5


def test_normalize_to_bin_out_of_range():
    assert normalize_to_bin(-0.5, 0.0, 1.0, 10) == 0
    assert normalize_to_bin(1.5, 0.0, 1.0, 10) == 9


def test_get_descriptor_axes_default():
    axes = get_descriptor_axes(data_source=None)
    assert "x" in axes and "y" in axes
    assert axes["x"]["name"] == "output_length"
    assert axes["y"]["name"] == "vocabulary_diversity"


class _CustomSource:
    def get_behavior_descriptors(self):
        return {
            "x": {"name": "specificity", "range": [0, 10]},
            "y": {"name": "domain_coverage", "range": [0, 10]},
        }


def test_get_descriptor_axes_custom():
    axes = get_descriptor_axes(data_source=_CustomSource())
    assert axes["x"]["name"] == "specificity"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_descriptors.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/descriptors.py
"""Forge behavior descriptors — quantify behavioral properties of judge outputs.

Default axes: output_length (normalized response verbosity) and
vocabulary_diversity (unique word ratio). Data sources can provide
custom axes via get_behavior_descriptors().
"""
from __future__ import annotations

DEFAULT_GRID_SIZE = 10

_DEFAULT_AXES = {
    "x": {"name": "output_length", "range": [0.0, 1.0], "description": "Normalized response length"},
    "y": {"name": "vocabulary_diversity", "range": [0.0, 1.0], "description": "Unique word ratio"},
}


def get_descriptor_axes(data_source=None) -> dict:
    """Get behavior descriptor axis definitions.

    Tries data_source.get_behavior_descriptors() first, falls back to defaults.
    """
    if data_source is not None:
        try:
            axes = data_source.get_behavior_descriptors()
            if axes and "x" in axes and "y" in axes:
                return axes
        except (AttributeError, NotImplementedError):
            pass
    return _DEFAULT_AXES


def compute_output_length(texts: list[str], *, max_length: int = 500) -> float:
    """Normalized average output length. Returns 0.0 to 1.0."""
    if not texts:
        return 0.0
    avg_len = sum(len(t) for t in texts) / len(texts)
    return min(1.0, avg_len / max_length)


def compute_vocabulary_diversity(texts: list[str]) -> float:
    """Unique word ratio across all texts. Returns 0.0 to 1.0."""
    if not texts:
        return 0.0
    words = []
    for t in texts:
        words.extend(t.lower().split())
    if not words:
        return 0.0
    return len(set(words)) / len(words)


def compute_default_descriptors(results: list[dict]) -> dict[str, float]:
    """Compute default behavior descriptors from judge results.

    Returns {"output_length": float, "vocabulary_diversity": float}.
    """
    texts = [r.get("judge_reasoning") or r.get("reasoning") or "" for r in results]
    texts = [t for t in texts if t]
    return {
        "output_length": compute_output_length(texts),
        "vocabulary_diversity": compute_vocabulary_diversity(texts),
    }


def normalize_to_bin(value: float, range_min: float, range_max: float, grid_size: int) -> int:
    """Map a continuous value to a discrete bin index (0 to grid_size-1)."""
    if range_max <= range_min:
        return 0
    normalized = (value - range_min) / (range_max - range_min)
    normalized = max(0.0, min(1.0, normalized))
    bin_idx = int(normalized * grid_size)
    return min(bin_idx, grid_size - 1)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_descriptors.py -v`
Expected: PASS (11 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/descriptors.py tests/test_forge_descriptors.py
git commit -m "feat(forge): add behavior descriptor computation for MAP-Elites"
```

---

## Batch 3: MAP-Elites Archive

**PRD:** The archive is a 2D grid where each cell holds the best-performing variant for that behavioral niche. Cells are indexed by behavior descriptors (x_bin, y_bin). Coverage (% cells occupied) and QD-score (sum of fitness) track exploration. Archive operations are pure functions operating on dicts — DB persistence is a separate task.

### Task 3: Archive data structures and operations

**Files:**

- Create: `ollama_queue/forge/archive.py`
- Test: `tests/test_forge_archive.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_archive.py
"""Tests for Forge MAP-Elites archive."""
from ollama_queue.forge.archive import (
    ArchiveCell,
    try_insert,
    compute_qd_score,
    compute_coverage,
    get_elites,
    grid_to_heatmap,
)


def _cell(x, y, fitness, variant="v1"):
    return ArchiveCell(x_bin=x, y_bin=y, x_value=float(x), y_value=float(y),
                       variant_id=variant, fitness=fitness)


def test_try_insert_empty_cell():
    grid = {}
    assert try_insert(grid, _cell(0, 0, 0.8)) is True
    assert (0, 0) in grid


def test_try_insert_higher_fitness_replaces():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5, "old"))
    assert try_insert(grid, _cell(0, 0, 0.8, "new")) is True
    assert grid[(0, 0)].variant_id == "new"


def test_try_insert_lower_fitness_rejected():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.8, "old"))
    assert try_insert(grid, _cell(0, 0, 0.5, "new")) is False
    assert grid[(0, 0)].variant_id == "old"


def test_compute_qd_score():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5))
    try_insert(grid, _cell(1, 0, 0.7))
    try_insert(grid, _cell(0, 1, 0.3))
    assert compute_qd_score(grid) == 1.5


def test_compute_qd_score_empty():
    assert compute_qd_score({}) == 0.0


def test_compute_coverage():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5))
    try_insert(grid, _cell(1, 1, 0.5))
    assert compute_coverage(grid, grid_size=10) == 2 / 100


def test_compute_coverage_full():
    grid = {}
    for x in range(3):
        for y in range(3):
            try_insert(grid, _cell(x, y, 0.5))
    assert compute_coverage(grid, grid_size=3) == 1.0


def test_get_elites():
    grid = {}
    for i in range(5):
        try_insert(grid, _cell(i, 0, i * 0.1 + 0.1, f"v{i}"))
    top = get_elites(grid, top_n=3)
    assert len(top) == 3
    assert top[0].fitness > top[1].fitness > top[2].fitness


def test_grid_to_heatmap():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5))
    try_insert(grid, _cell(2, 1, 0.8))
    hm = grid_to_heatmap(grid, grid_size=3)
    assert hm[0][0] == 0.5
    assert hm[1][2] == 0.8
    assert hm[0][1] is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_archive.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/archive.py
"""Forge MAP-Elites archive — quality-diversity prompt strategy storage.

A 2D grid indexed by behavior descriptors. Each cell holds the best-performing
variant for that behavioral niche. Coverage and QD-score track exploration.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArchiveCell:
    """One cell in the MAP-Elites grid."""
    x_bin: int
    y_bin: int
    x_value: float
    y_value: float
    variant_id: str
    fitness: float
    prompt_text: str | None = None
    metadata: dict | None = None


def try_insert(grid: dict[tuple[int, int], ArchiveCell], cell: ArchiveCell) -> bool:
    """Insert cell if empty or new fitness exceeds existing.

    Returns True if inserted/replaced, False if existing is better.
    """
    key = (cell.x_bin, cell.y_bin)
    existing = grid.get(key)
    if existing is None or cell.fitness > existing.fitness:
        grid[key] = cell
        return True
    return False


def compute_qd_score(grid: dict[tuple[int, int], ArchiveCell]) -> float:
    """Sum of fitness across all occupied cells."""
    return sum(cell.fitness for cell in grid.values())


def compute_coverage(grid: dict[tuple[int, int], ArchiveCell], grid_size: int) -> float:
    """Fraction of grid cells that are occupied. 0.0 to 1.0."""
    total = grid_size * grid_size
    if total == 0:
        return 0.0
    return len(grid) / total


def get_elites(grid: dict[tuple[int, int], ArchiveCell], *, top_n: int = 10) -> list[ArchiveCell]:
    """Return the top N cells by fitness, descending."""
    cells = sorted(grid.values(), key=lambda c: c.fitness, reverse=True)
    return cells[:top_n]


def grid_to_heatmap(grid: dict[tuple[int, int], ArchiveCell], grid_size: int) -> list[list[float | None]]:
    """Convert archive to a 2D fitness heatmap. None = empty cell."""
    heatmap: list[list[float | None]] = [[None] * grid_size for _ in range(grid_size)]
    for (x, y), cell in grid.items():
        if 0 <= x < grid_size and 0 <= y < grid_size:
            heatmap[y][x] = cell.fitness
    return heatmap
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_archive.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/archive.py tests/test_forge_archive.py
git commit -m "feat(forge): add MAP-Elites archive — grid, insert, QD-score, coverage"
```

---

### Task 4: Archive DB persistence

**Files:**

- Modify: `ollama_queue/db/forge.py` (add archive + thompson tables + CRUD)
- Modify: `ollama_queue/db/schema.py` (add CREATE TABLE statements)
- Test: `tests/test_forge_db_phase2.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_db_phase2.py
"""Tests for Forge Phase 2 DB operations — archive + thompson state."""


def test_upsert_archive_cell_insert(db):
    db.upsert_forge_archive_cell(
        x_bin=0, y_bin=0, x_value=0.1, y_value=0.2,
        variant_id="A", fitness=0.75, prompt_text="test prompt",
        run_id=None,
    )
    cell = db.get_forge_archive_cell(0, 0)
    assert cell is not None
    assert cell["variant_id"] == "A"
    assert cell["fitness"] == 0.75


def test_upsert_archive_cell_replaces_lower_fitness(db):
    db.upsert_forge_archive_cell(x_bin=1, y_bin=1, x_value=0.5, y_value=0.5,
                                 variant_id="A", fitness=0.5)
    db.upsert_forge_archive_cell(x_bin=1, y_bin=1, x_value=0.5, y_value=0.5,
                                 variant_id="B", fitness=0.8)
    cell = db.get_forge_archive_cell(1, 1)
    assert cell["variant_id"] == "B"


def test_upsert_archive_cell_keeps_higher_fitness(db):
    db.upsert_forge_archive_cell(x_bin=2, y_bin=2, x_value=0.5, y_value=0.5,
                                 variant_id="A", fitness=0.9)
    db.upsert_forge_archive_cell(x_bin=2, y_bin=2, x_value=0.5, y_value=0.5,
                                 variant_id="B", fitness=0.3)
    cell = db.get_forge_archive_cell(2, 2)
    assert cell["variant_id"] == "A"


def test_get_forge_archive_grid(db):
    for i in range(3):
        db.upsert_forge_archive_cell(x_bin=i, y_bin=0, x_value=float(i), y_value=0.0,
                                     variant_id=f"v{i}", fitness=i * 0.1)
    grid = db.get_forge_archive_grid()
    assert len(grid) == 3


def test_clear_forge_archive(db):
    db.upsert_forge_archive_cell(x_bin=0, y_bin=0, x_value=0.0, y_value=0.0,
                                 variant_id="A", fitness=0.5)
    db.clear_forge_archive()
    assert db.get_forge_archive_grid() == []


def test_save_and_load_thompson_state(db):
    state = {"q1_likely": {"alpha": 3.0, "beta": 1.5, "observations": 10}}
    db.save_forge_thompson_state(state)
    loaded = db.load_forge_thompson_state()
    assert loaded == state


def test_load_thompson_state_empty(db):
    assert db.load_forge_thompson_state() is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_db_phase2.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add to `ollama_queue/db/schema.py` in the `_create_tables` method:

```python
# Forge Phase 2: MAP-Elites archive
conn.execute("""CREATE TABLE IF NOT EXISTS forge_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    x_bin INTEGER NOT NULL,
    y_bin INTEGER NOT NULL,
    x_value REAL NOT NULL,
    y_value REAL NOT NULL,
    variant_id TEXT NOT NULL,
    fitness REAL NOT NULL,
    prompt_text TEXT,
    metadata_json TEXT,
    run_id INTEGER REFERENCES forge_runs(id),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(x_bin, y_bin)
)""")

# Forge Phase 2: Thompson Sampling state
conn.execute("""CREATE TABLE IF NOT EXISTS forge_thompson_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state_json TEXT NOT NULL,
    updated_at REAL NOT NULL
)""")
```

Add to `ollama_queue/db/forge.py`:

```python
def upsert_forge_archive_cell(
    self, *, x_bin: int, y_bin: int, x_value: float, y_value: float,
    variant_id: str, fitness: float, prompt_text: str | None = None,
    metadata_json: str | None = None, run_id: int | None = None,
) -> None:
    """Insert or replace archive cell — only replaces if fitness is higher."""
    now = time.time()
    with self._lock:
        conn = self._connect()
        existing = conn.execute(
            "SELECT fitness FROM forge_archive WHERE x_bin = ? AND y_bin = ?",
            (x_bin, y_bin),
        ).fetchone()
        if existing and existing["fitness"] >= fitness:
            return  # existing is better
        conn.execute(
            """INSERT INTO forge_archive
               (x_bin, y_bin, x_value, y_value, variant_id, fitness,
                prompt_text, metadata_json, run_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(x_bin, y_bin) DO UPDATE SET
                 x_value=excluded.x_value, y_value=excluded.y_value,
                 variant_id=excluded.variant_id, fitness=excluded.fitness,
                 prompt_text=excluded.prompt_text, metadata_json=excluded.metadata_json,
                 run_id=excluded.run_id, updated_at=excluded.updated_at""",
            (x_bin, y_bin, x_value, y_value, variant_id, fitness,
             prompt_text, metadata_json, run_id, now, now),
        )
        conn.commit()

def get_forge_archive_cell(self, x_bin: int, y_bin: int) -> dict | None:
    with self._lock:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM forge_archive WHERE x_bin = ? AND y_bin = ?",
            (x_bin, y_bin),
        ).fetchone()
        return dict(row) if row else None

def get_forge_archive_grid(self) -> list[dict]:
    with self._lock:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM forge_archive ORDER BY x_bin, y_bin").fetchall()
        return [dict(r) for r in rows]

def clear_forge_archive(self) -> None:
    with self._lock:
        conn = self._connect()
        conn.execute("DELETE FROM forge_archive")
        conn.commit()

def save_forge_thompson_state(self, state: dict) -> None:
    import json
    now = time.time()
    state_json = json.dumps(state)
    with self._lock:
        conn = self._connect()
        conn.execute("DELETE FROM forge_thompson_state")
        conn.execute(
            "INSERT INTO forge_thompson_state (state_json, updated_at) VALUES (?, ?)",
            (state_json, now),
        )
        conn.commit()

def load_forge_thompson_state(self) -> dict | None:
    import json
    with self._lock:
        conn = self._connect()
        row = conn.execute(
            "SELECT state_json FROM forge_thompson_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["state_json"]) if row else None
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_db_phase2.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add ollama_queue/db/forge.py ollama_queue/db/schema.py tests/test_forge_db_phase2.py
git commit -m "feat(forge): add archive + thompson DB tables and CRUD"
```

---

## Batch 4: Thompson Sampling

**PRD:** Adaptive oracle budget allocation using Thompson Sampling. Categories (quartiles or groups) with uncertain judge-oracle agreement get more oracle checks. Adapted from ARIA shadow_engine.py — uses Beta distribution posteriors with f-dsw discount and window cap.

### Task 5: Thompson budget allocator

**Files:**

- Create: `ollama_queue/forge/thompson.py`
- Test: `tests/test_forge_thompson.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_thompson.py
"""Tests for Forge Thompson Sampling oracle budget allocation."""
from ollama_queue.forge.thompson import ThompsonBudget


def test_allocate_budget_uniform_initial():
    """With no observations, budget should be roughly uniform."""
    tb = ThompsonBudget()
    alloc = tb.allocate_budget(20, ["q1", "q2", "q3", "q4"], seed=42)
    assert sum(alloc.values()) == 20
    assert all(v >= 1 for v in alloc.values())


def test_allocate_budget_favors_uncertain():
    """Category with worse agreement should get more budget."""
    tb = ThompsonBudget()
    # q1 has high agreement
    for _ in range(20):
        tb.record_agreement("q1", agreed=True)
    # q2 has low agreement
    for _ in range(20):
        tb.record_agreement("q2", agreed=False)
    # q3 and q4 have no observations (maximally uncertain)

    alloc = tb.allocate_budget(40, ["q1", "q2", "q3", "q4"], seed=42)
    assert sum(alloc.values()) == 40
    # q1 (high agreement, low uncertainty) should get less than q2 (low agreement)
    assert alloc["q1"] <= alloc["q2"]


def test_allocate_budget_minimum_one_each():
    tb = ThompsonBudget()
    alloc = tb.allocate_budget(4, ["a", "b", "c", "d"], seed=42)
    assert all(v >= 1 for v in alloc.values())


def test_allocate_budget_insufficient():
    """When budget < categories, some get 0."""
    tb = ThompsonBudget()
    alloc = tb.allocate_budget(2, ["a", "b", "c", "d"], seed=42)
    assert sum(alloc.values()) == 2


def test_record_agreement_updates_state():
    tb = ThompsonBudget()
    tb.record_agreement("q1", agreed=True)
    tb.record_agreement("q1", agreed=False)
    stats = tb.get_stats()
    assert stats["q1"]["observations"] == 2


def test_discount_factor_decays():
    tb = ThompsonBudget(discount=0.9)
    for _ in range(10):
        tb.record_agreement("q1", agreed=True)
    stats = tb.get_stats()
    # Alpha should be less than 1.0 + 10 due to discount
    assert stats["q1"]["alpha"] < 11.0


def test_window_cap():
    tb = ThompsonBudget(window=5)
    for _ in range(20):
        tb.record_agreement("q1", agreed=True)
    stats = tb.get_stats()
    assert stats["q1"]["observations"] <= 5


def test_get_state_roundtrip():
    tb = ThompsonBudget()
    tb.record_agreement("q1", agreed=True)
    tb.record_agreement("q2", agreed=False)
    state = tb.get_state()

    tb2 = ThompsonBudget()
    tb2.load_state(state)
    assert tb2.get_stats() == tb.get_stats()


def test_allocate_budget_deterministic():
    tb = ThompsonBudget()
    a = tb.allocate_budget(20, ["q1", "q2", "q3", "q4"], seed=42)
    b = tb.allocate_budget(20, ["q1", "q2", "q3", "q4"], seed=42)
    assert a == b


def test_allocate_budget_empty_categories():
    tb = ThompsonBudget()
    assert tb.allocate_budget(10, []) == {}
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_thompson.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/thompson.py
"""Forge Thompson Sampling — adaptive oracle budget allocation.

Allocates oracle budget to categories (quartiles or groups) based on
uncertainty. Categories where judge-oracle agreement is uncertain get
more oracle checks. Adapted from ARIA shadow_engine.py ThompsonSampler.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

_DISCOUNT_FACTOR = 0.95
_WINDOW_SIZE = 100


@dataclass
class _Bucket:
    alpha: float = 1.0
    beta: float = 1.0
    observations: int = 0


class ThompsonBudget:
    """Allocates oracle budget across categories using Thompson Sampling.

    Categories with high uncertainty (alpha ~ beta) get more checks.
    """

    def __init__(self, discount: float = _DISCOUNT_FACTOR, window: int = _WINDOW_SIZE):
        self._buckets: dict[str, _Bucket] = {}
        self._discount = discount
        self._window = window

    def _get_bucket(self, category: str) -> _Bucket:
        if category not in self._buckets:
            self._buckets[category] = _Bucket()
        return self._buckets[category]

    def record_agreement(self, category: str, agreed: bool) -> None:
        """Update posterior after observing judge-oracle agreement."""
        b = self._get_bucket(category)
        b.alpha = max(1.0, b.alpha * self._discount)
        b.beta = max(1.0, b.beta * self._discount)
        if agreed:
            b.alpha += 1.0
        else:
            b.beta += 1.0
        b.observations += 1
        if b.observations > self._window:
            scale = self._window / b.observations
            b.alpha = 1.0 + (b.alpha - 1.0) * scale
            b.beta = 1.0 + (b.beta - 1.0) * scale
            b.observations = self._window

    def allocate_budget(
        self, total_budget: int, categories: list[str], *, seed: int | None = None,
    ) -> dict[str, int]:
        """Allocate oracle checks — uncertain categories get more budget."""
        if not categories:
            return {}
        rng = random.Random(seed)

        samples: dict[str, float] = {}
        for cat in categories:
            b = self._get_bucket(cat)
            sample = rng.betavariate(b.alpha, b.beta)
            samples[cat] = 1.0 - sample  # invert: low agreement = high weight

        if total_budget < len(categories):
            allocation: dict[str, int] = {}
            for i, cat in enumerate(categories):
                allocation[cat] = 1 if i < total_budget else 0
            return allocation

        remaining = total_budget - len(categories)
        allocation = {cat: 1 for cat in categories}
        total_weight = sum(samples.values())

        if total_weight > 0 and remaining > 0:
            for cat in categories:
                extra = int(round(remaining * samples[cat] / total_weight))
                allocation[cat] += extra
            diff = total_budget - sum(allocation.values())
            if diff != 0:
                top = max(categories, key=lambda c: samples[c])
                allocation[top] += diff

        return allocation

    def get_state(self) -> dict[str, dict]:
        return {
            cat: {"alpha": b.alpha, "beta": b.beta, "observations": b.observations}
            for cat, b in self._buckets.items()
        }

    def load_state(self, state: dict[str, dict]) -> None:
        self._buckets.clear()
        for cat, vals in state.items():
            self._buckets[cat] = _Bucket(
                alpha=vals.get("alpha", 1.0),
                beta=vals.get("beta", 1.0),
                observations=vals.get("observations", 0),
            )

    def get_stats(self) -> dict[str, dict]:
        result = {}
        for cat, b in self._buckets.items():
            mean = b.alpha / (b.alpha + b.beta)
            result[cat] = {
                "mean": round(mean, 4),
                "alpha": round(b.alpha, 2),
                "beta": round(b.beta, 2),
                "observations": b.observations,
            }
        return result
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_thompson.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/thompson.py tests/test_forge_thompson.py
git commit -m "feat(forge): add Thompson Sampling oracle budget allocation"
```

---

## Batch 5: Evolution Operators

**PRD:** Create new prompt variants by combining and mutating successful strategies from the archive. Tournament selection picks parents, sentence-level crossover blends them, mutation introduces variation. Each offspring becomes a new eval variant in the next Forge cycle.

### Task 6: Tournament selection and crossover

**Files:**

- Create: `ollama_queue/forge/evolver.py`
- Test: `tests/test_forge_evolver.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_evolver.py
"""Tests for Forge evolution operators."""
from ollama_queue.forge.archive import ArchiveCell
from ollama_queue.forge.evolver import (
    tournament_select,
    crossover_prompts,
    mutate_prompt,
    evolve_generation,
)


def _cell(variant, fitness, prompt):
    return ArchiveCell(x_bin=0, y_bin=0, x_value=0.0, y_value=0.0,
                       variant_id=variant, fitness=fitness, prompt_text=prompt)


def test_tournament_select_picks_higher_fitness():
    cells = [_cell("a", 0.3, "low"), _cell("b", 0.9, "high")]
    # With k=2, always picks the better one
    winner = tournament_select(cells, k=2, seed=42)
    assert winner.variant_id == "b"


def test_tournament_select_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        tournament_select([], k=2, seed=42)


def test_crossover_prompts_interleaves():
    a = "Sentence one. Sentence two. Sentence three."
    b = "Alpha fact. Beta fact. Gamma fact."
    child = crossover_prompts(a, b, seed=42)
    assert len(child) > 0
    # Child should contain words from both parents
    words = child.lower().split()
    # At least some mixing should occur
    assert len(words) > 2


def test_crossover_prompts_empty_parent():
    assert crossover_prompts("", "Hello world.", seed=42) == "Hello world."
    assert crossover_prompts("Hello world.", "", seed=42) == "Hello world."


def test_mutate_prompt_preserves_most():
    prompt = "First rule. Second rule. Third rule. Fourth rule. Fifth rule."
    mutated = mutate_prompt(prompt, mutation_rate=0.1, seed=42)
    # Most content should survive
    assert len(mutated) > 0


def test_mutate_prompt_zero_rate():
    prompt = "Exact same text here."
    mutated = mutate_prompt(prompt, mutation_rate=0.0, seed=42)
    assert mutated == prompt


def test_evolve_generation_count():
    cells = [
        _cell("a", 0.5, "Rule one. Rule two."),
        _cell("b", 0.7, "Alpha. Beta. Gamma."),
        _cell("c", 0.9, "First. Second. Third."),
    ]
    offspring = evolve_generation(cells, n_offspring=4, seed=42)
    assert len(offspring) == 4
    assert all(isinstance(p, str) and len(p) > 0 for p in offspring)


def test_evolve_generation_too_few_cells():
    cells = [_cell("a", 0.5, "Only one.")]
    offspring = evolve_generation(cells, n_offspring=4, seed=42)
    assert offspring == []


def test_evolve_generation_deterministic():
    cells = [
        _cell("a", 0.5, "Rule one. Rule two."),
        _cell("b", 0.7, "Alpha. Beta."),
    ]
    a = evolve_generation(cells, n_offspring=3, seed=42)
    b = evolve_generation(cells, n_offspring=3, seed=42)
    assert a == b
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_evolver.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/evolver.py
"""Forge evolver — variant creation through tournament selection + crossover + mutation.

Creates new prompt variants by combining and mutating successful strategies
from the MAP-Elites archive. Each offspring enters the next Forge cycle.
"""
from __future__ import annotations

import random
import re

from ollama_queue.forge.archive import ArchiveCell

_SENTENCE_RE = re.compile(r"(?<=[.!?\n])\s+")


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = _SENTENCE_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def tournament_select(
    cells: list[ArchiveCell], *, k: int = 2, seed: int | None = None,
) -> ArchiveCell:
    """Select a parent via k-tournament. Higher fitness wins."""
    if not cells:
        raise ValueError("Cannot select from empty archive")
    rng = random.Random(seed)
    contestants = rng.sample(cells, min(k, len(cells)))
    return max(contestants, key=lambda c: c.fitness)


def crossover_prompts(
    parent_a: str, parent_b: str, *, seed: int | None = None,
) -> str:
    """Sentence-level crossover between two prompt strings."""
    rng = random.Random(seed)
    sents_a = _split_sentences(parent_a)
    sents_b = _split_sentences(parent_b)

    if not sents_a:
        return parent_b
    if not sents_b:
        return parent_a

    result = []
    max_len = max(len(sents_a), len(sents_b))
    for i in range(max_len):
        if rng.random() < 0.5 and i < len(sents_a):
            result.append(sents_a[i])
        elif i < len(sents_b):
            result.append(sents_b[i])
        elif i < len(sents_a):
            result.append(sents_a[i])

    return " ".join(result)


def mutate_prompt(
    prompt: str, *, mutation_rate: float = 0.15, seed: int | None = None,
) -> str:
    """Apply small random mutations to a prompt string."""
    rng = random.Random(seed)
    sentences = _split_sentences(prompt)
    if not sentences:
        return prompt

    mutated = list(sentences)
    for i in range(len(mutated)):
        if rng.random() < mutation_rate:
            op = rng.choice(["emphasis", "remove", "swap"])
            if op == "emphasis" and mutated[i]:
                mutated[i] = mutated[i].rstrip(".") + " — this is critical."
            elif op == "remove" and len(mutated) > 2:
                mutated[i] = ""
            elif op == "swap" and len(mutated) > 1:
                j = rng.randint(0, len(mutated) - 1)
                mutated[i], mutated[j] = mutated[j], mutated[i]

    return " ".join(s for s in mutated if s).strip()


def evolve_generation(
    cells: list[ArchiveCell], *, n_offspring: int = 4,
    mutation_rate: float = 0.15, seed: int | None = None,
) -> list[str]:
    """Create n_offspring new prompt variants from the archive.

    Returns list of new prompt strings (not yet registered as variants).
    """
    if len(cells) < 2:
        return []
    rng = random.Random(seed)
    offspring = []

    for _ in range(n_offspring):
        parent_a = tournament_select(cells, k=2, seed=rng.randint(0, 2**31))
        parent_b = tournament_select(cells, k=2, seed=rng.randint(0, 2**31))
        attempts = 0
        while parent_b.variant_id == parent_a.variant_id and attempts < 5:
            parent_b = tournament_select(cells, k=2, seed=rng.randint(0, 2**31))
            attempts += 1

        child = crossover_prompts(parent_a.prompt_text or "", parent_b.prompt_text or "",
                                  seed=rng.randint(0, 2**31))
        child = mutate_prompt(child, mutation_rate=mutation_rate, seed=rng.randint(0, 2**31))

        if child.strip():
            offspring.append(child)

    return offspring
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_evolver.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/evolver.py tests/test_forge_evolver.py
git commit -m "feat(forge): add evolution operators — tournament, crossover, mutation"
```

---

## Batch 6: Goodhart Monitoring

**PRD:** Composite monitoring score for human observation and Goodhart divergence detection. The composite is NEVER used as an optimization target — the optimizer sees only calibrated F1 on validation. Also detects F1 plateau (stale optimization) and train/validation gap (overfitting).

### Task 7: Composite score and divergence detection

**Files:**

- Create: `ollama_queue/forge/goodhart.py`
- Test: `tests/test_forge_goodhart.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_goodhart.py
"""Tests for Forge Goodhart monitoring — composite + divergence."""
from ollama_queue.forge.goodhart import (
    compute_monitoring_composite,
    check_goodhart_divergence,
    compute_metric_staleness,
)


def test_composite_all_ones():
    val = compute_monitoring_composite(kappa=1.0, calibrated_f1=1.0,
                                       archive_coverage=1.0, score_variance=1.0)
    assert val == 1.0


def test_composite_all_zeros():
    assert compute_monitoring_composite(kappa=0.0, calibrated_f1=0.0,
                                        archive_coverage=0.0, score_variance=0.0) == 0.0


def test_composite_weights():
    # Only kappa=1, rest=0 -> 0.3
    val = compute_monitoring_composite(kappa=1.0, calibrated_f1=0.0,
                                       archive_coverage=0.0, score_variance=0.0)
    assert abs(val - 0.3) < 0.001


def test_divergence_detected():
    train = [0.8, 0.82, 0.85, 0.87, 0.90]
    val = [0.6, 0.58, 0.55, 0.52, 0.50]
    result = check_goodhart_divergence(train, val)
    assert result["diverging"] is True
    assert result["gap"] > 0.15


def test_divergence_not_detected():
    train = [0.8, 0.82, 0.81, 0.83, 0.82]
    val = [0.78, 0.80, 0.79, 0.81, 0.80]
    result = check_goodhart_divergence(train, val)
    assert result["diverging"] is False


def test_divergence_insufficient_data():
    result = check_goodhart_divergence([0.8], [0.7])
    assert result["diverging"] is False
    assert result["reason"] == "insufficient_data"


def test_staleness_plateau():
    history = [0.75, 0.75, 0.76, 0.75, 0.75, 0.76, 0.75, 0.75, 0.76, 0.75]
    result = compute_metric_staleness(history, window=10)
    assert result["stale"] is True


def test_staleness_improving():
    history = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    result = compute_metric_staleness(history, window=10)
    assert result["stale"] is False


def test_staleness_insufficient():
    result = compute_metric_staleness([0.5, 0.6], window=10)
    assert result["reason"] == "insufficient_data"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_goodhart.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/goodhart.py
"""Forge Goodhart monitoring — composite score for human observation only.

The monitoring composite is NEVER used as an optimization target.
The optimizer sees only calibrated F1 on the validation set.
"""
from __future__ import annotations

import statistics


def compute_monitoring_composite(
    *, kappa: float, calibrated_f1: float,
    archive_coverage: float = 0.0, score_variance: float = 0.0,
) -> float:
    """Weighted composite for human monitoring — NEVER for optimization.

    Weights: kappa=0.3, f1=0.3, coverage=0.2, variance=0.2.
    """
    return (
        kappa * 0.3
        + calibrated_f1 * 0.3
        + archive_coverage * 0.2
        + min(1.0, score_variance) * 0.2
    )


def check_goodhart_divergence(
    train_f1s: list[float], validation_f1s: list[float], *, threshold: float = 0.15,
) -> dict:
    """Detect train/validation gap — sign of overfitting to train set."""
    if len(train_f1s) < 3 or len(validation_f1s) < 3:
        return {"diverging": False, "train_mean": None, "val_mean": None,
                "gap": None, "reason": "insufficient_data"}

    train_mean = statistics.mean(train_f1s[-5:])
    val_mean = statistics.mean(validation_f1s[-5:])
    gap = train_mean - val_mean

    return {
        "diverging": gap > threshold,
        "train_mean": round(train_mean, 4),
        "val_mean": round(val_mean, 4),
        "gap": round(gap, 4),
    }


def compute_metric_staleness(
    f1_history: list[float], *, window: int = 10, plateau_threshold: float = 0.02,
) -> dict:
    """Detect if optimization has stalled (F1 plateau)."""
    if len(f1_history) < window:
        return {"stale": False, "recent_stdev": None,
                "window_used": len(f1_history), "reason": "insufficient_data"}

    recent = f1_history[-window:]
    stdev = statistics.pstdev(recent)

    return {
        "stale": stdev < plateau_threshold,
        "recent_stdev": round(stdev, 4),
        "window_used": window,
    }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_goodhart.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/goodhart.py tests/test_forge_goodhart.py
git commit -m "feat(forge): add Goodhart monitoring — composite, divergence, staleness"
```

---

## Batch 7: Engine Evolve Phase (NEW FILE — `engine_evolve.py`)

**PRD:** Integrate Phase 2 modules into the Forge engine cycle via a dedicated `engine_evolve.py` orchestrator. After calibrate, the engine calls `run_evolve_phase()` which: (1) assigns pairs to train/validation splits, (2) computes behavior descriptors and inserts into archive, (3) allocates oracle budget via Thompson Sampling, (4) triggers evolution if archive has enough cells. `engine.py` gains one import and one function call — nothing more.

> **IMPORTANT:** Do NOT add functions to `engine.py`. It already exceeds ruff complexity limits (C901: 16>10, PLR0915: 74>50). All Phase 2 orchestration goes in the new `engine_evolve.py` file. Each function in `engine_evolve.py` does ONE thing and stays under 30 lines.

### Task 8: Create `engine_evolve.py` with split, archive, Thompson, evolution functions

**Files:**

- Create: `ollama_queue/forge/engine_evolve.py` (all Phase 2 orchestration in one file, one function per concern)
- Test: `tests/test_forge_engine_evolve.py` (all Phase 2 engine tests in one file)

**Step 1: Write the failing tests**

```python
# tests/test_forge_engine_evolve.py
"""Tests for Forge engine Phase 2 — evolve phase orchestration.

All Phase 2 engine integration tests live here. Each test targets one
function in engine_evolve.py. Functions are pure (no DB/HTTP) — they
take data in and return data out.
"""
from ollama_queue.forge.engine_evolve import (
    assign_pair_splits,
    populate_archive_cell,
    allocate_oracle_budget_thompson,
    maybe_evolve,
)
from ollama_queue.forge.splits import TRAIN, VALIDATION, TEST
from ollama_queue.forge.archive import ArchiveCell


# --- assign_pair_splits ---

def test_assign_pair_splits_deterministic():
    pairs = [
        {"item_a": "1", "item_b": "2", "similarity": 0.8, "quartile": "q1_likely"},
        {"item_a": "3", "item_b": "4", "similarity": 0.3, "quartile": "q3_unlikely"},
    ]
    result = assign_pair_splits(pairs, seed=42)
    assert all("split" in p for p in result)
    assert all(p["split"] in (TRAIN, VALIDATION, TEST) for p in result)

    # Deterministic
    result2 = assign_pair_splits(pairs, seed=42)
    assert [p["split"] for p in result] == [p["split"] for p in result2]


def test_assign_pair_splits_uses_item_a():
    """Split is determined by source item, not pair."""
    pairs = [
        {"item_a": "same", "item_b": "x", "similarity": 0.5, "quartile": "q2_maybe"},
        {"item_a": "same", "item_b": "y", "similarity": 0.3, "quartile": "q3_unlikely"},
    ]
    result = assign_pair_splits(pairs, seed=42)
    assert result[0]["split"] == result[1]["split"]


# --- populate_archive_cell ---

def test_populate_archive_creates_cell():
    results = [
        {"judge_reasoning": "Good match because error handling applies.", "judge_score": 4},
        {"judge_reasoning": "Partial overlap with testing concern.", "judge_score": 3},
    ]
    metrics = {"f1": 0.75}
    cell = populate_archive_cell(results, metrics, variant_id="A", grid_size=10)
    assert isinstance(cell, ArchiveCell)
    assert cell.variant_id == "A"
    assert cell.fitness == 0.75
    assert 0 <= cell.x_bin < 10
    assert 0 <= cell.y_bin < 10


def test_populate_archive_no_results():
    cell = populate_archive_cell([], {"f1": 0.0}, variant_id="A", grid_size=10)
    assert cell.x_bin == 0
    assert cell.y_bin == 0


# --- allocate_oracle_budget_thompson ---

def test_allocate_budget_with_quartiles():
    results = [
        {"quartile": "q1_likely", "id": 1, "judge_score": 3},
        {"quartile": "q1_likely", "id": 2, "judge_score": 4},
        {"quartile": "q2_maybe", "id": 3, "judge_score": 3},
        {"quartile": "q3_unlikely", "id": 4, "judge_score": 2},
        {"quartile": "q4_none", "id": 5, "judge_score": 1},
    ]
    sample = allocate_oracle_budget_thompson(
        results, total_budget=4, thompson_state=None, seed=42,
    )
    assert len(sample) <= 4
    assert all(r in results for r in sample)


def test_allocate_budget_returns_empty_for_no_results():
    sample = allocate_oracle_budget_thompson([], total_budget=10, thompson_state=None, seed=42)
    assert sample == []


# --- maybe_evolve ---

def _cell(x, y, variant, fitness, prompt="Test prompt."):
    return ArchiveCell(x_bin=x, y_bin=y, x_value=float(x), y_value=float(y),
                       variant_id=variant, fitness=fitness, prompt_text=prompt)


def test_maybe_evolve_enough_cells():
    grid = {(i, 0): _cell(i, 0, f"v{i}", 0.5 + i * 0.1, f"Rule {i}.") for i in range(5)}
    offspring = maybe_evolve(grid, n_offspring=3, min_archive_size=3, seed=42)
    assert len(offspring) == 3


def test_maybe_evolve_too_few_cells():
    grid = {(0, 0): _cell(0, 0, "v0", 0.5, "One rule.")}
    offspring = maybe_evolve(grid, n_offspring=3, min_archive_size=3, seed=42)
    assert offspring == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_engine_evolve.py -v`
Expected: FAIL with "cannot import name 'assign_pair_splits' from 'ollama_queue.forge.engine_evolve'"

**Step 3: Write the implementation**

```python
# ollama_queue/forge/engine_evolve.py
"""Forge engine — Phase 2 (Evolve) orchestration.

Coordinates splits, descriptors, archive, Thompson Sampling, and evolution.
Called from engine.py as a single run_evolve_phase() entry point.
Each public function does ONE thing — pure computation, no DB/HTTP.
"""
from __future__ import annotations

import random

from ollama_queue.forge.archive import ArchiveCell, try_insert
from ollama_queue.forge.descriptors import (
    DEFAULT_GRID_SIZE,
    compute_default_descriptors,
    get_descriptor_axes,
    normalize_to_bin,
)
from ollama_queue.forge.evolver import evolve_generation
from ollama_queue.forge.splits import assign_split
from ollama_queue.forge.thompson import ThompsonBudget


def assign_pair_splits(pairs: list[dict], *, seed: int = 0) -> list[dict]:
    """Annotate each pair with its train/validation/test split.

    Split is determined by the source item (item_a) for consistency.
    """
    for pair in pairs:
        pair["split"] = assign_split(pair["item_a"], seed=seed)
    return pairs


def populate_archive_cell(
    results: list[dict], metrics: dict, *, variant_id: str, grid_size: int = DEFAULT_GRID_SIZE,
) -> ArchiveCell:
    """Compute behavior descriptors and create an archive cell for this variant."""
    desc = compute_default_descriptors(results)
    axes = get_descriptor_axes()

    x_range = axes["x"].get("range", [0.0, 1.0])
    y_range = axes["y"].get("range", [0.0, 1.0])
    x_bin = normalize_to_bin(desc.get("output_length", 0.0), x_range[0], x_range[1], grid_size)
    y_bin = normalize_to_bin(desc.get("vocabulary_diversity", 0.0), y_range[0], y_range[1], grid_size)

    return ArchiveCell(
        x_bin=x_bin, y_bin=y_bin,
        x_value=desc.get("output_length", 0.0),
        y_value=desc.get("vocabulary_diversity", 0.0),
        variant_id=variant_id,
        fitness=metrics.get("f1", 0.0),
    )


def allocate_oracle_budget_thompson(
    results: list[dict], *, total_budget: int,
    thompson_state: dict | None = None, seed: int | None = None,
) -> list[dict]:
    """Allocate oracle budget across quartiles using Thompson Sampling."""
    if not results:
        return []

    tb = ThompsonBudget()
    if thompson_state:
        tb.load_state(thompson_state)

    by_quartile: dict[str, list[dict]] = {}
    for r in results:
        by_quartile.setdefault(r.get("quartile", "unknown"), []).append(r)

    categories = list(by_quartile.keys())
    if not categories:
        return []

    allocation = tb.allocate_budget(total_budget, categories, seed=seed)
    rng = random.Random(seed)  # noqa: S311
    sample = []
    for cat, n in allocation.items():
        pool = by_quartile.get(cat, [])
        n_pick = min(n, len(pool))
        if n_pick > 0:
            sample.extend(rng.sample(pool, n_pick))
    return sample


def maybe_evolve(
    grid: dict, *, n_offspring: int = 4, min_archive_size: int = 3, seed: int | None = None,
) -> list[str]:
    """Trigger evolution if archive is large enough. Returns new prompt strings."""
    cells = list(grid.values())
    if len(cells) < min_archive_size:
        return []
    return evolve_generation(cells, n_offspring=n_offspring, seed=seed)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_forge_engine_evolve.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/engine_evolve.py tests/test_forge_engine_evolve.py
git commit -m "feat(forge): add engine_evolve.py — Phase 2 orchestration (splits, archive, Thompson, evolution)"
```

---

### Task 9: Wire `run_evolve_phase()` into `engine.py`

**Files:**

- Modify: `ollama_queue/forge/engine_evolve.py` (add `run_evolve_phase()` entry point that coordinates DB calls)
- Modify: `ollama_queue/forge/engine.py` (add one import + one function call — nothing else)

> **CRITICAL:** `engine.py` gets exactly ONE new line in `_run_calibrate_and_metrics()` or after it. Do not add any other functions or logic to `engine.py`.

**Step 1: Add `run_evolve_phase()` to `engine_evolve.py`**

This is the only function in `engine_evolve.py` that touches DB — it coordinates the pure functions above with DB persistence.

```python
# Append to engine_evolve.py

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


def run_evolve_phase(
    *, db: Database, run_id: int, run: dict, results: list[dict], metrics: dict,
) -> None:
    """Phase 2 orchestration: splits → archive → Thompson → evolve.

    Called from engine.py after calibration. Never raises — logs and returns on error.
    """
    try:
        _run_evolve_phase_inner(db=db, run_id=run_id, run=run, results=results, metrics=metrics)
    except Exception as exc:
        _log.warning("forge evolve phase: run %d error: %s", run_id, exc)


def _run_evolve_phase_inner(
    *, db: Database, run_id: int, run: dict, results: list[dict], metrics: dict,
) -> None:
    """Inner evolve — split, archive, Thompson, evolve."""
    from ollama_queue.forge.settings import get_forge_setting

    seed = run.get("seed", 0)

    # 1. Archive population
    cell = populate_archive_cell(results, metrics, variant_id=run["variant_id"])
    grid_rows = db.get_forge_archive_grid()
    grid = {(r["x_bin"], r["y_bin"]): ArchiveCell(**{
        k: r[k] for k in ("x_bin", "y_bin", "x_value", "y_value", "variant_id", "fitness")
    }) for r in grid_rows}

    if try_insert(grid, cell):
        db.upsert_forge_archive_cell(
            x_bin=cell.x_bin, y_bin=cell.y_bin,
            x_value=cell.x_value, y_value=cell.y_value,
            variant_id=cell.variant_id, fitness=cell.fitness,
            run_id=run_id,
        )
        _log.info("forge evolve: archived variant %s at (%d, %d) fitness=%.3f",
                   cell.variant_id, cell.x_bin, cell.y_bin, cell.fitness)

    # 2. Thompson state update (load → observe → save)
    thompson_state = db.load_forge_thompson_state()
    # State update happens after oracle phase in future runs
    if thompson_state:
        db.save_forge_thompson_state(thompson_state)

    # 3. Evolution trigger
    offspring = maybe_evolve(grid, seed=seed)
    if offspring:
        _log.info("forge evolve: generated %d offspring prompts", len(offspring))
```

**Step 2: Add one-line call to `engine.py`**

In `engine.py`, at the end of `_run_calibrate_and_metrics()`, add:

```python
from ollama_queue.forge.engine_evolve import run_evolve_phase

# After the _log.info("forge engine: run %d complete ...") line:
run_evolve_phase(db=db, run_id=run_id, run=run, results=results, metrics=metrics)
```

**Step 3: Run full test suite**

Run: `python -m pytest --timeout=120 -x -q`
Expected: All tests pass.

**Step 4: Commit**

```bash
git add ollama_queue/forge/engine_evolve.py ollama_queue/forge/engine.py
git commit -m "feat(forge): wire evolve phase into engine — one-line integration"
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_engine_evolve.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/engine.py tests/test_forge_engine_evolve.py
git commit -m "feat(forge): add evolution trigger to engine"
```

---

## Batch 8: API Routes

**PRD:** REST endpoints for archive grid visualization, cell details, and evolution status. Follows the same pattern as Phase 1's `forge_runs.py` and `forge_settings.py`.

### Task 12: Archive API endpoints

**Files:**

- Create: `ollama_queue/api/forge_archive.py`
- Modify: `ollama_queue/api/__init__.py` (wire router)
- Test: `tests/test_api_forge_archive.py`

**Step 1: Write the failing test**

```python
# tests/test_api_forge_archive.py
"""Tests for Forge archive API endpoints."""
import pytest
from fastapi.testclient import TestClient
from ollama_queue.db import Database
from ollama_queue.app import create_app


@pytest.fixture
def client():
    db = Database(":memory:")
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_get_forge_archive_empty(client):
    c, _ = client
    resp = c.get("/api/forge/archive")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cells"] == []
    assert data["qd_score"] == 0.0
    assert data["coverage"] == 0.0


def test_get_forge_archive_with_cells(client):
    c, db = client
    db.upsert_forge_archive_cell(
        x_bin=0, y_bin=0, x_value=0.1, y_value=0.2,
        variant_id="A", fitness=0.75,
    )
    db.upsert_forge_archive_cell(
        x_bin=1, y_bin=1, x_value=0.5, y_value=0.5,
        variant_id="B", fitness=0.85,
    )
    resp = c.get("/api/forge/archive")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["cells"]) == 2
    assert data["qd_score"] == 1.6
    assert data["coverage"] > 0


def test_get_forge_archive_heatmap(client):
    c, db = client
    db.upsert_forge_archive_cell(
        x_bin=0, y_bin=0, x_value=0.0, y_value=0.0,
        variant_id="A", fitness=0.8,
    )
    resp = c.get("/api/forge/archive/heatmap?grid_size=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["heatmap"]) == 5
    assert data["heatmap"][0][0] == 0.8


def test_get_forge_archive_cell(client):
    c, db = client
    db.upsert_forge_archive_cell(
        x_bin=3, y_bin=4, x_value=0.3, y_value=0.4,
        variant_id="A", fitness=0.65,
    )
    resp = c.get("/api/forge/archive/cell?x=3&y=4")
    assert resp.status_code == 200
    assert resp.json()["variant_id"] == "A"


def test_get_forge_archive_cell_not_found(client):
    c, _ = client
    resp = c.get("/api/forge/archive/cell?x=99&y=99")
    assert resp.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_forge_archive.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/api/forge_archive.py
"""Forge archive API endpoints — MAP-Elites grid visualization."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import ollama_queue.api as _api
from ollama_queue.forge.archive import compute_coverage, compute_qd_score, grid_to_heatmap
from ollama_queue.forge.descriptors import DEFAULT_GRID_SIZE

router = APIRouter(tags=["forge"])


@router.get("/api/forge/archive")
def get_forge_archive():
    cells = _api.db.get_forge_archive_grid()
    grid = {(c["x_bin"], c["y_bin"]): c for c in cells}
    return {
        "cells": cells,
        "qd_score": compute_qd_score_from_dicts(cells),
        "coverage": len(cells) / (DEFAULT_GRID_SIZE ** 2) if cells else 0.0,
        "grid_size": DEFAULT_GRID_SIZE,
    }


@router.get("/api/forge/archive/heatmap")
def get_forge_archive_heatmap(grid_size: int = Query(DEFAULT_GRID_SIZE, ge=1, le=50)):
    cells = _api.db.get_forge_archive_grid()
    # Build heatmap
    heatmap: list[list[float | None]] = [[None] * grid_size for _ in range(grid_size)]
    for c in cells:
        x, y = c["x_bin"], c["y_bin"]
        if 0 <= x < grid_size and 0 <= y < grid_size:
            heatmap[y][x] = c["fitness"]
    return {"heatmap": heatmap, "grid_size": grid_size}


@router.get("/api/forge/archive/cell")
def get_forge_archive_cell(x: int = Query(...), y: int = Query(...)):
    cell = _api.db.get_forge_archive_cell(x, y)
    if cell is None:
        raise HTTPException(404, detail="Archive cell not found")
    return cell


def compute_qd_score_from_dicts(cells: list[dict]) -> float:
    return sum(c.get("fitness", 0.0) for c in cells)
```

Add to `api/__init__.py`:

```python
from ollama_queue.api import forge_archive
app.include_router(forge_archive.router)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_forge_archive.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add ollama_queue/api/forge_archive.py ollama_queue/api/__init__.py tests/test_api_forge_archive.py
git commit -m "feat(forge): add archive API endpoints — grid, heatmap, cell"
```

---

## Batch 9: Wiring + Settings

**PRD:** Final wiring: update `forge/__init__.py` re-exports for Phase 2 symbols, seed new settings defaults (evolution, Thompson, splits), and run the full test suite.

### Task 13: Update re-exports and seed Phase 2 settings

**Files:**

- Modify: `ollama_queue/forge/__init__.py`
- Modify: `ollama_queue/db/schema.py` (seed Phase 2 setting defaults)
- Modify: `ollama_queue/forge/settings.py` (add Phase 2 setting keys)

**Step 1: Add Phase 2 keys to `forge/settings.py`**

Append to `FORGE_DEFAULTS`:

```python
# Phase 2: Evolution
"forge.grid_size": 10,
"forge.evolution_enabled": False,
"forge.evolution_offspring": 4,
"forge.evolution_min_archive": 3,
"forge.evolution_mutation_rate": 0.15,
"forge.thompson_enabled": True,
"forge.thompson_discount": 0.95,
"forge.thompson_window": 100,
```

**Step 2: Update `forge/__init__.py` re-exports**

Add:

```python
from ollama_queue.forge.archive import ArchiveCell, try_insert, compute_qd_score, compute_coverage, get_elites, grid_to_heatmap
from ollama_queue.forge.descriptors import compute_default_descriptors, normalize_to_bin, get_descriptor_axes, DEFAULT_GRID_SIZE
from ollama_queue.forge.evolver import tournament_select, crossover_prompts, mutate_prompt, evolve_generation
from ollama_queue.forge.goodhart import compute_monitoring_composite, check_goodhart_divergence, compute_metric_staleness
from ollama_queue.forge.splits import assign_split, split_items, TRAIN, VALIDATION, TEST
from ollama_queue.forge.thompson import ThompsonBudget
```

And append to `__all__`.

**Step 3: Seed Phase 2 defaults in `db/schema.py`**

Add to the Forge settings seed block:

```python
# Forge Phase 2 defaults
("forge.grid_size", "10"),
("forge.evolution_enabled", "false"),
("forge.evolution_offspring", "4"),
("forge.evolution_min_archive", "3"),
("forge.evolution_mutation_rate", "0.15"),
("forge.thompson_enabled", "true"),
("forge.thompson_discount", "0.95"),
("forge.thompson_window", "100"),
```

**Step 4: Run full test suite**

Run: `python -m pytest --timeout=120 -x -q`
Expected: All existing tests pass, plus ~70 new Phase 2 tests.

**Step 5: Commit**

```bash
git add ollama_queue/forge/__init__.py ollama_queue/forge/settings.py ollama_queue/db/schema.py
git commit -m "feat(forge): Phase 2 wiring — re-exports, settings, schema seeds"
```

---

### Task 14: Verify all Phase 2 imports and integration

**Step 1: Verify all modules importable**

```python
from ollama_queue.forge import (
    ArchiveCell, try_insert, compute_qd_score, compute_coverage,
    ThompsonBudget, evolve_generation, tournament_select,
    compute_monitoring_composite, check_goodhart_divergence,
    assign_split, split_items, TRAIN, VALIDATION, TEST,
    compute_default_descriptors, normalize_to_bin,
)
print("All Phase 2 forge imports OK")
```

**Step 2: Verify new API endpoint registered**

```bash
python -c "
from ollama_queue.db import Database
from ollama_queue.app import create_app
db = Database(':memory:')
db.initialize()
app = create_app(db)
routes = [r.path for r in app.routes if hasattr(r, 'path')]
archive_routes = [r for r in routes if '/forge/archive' in r]
print(f'Archive routes: {len(archive_routes)}')
assert len(archive_routes) >= 3
print('Phase 2 API wiring OK')
"
```

**Step 3: Run full test suite**

Run: `python -m pytest --timeout=120 -q`
Expected: All tests pass, 0 failures.

**Step 4: Final commit**

```bash
git add ollama_queue/forge/__init__.py
git commit -m "feat(forge): Phase 2 complete — MAP-Elites evolution + Thompson Sampling"
```

---

## Summary

| Batch | Tasks | Tests | Modules Created/Modified |
|-------|-------|-------|--------------------------|
| 1. Splits | 1 | ~7 | splits.py |
| 2. Descriptors | 2 | ~11 | descriptors.py |
| 3. Archive | 3-4 | ~17 | archive.py, db/forge.py (+tables) |
| 4. Thompson | 5 | ~10 | thompson.py |
| 5. Evolution | 6 | ~9 | evolver.py |
| 6. Goodhart | 7 | ~9 | goodhart.py |
| 7. Engine Extension | 8-11 | ~8 | engine.py (4 additions) |
| 8. API Routes | 12 | ~5 | api/forge_archive.py |
| 9. Wiring | 13-14 | ~2 | __init__.py, settings.py, schema.py |
| **Total** | **14 tasks** | **~78 tests** | **6 new + 4 modified files** |

**Dependency graph:**

```
splits.py                      (Batch 1: standalone)
descriptors.py                 (Batch 2: standalone)
archive.py ← db/forge.py      (Batch 3: needs schema)
thompson.py                    (Batch 4: standalone)
evolver.py ← archive.py       (Batch 5: needs ArchiveCell)
goodhart.py                    (Batch 6: standalone)
engine.py ← all of the above  (Batch 7: integrates everything)
api/forge_archive.py ← db     (Batch 8: needs DB CRUD)
```

Batches 1, 2, 4, and 6 can run in parallel (no cross-dependencies).
