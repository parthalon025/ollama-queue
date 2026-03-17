# Forge v2 Phase 1 (Calibrate) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a fresh `ollama_queue/forge/` package that replaces the existing eval pipeline with oracle-calibrated, embedding-stratified evaluation. Phase 1 delivers: trustworthy judge scoring, oracle validation every cycle, Calibration UI, and Observer/Advisor autonomy levels.

**Architecture:** Modular package with one file per responsibility (~50-150 lines each). Reuses existing `eval/providers.py` (EvalProvider ABC + Claude/OpenAI/Ollama adapters) and `db/` patterns (mixin classes, RLock, WAL mode). New `forge/` tables added to `db/schema.py`. API routes in `api/forge_*.py` files wired via `register_routes()`. Embedding via nomic-embed-text through Ollama proxy. Oracle (Claude/GPT-4) as ground truth — no cluster dependency for metrics.

**Tech Stack:** Python 3.12, FastAPI, SQLite (WAL), httpx, numpy (cosine sim), scikit-learn (isotonic regression), pytest + pytest-xdist

**Design doc:** `docs/plans/2026-03-17-forge-v2-design.md`

**Reference code:**
- `ollama_queue/eval/providers.py` — EvalProvider ABC, reuse directly
- `ollama_queue/eval/judge.py` — prompt patterns (adapt, don't copy)
- `ha-aria/aria/modules/shadow_engine.py:64-201` — Thompson Sampling pattern
- `ha-aria/aria/engine/predictions/scoring.py:78-91` — mean-of-halves trend
- `ollama_queue/db/eval.py` — DB mixin pattern

---

## Module Map

```
ollama_queue/forge/           # Fresh v2 package — DO NOT modify eval/
  __init__.py                 # Re-exports public API (~20 lines)
  types.py                    # Protocol, enums, result dataclasses (~60 lines)
  settings.py                 # Setting keys + defaults + helpers (~40 lines)
  embedder.py                 # Embed items via nomic-embed-text (~80 lines)
  pairs.py                    # Embedding-stratified pair selection (~100 lines)
  judge.py                    # Prompt building + response parsing (~120 lines)
  oracle.py                   # Oracle scoring + kappa + per-group (~120 lines)
  calibrator.py               # Isotonic regression mapping (~80 lines)
  metrics.py                  # Oracle-ground-truth F1, Spearman, variance (~80 lines)
  engine.py                   # Pipeline orchestrator (~150 lines)

ollama_queue/db/
  forge.py                    # ForgeMixin: CRUD for forge_* tables (~150 lines)

ollama_queue/api/
  forge_runs.py               # Run lifecycle endpoints (~120 lines)
  forge_calibration.py        # Calibration data endpoints (~80 lines)
  forge_settings.py           # Settings + autonomy endpoints (~60 lines)

tests/
  test_forge_types.py         # Protocol + enum tests
  test_forge_settings.py      # Setting defaults + helpers
  test_forge_embedder.py      # Embedding with mocked Ollama
  test_forge_pairs.py         # Stratified selection logic
  test_forge_judge.py         # Prompt build + response parse
  test_forge_oracle.py        # Oracle scoring + kappa math
  test_forge_calibrator.py    # Isotonic regression
  test_forge_metrics.py       # F1, Spearman, variance
  test_forge_engine.py        # Full pipeline orchestration
  test_forge_db.py            # DB mixin CRUD
  test_api_forge_runs.py      # API endpoint tests
  test_api_forge_calibration.py
  test_api_forge_settings.py
```

Each module is one screen of code. A sub-agent can read, understand, and modify any single file without needing context from the others.

---

## Batch 1: Foundation (Types + Settings + DB Schema)

**PRD:** Define the data contracts, configuration surface, and storage layer that every other module depends on. No business logic — just shapes and plumbing.

### Task 1: ForgeDataSource Protocol and result types

**Files:**

- Create: `ollama_queue/forge/__init__.py`
- Create: `ollama_queue/forge/types.py`
- Test: `tests/test_forge_types.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_types.py
"""Tests for Forge type definitions and Protocol compliance."""
from ollama_queue.forge.types import (
    ForgeDataSource,
    ForgeResult,
    ForgeRunStatus,
    AutonomyLevel,
    PairQuartile,
)


def test_autonomy_levels():
    assert AutonomyLevel.OBSERVER.value == "observer"
    assert AutonomyLevel.ADVISOR.value == "advisor"
    assert AutonomyLevel.OPERATOR.value == "operator"


def test_pair_quartiles():
    assert PairQuartile.LIKELY.value == "q1_likely"
    assert PairQuartile.MAYBE.value == "q2_maybe"
    assert PairQuartile.UNLIKELY.value == "q3_unlikely"
    assert PairQuartile.NONE.value == "q4_none"


def test_run_status_terminal():
    assert ForgeRunStatus.COMPLETE.is_terminal()
    assert ForgeRunStatus.FAILED.is_terminal()
    assert ForgeRunStatus.CANCELLED.is_terminal()
    assert not ForgeRunStatus.RUNNING.is_terminal()
    assert not ForgeRunStatus.QUEUED.is_terminal()


def test_forge_result_fields():
    r = ForgeResult(
        source_item_id="101",
        target_item_id="102",
        embedding_similarity=0.85,
        quartile=PairQuartile.LIKELY,
        judge_score=4,
        oracle_score=None,
    )
    assert r.source_item_id == "101"
    assert r.oracle_score is None


class _MockSource:
    def get_items(self, *, limit=100):
        return [{"id": "1", "title": "t", "one_liner": "o", "description": "d"}]


def test_protocol_compliance():
    """A class implementing get_items satisfies ForgeDataSource."""
    src = _MockSource()
    assert isinstance(src, ForgeDataSource)
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python -m pytest tests/test_forge_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ollama_queue.forge'`

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/__init__.py
"""Forge v2 — evaluation engine with oracle-calibrated scoring."""
from ollama_queue.forge.types import (
    AutonomyLevel,
    ForgeDataSource,
    ForgeResult,
    ForgeRunStatus,
    PairQuartile,
)

__all__ = [
    "AutonomyLevel",
    "ForgeDataSource",
    "ForgeResult",
    "ForgeRunStatus",
    "PairQuartile",
]
```

```python
# ollama_queue/forge/types.py
"""Forge type definitions — Protocol, enums, result containers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class AutonomyLevel(Enum):
    """How much Forge can act on its own."""
    OBSERVER = "observer"   # Reports only
    ADVISOR = "advisor"     # Auto-promote when gates pass
    OPERATOR = "operator"   # Auto-promote + feedback to data source


class ForgeRunStatus(Enum):
    """Lifecycle states for a Forge run."""
    QUEUED = "queued"
    EMBEDDING = "embedding"
    JUDGING = "judging"
    ORACLE = "oracle"
    CALIBRATING = "calibrating"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in (self.COMPLETE, self.FAILED, self.CANCELLED)


class PairQuartile(Enum):
    """Embedding distance quartile for stratified sampling."""
    LIKELY = "q1_likely"        # sim 0.75-1.0
    MAYBE = "q2_maybe"          # sim 0.50-0.75
    UNLIKELY = "q3_unlikely"    # sim 0.25-0.50
    NONE = "q4_none"            # sim 0.00-0.25


@dataclass(frozen=True, slots=True)
class ForgeResult:
    """One scored pair from a Forge run."""
    source_item_id: str
    target_item_id: str
    embedding_similarity: float
    quartile: PairQuartile
    judge_score: int | None = None
    oracle_score: int | None = None
    judge_reasoning: str | None = None
    oracle_reasoning: str | None = None
    calibrated_score: float | None = None


@runtime_checkable
class ForgeDataSource(Protocol):
    """Minimum contract for a Forge data source.

    Only get_items() is required. All other methods are optional —
    Forge computes embeddings itself and uses the oracle as ground truth.
    """

    def get_items(self, *, limit: int = 100) -> list[dict]:
        """Return items with id, title, one_liner, description, tags."""
        ...
```

**Step 4: Run test to verify it passes**

Run: `cd ~/Documents/projects/ollama-queue && python -m pytest tests/test_forge_types.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/__init__.py ollama_queue/forge/types.py tests/test_forge_types.py
git commit -m "feat(forge): add type definitions — Protocol, enums, ForgeResult"
```

---

### Task 2: Forge settings keys and defaults

**Files:**

- Create: `ollama_queue/forge/settings.py`
- Test: `tests/test_forge_settings.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_settings.py
"""Tests for Forge setting keys and helpers."""
from ollama_queue.forge.settings import (
    FORGE_DEFAULTS,
    get_forge_setting,
)


def test_defaults_contain_all_keys():
    required = {
        "forge.oracle_provider",
        "forge.oracle_model",
        "forge.oracle_budget",
        "forge.oracle_fraction",
        "forge.oracle_min_kappa",
        "forge.judge_model",
        "forge.judge_provider",
        "forge.judge_temperature",
        "forge.pairs_per_quartile",
        "forge.positive_threshold",
        "forge.f1_threshold",
        "forge.auto_promote_min_improvement",
        "forge.autonomy_level",
        "forge.embedding_model",
    }
    assert required.issubset(FORGE_DEFAULTS.keys())


def test_get_forge_setting_with_db_value(db):
    db.set_setting("forge.oracle_budget", "30")
    assert get_forge_setting(db, "forge.oracle_budget", int) == 30


def test_get_forge_setting_falls_back_to_default(db):
    val = get_forge_setting(db, "forge.oracle_budget", int)
    assert val == FORGE_DEFAULTS["forge.oracle_budget"]


def test_get_forge_setting_handles_none_for_numeric(db):
    db.set_setting("forge.oracle_budget", None)
    val = get_forge_setting(db, "forge.oracle_budget", int)
    assert val == FORGE_DEFAULTS["forge.oracle_budget"]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_settings.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/settings.py
"""Forge setting keys, defaults, and typed accessors."""
from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from ollama_queue.db import Database

T = TypeVar("T", int, float, str, bool)

FORGE_DEFAULTS: dict[str, int | float | str | bool] = {
    # Oracle
    "forge.oracle_provider": "claude",
    "forge.oracle_model": "claude-sonnet-4-20250514",
    "forge.oracle_budget": 20,
    "forge.oracle_fraction": 0.2,
    "forge.oracle_min_kappa": 0.6,
    # Judge
    "forge.judge_model": "",
    "forge.judge_provider": "ollama",
    "forge.judge_temperature": 0.1,
    # Pair selection
    "forge.pairs_per_quartile": 20,
    "forge.positive_threshold": 3,
    "forge.embedding_model": "nomic-embed-text",
    # Autonomy
    "forge.autonomy_level": "observer",
    # Auto-promote gates
    "forge.f1_threshold": 0.7,
    "forge.auto_promote_min_improvement": 0.05,
}


def get_forge_setting(db: Database, key: str, cast: type[T]) -> T:
    """Read a forge setting from DB, falling back to FORGE_DEFAULTS.

    Handles None and empty string gracefully for numeric types.
    """
    raw = db.get_setting(key)
    default = FORGE_DEFAULTS.get(key)

    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return cast(default) if default is not None else cast()

    try:
        if cast is bool:
            if isinstance(raw, str):
                return raw.strip().lower() not in ("false", "0", "")
            return bool(raw)
        return cast(raw)
    except (ValueError, TypeError):
        return cast(default) if default is not None else cast()
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_settings.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/settings.py tests/test_forge_settings.py
git commit -m "feat(forge): add settings keys and typed accessor"
```

---

### Task 3: Forge DB mixin and schema

**Files:**

- Create: `ollama_queue/db/forge.py`
- Modify: `ollama_queue/db/schema.py` (add forge tables to CREATE section)
- Modify: `ollama_queue/db/__init__.py` (add ForgeMixin to Database MRO)
- Test: `tests/test_forge_db.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_db.py
"""Tests for Forge DB mixin — CRUD for forge tables."""
import json


def test_create_forge_run(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
        pairs_per_quartile=20,
    )
    assert isinstance(run_id, int)
    assert run_id > 0


def test_get_forge_run(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    run = db.get_forge_run(run_id)
    assert run is not None
    assert run["status"] == "queued"
    assert run["judge_model"] == "qwen3:14b"


def test_get_forge_run_not_found(db):
    assert db.get_forge_run(9999) is None


def test_update_forge_run_status(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.update_forge_run(run_id, status="judging")
    run = db.get_forge_run(run_id)
    assert run["status"] == "judging"


def test_insert_forge_result(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.insert_forge_result(
        run_id=run_id,
        source_item_id="101",
        target_item_id="102",
        embedding_similarity=0.85,
        quartile="q1_likely",
        judge_score=4,
    )
    results = db.get_forge_results(run_id)
    assert len(results) == 1
    assert results[0]["judge_score"] == 4
    assert results[0]["embedding_similarity"] == 0.85


def test_insert_forge_result_dedup(db):
    """INSERT OR IGNORE — duplicate pair skipped."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.insert_forge_result(
        run_id=run_id, source_item_id="101", target_item_id="102",
        embedding_similarity=0.85, quartile="q1_likely", judge_score=4,
    )
    db.insert_forge_result(
        run_id=run_id, source_item_id="101", target_item_id="102",
        embedding_similarity=0.85, quartile="q1_likely", judge_score=5,
    )
    results = db.get_forge_results(run_id)
    assert len(results) == 1
    assert results[0]["judge_score"] == 4  # first insert wins


def test_update_forge_result_oracle(db):
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="qwen3:14b",
        oracle_model="claude-sonnet-4-20250514",
    )
    db.insert_forge_result(
        run_id=run_id, source_item_id="101", target_item_id="102",
        embedding_similarity=0.85, quartile="q1_likely", judge_score=4,
    )
    results = db.get_forge_results(run_id)
    db.update_forge_result_oracle(results[0]["id"], oracle_score=3, oracle_reasoning="Looks ok")
    updated = db.get_forge_results(run_id)
    assert updated[0]["oracle_score"] == 3
    assert updated[0]["oracle_reasoning"] == "Looks ok"


def test_store_forge_embedding(db):
    db.store_forge_embedding("item-101", "abc123", [0.1, 0.2, 0.3])
    vec = db.get_forge_embedding("item-101", "abc123")
    assert vec is not None
    assert len(vec) == 3
    assert abs(vec[0] - 0.1) < 1e-6


def test_get_forge_embedding_miss(db):
    assert db.get_forge_embedding("missing", "abc123") is None


def test_list_forge_runs(db):
    db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A", judge_model="m", oracle_model="o",
    )
    db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="B", judge_model="m", oracle_model="o",
    )
    runs = db.list_forge_runs()
    assert len(runs) == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_db.py -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'create_forge_run'`

**Step 3: Write minimal implementation**

```python
# ollama_queue/db/forge.py
"""ForgeMixin — CRUD for forge_runs, forge_results, forge_embeddings tables."""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3


class ForgeMixin:
    """DB operations for Forge v2 evaluation engine."""

    def create_forge_run(
        self,
        *,
        data_source_url: str,
        variant_id: str,
        judge_model: str,
        oracle_model: str,
        pairs_per_quartile: int = 20,
        label: str | None = None,
        seed: int | None = None,
    ) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """INSERT INTO forge_runs
                   (data_source_url, variant_id, judge_model, oracle_model,
                    pairs_per_quartile, label, seed, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (data_source_url, variant_id, judge_model, oracle_model,
                 pairs_per_quartile, label, seed, time.time()),
            )
            conn.commit()
            return cur.lastrowid

    def get_forge_run(self, run_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM forge_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_forge_runs(self, *, limit: int = 50) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM forge_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_forge_run(self, run_id: int, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [run_id]
        with self._lock:
            conn = self._connect()
            conn.execute(f"UPDATE forge_runs SET {sets} WHERE id = ?", vals)
            conn.commit()

    def insert_forge_result(
        self,
        *,
        run_id: int,
        source_item_id: str,
        target_item_id: str,
        embedding_similarity: float,
        quartile: str,
        judge_score: int | None = None,
        judge_reasoning: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR IGNORE INTO forge_results
                   (run_id, source_item_id, target_item_id,
                    embedding_similarity, quartile, judge_score,
                    judge_reasoning, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, source_item_id, target_item_id,
                 embedding_similarity, quartile, judge_score,
                 judge_reasoning, time.time()),
            )
            conn.commit()

    def get_forge_results(self, run_id: int) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM forge_results WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_forge_result_oracle(
        self, result_id: int, *, oracle_score: int, oracle_reasoning: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE forge_results
                   SET oracle_score = ?, oracle_reasoning = ?
                   WHERE id = ?""",
                (oracle_score, oracle_reasoning, result_id),
            )
            conn.commit()

    def store_forge_embedding(
        self, item_id: str, content_hash: str, vector: list[float],
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR REPLACE INTO forge_embeddings
                   (item_id, content_hash, vector_json, created_at)
                   VALUES (?, ?, ?, ?)""",
                (item_id, content_hash, json.dumps(vector), time.time()),
            )
            conn.commit()

    def get_forge_embedding(
        self, item_id: str, content_hash: str,
    ) -> list[float] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT vector_json FROM forge_embeddings
                   WHERE item_id = ? AND content_hash = ?""",
                (item_id, content_hash),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["vector_json"])
```

Add tables to `db/schema.py` inside `initialize()` (after existing eval tables, ~line 530):

```sql
-- Forge v2 tables
CREATE TABLE IF NOT EXISTS forge_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    data_source_url     TEXT NOT NULL,
    variant_id          TEXT NOT NULL,
    judge_model         TEXT NOT NULL,
    oracle_model        TEXT NOT NULL,
    pairs_per_quartile  INTEGER DEFAULT 20,
    label               TEXT,
    seed                INTEGER,
    status              TEXT NOT NULL DEFAULT 'queued',
    metrics_json        TEXT,
    calibration_json    TEXT,
    oracle_json         TEXT,
    report_md           TEXT,
    error               TEXT,
    created_at          REAL NOT NULL,
    started_at          REAL,
    completed_at        REAL
);

CREATE TABLE IF NOT EXISTS forge_results (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                INTEGER NOT NULL REFERENCES forge_runs(id) ON DELETE CASCADE,
    source_item_id        TEXT NOT NULL,
    target_item_id        TEXT NOT NULL,
    embedding_similarity  REAL NOT NULL,
    quartile              TEXT NOT NULL,
    judge_score           INTEGER,
    judge_reasoning       TEXT,
    oracle_score          INTEGER,
    oracle_reasoning      TEXT,
    calibrated_score      REAL,
    created_at            REAL NOT NULL,
    UNIQUE (run_id, source_item_id, target_item_id)
);
CREATE INDEX IF NOT EXISTS idx_forge_results_run ON forge_results(run_id);

CREATE TABLE IF NOT EXISTS forge_embeddings (
    item_id       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    vector_json   TEXT NOT NULL,
    created_at    REAL NOT NULL,
    PRIMARY KEY (item_id, content_hash)
);
```

Add `ForgeMixin` to Database class MRO in `db/__init__.py`:

```python
from ollama_queue.db.forge import ForgeMixin

class Database(
    SchemaMixin,
    JobsMixin,
    ScheduleMixin,
    SettingsMixin,
    HealthMixin,
    DLQMixin,
    EvalMixin,
    BackendsMixin,
    ForgeMixin,  # <-- add here
):
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_db.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add ollama_queue/db/forge.py ollama_queue/db/schema.py ollama_queue/db/__init__.py tests/test_forge_db.py
git commit -m "feat(forge): add DB mixin and schema — forge_runs, forge_results, forge_embeddings"
```

---

## Batch 2: Embedding + Pair Selection

**PRD:** Given a list of items from a data source, embed them and select a stratified sample of pairs across the embedding distance spectrum. This is the foundation for oracle-as-ground-truth — pairs are selected by similarity, not by cluster membership.

### Task 4: Embedder module

**Files:**

- Create: `ollama_queue/forge/embedder.py`
- Test: `tests/test_forge_embedder.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_embedder.py
"""Tests for Forge embedder — embeds items via Ollama or cache."""
import hashlib
import json
from unittest.mock import patch, MagicMock

from ollama_queue.forge.embedder import embed_items, content_hash


def test_content_hash_deterministic():
    item = {"title": "foo", "one_liner": "bar", "description": "baz"}
    h1 = content_hash(item)
    h2 = content_hash(item)
    assert h1 == h2
    assert len(h1) == 16  # sha256[:16]


def test_content_hash_changes_on_content():
    a = content_hash({"title": "a", "one_liner": "", "description": ""})
    b = content_hash({"title": "b", "one_liner": "", "description": ""})
    assert a != b


def test_embed_items_uses_cache(db):
    """Cached embeddings are returned without calling Ollama."""
    item = {"id": "101", "title": "foo", "one_liner": "bar", "description": "baz"}
    ch = content_hash(item)
    db.store_forge_embedding("101", ch, [0.1, 0.2, 0.3])

    result = embed_items(
        db=db,
        items=[item],
        model="nomic-embed-text",
        http_base="http://127.0.0.1:7683",
    )
    assert "101" in result
    assert len(result["101"]) == 3


def test_embed_items_calls_ollama_on_miss(db):
    """Cache miss triggers Ollama embed call."""
    item = {"id": "101", "title": "foo", "one_liner": "bar", "description": "baz"}

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"embedding": [0.4, 0.5, 0.6]}

    with patch("ollama_queue.forge.embedder.httpx.post", return_value=mock_resp):
        result = embed_items(
            db=db, items=[item], model="nomic-embed-text",
            http_base="http://127.0.0.1:7683",
        )

    assert "101" in result
    assert result["101"] == [0.4, 0.5, 0.6]
    # Verify cached
    ch = content_hash(item)
    cached = db.get_forge_embedding("101", ch)
    assert cached == [0.4, 0.5, 0.6]


def test_embed_items_skips_failed_embed(db):
    """Failed Ollama call skips item, doesn't crash."""
    item = {"id": "101", "title": "foo", "one_liner": "bar", "description": "baz"}

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("ollama_queue.forge.embedder.httpx.post", return_value=mock_resp):
        result = embed_items(
            db=db, items=[item], model="nomic-embed-text",
            http_base="http://127.0.0.1:7683",
        )

    assert "101" not in result
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_embedder.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/embedder.py
"""Embed items via Ollama's /api/embed endpoint, with DB caching."""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


def content_hash(item: dict) -> str:
    """Deterministic hash of item text fields. Used as cache key."""
    text = f"{item.get('title', '')}|{item.get('one_liner', '')}|{item.get('description', '')}"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def embed_items(
    *,
    db: Database,
    items: list[dict],
    model: str,
    http_base: str,
    timeout: int = 30,
) -> dict[str, list[float]]:
    """Embed items, using DB cache where available.

    Returns: {item_id: vector} for all successfully embedded items.
    Missing items (cache miss + Ollama failure) are silently skipped.
    """
    result: dict[str, list[float]] = {}

    for item in items:
        item_id = item["id"]
        ch = content_hash(item)

        # Check cache first
        cached = db.get_forge_embedding(item_id, ch)
        if cached is not None:
            result[item_id] = cached
            continue

        # Cache miss — call Ollama
        text = f"{item.get('title', '')} {item.get('one_liner', '')} {item.get('description', '')}"
        try:
            resp = httpx.post(
                f"{http_base}/api/embed",
                json={"model": model, "input": text},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            _log.warning("forge embedder: HTTP error for item %s: %s", item_id, exc)
            continue

        if resp.status_code != 200:
            _log.warning("forge embedder: status %d for item %s", resp.status_code, item_id)
            continue

        data = resp.json()
        vector = data.get("embedding") or (data.get("embeddings") or [None])[0]
        if vector is None:
            _log.warning("forge embedder: no embedding in response for item %s", item_id)
            continue

        db.store_forge_embedding(item_id, ch, vector)
        result[item_id] = vector

    _log.info("forge embedder: %d/%d items embedded", len(result), len(items))
    return result
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_embedder.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/embedder.py tests/test_forge_embedder.py
git commit -m "feat(forge): add embedder module — Ollama embed with DB cache"
```

---

### Task 5: Pair selection module

**Files:**

- Create: `ollama_queue/forge/pairs.py`
- Test: `tests/test_forge_pairs.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_pairs.py
"""Tests for embedding-stratified pair selection."""
import math
from ollama_queue.forge.pairs import (
    cosine_similarity,
    build_similarity_matrix,
    select_stratified_pairs,
)
from ollama_queue.forge.types import PairQuartile


def test_cosine_similarity_identical():
    assert abs(cosine_similarity([1, 0, 0], [1, 0, 0]) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal():
    assert abs(cosine_similarity([1, 0, 0], [0, 1, 0])) < 1e-6


def test_cosine_similarity_opposite():
    assert abs(cosine_similarity([1, 0], [-1, 0]) - (-1.0)) < 1e-6


def test_build_similarity_matrix():
    embeddings = {
        "a": [1.0, 0.0],
        "b": [0.0, 1.0],
        "c": [0.707, 0.707],
    }
    matrix = build_similarity_matrix(embeddings)
    # 3 items → 3 pairs (a-b, a-c, b-c)
    assert len(matrix) == 3
    # a-b should be ~0, a-c and b-c should be ~0.707
    sims = {(p["item_a"], p["item_b"]): p["similarity"] for p in matrix}
    assert abs(sims[("a", "b")]) < 0.01
    assert abs(sims[("a", "c")] - 0.707) < 0.01


def test_select_stratified_pairs_quartile_distribution():
    """Each quartile gets equal representation."""
    # 10 items with embeddings spread across similarity range
    embeddings = {str(i): [math.cos(i * 0.3), math.sin(i * 0.3)] for i in range(10)}
    matrix = build_similarity_matrix(embeddings)

    pairs = select_stratified_pairs(matrix, per_quartile=5, seed=42)

    quartile_counts = {}
    for p in pairs:
        q = p["quartile"]
        quartile_counts[q] = quartile_counts.get(q, 0) + 1

    # Each quartile should have at most per_quartile pairs
    for q, count in quartile_counts.items():
        assert count <= 5


def test_select_stratified_pairs_deterministic():
    embeddings = {str(i): [math.cos(i * 0.5), math.sin(i * 0.5)] for i in range(8)}
    matrix = build_similarity_matrix(embeddings)

    pairs_a = select_stratified_pairs(matrix, per_quartile=3, seed=42)
    pairs_b = select_stratified_pairs(matrix, per_quartile=3, seed=42)

    ids_a = [(p["item_a"], p["item_b"]) for p in pairs_a]
    ids_b = [(p["item_a"], p["item_b"]) for p in pairs_b]
    assert ids_a == ids_b


def test_select_stratified_pairs_small_dataset():
    """Graceful with fewer pairs than requested."""
    embeddings = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    matrix = build_similarity_matrix(embeddings)

    pairs = select_stratified_pairs(matrix, per_quartile=10, seed=42)
    assert len(pairs) == 1  # only 1 possible pair


def test_pair_has_required_fields():
    embeddings = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.5, 0.5]}
    matrix = build_similarity_matrix(embeddings)
    pairs = select_stratified_pairs(matrix, per_quartile=5, seed=42)

    for p in pairs:
        assert "item_a" in p
        assert "item_b" in p
        assert "similarity" in p
        assert "quartile" in p
        assert p["quartile"] in [q.value for q in PairQuartile]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_pairs.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/pairs.py
"""Embedding-stratified pair selection for Forge evaluation.

Selects diverse pairs across the full similarity spectrum so the judge
is tested on easy matches, hard matches, and everything in between.
No cluster labels required.
"""
from __future__ import annotations

import math
import random

from ollama_queue.forge.types import PairQuartile

# Quartile boundaries (inclusive lower, exclusive upper)
_QUARTILE_BOUNDS: list[tuple[float, float, PairQuartile]] = [
    (0.75, 1.01, PairQuartile.LIKELY),
    (0.50, 0.75, PairQuartile.MAYBE),
    (0.25, 0.50, PairQuartile.UNLIKELY),
    (-1.01, 0.25, PairQuartile.NONE),
]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns -1.0 to 1.0."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_similarity_matrix(
    embeddings: dict[str, list[float]],
) -> list[dict]:
    """Compute pairwise cosine similarity for all item pairs.

    Returns list of {item_a, item_b, similarity} dicts, sorted by
    similarity descending.
    """
    ids = sorted(embeddings.keys())
    pairs = []
    for i, id_a in enumerate(ids):
        for id_b in ids[i + 1:]:
            sim = cosine_similarity(embeddings[id_a], embeddings[id_b])
            pairs.append({"item_a": id_a, "item_b": id_b, "similarity": sim})
    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs


def select_stratified_pairs(
    similarity_matrix: list[dict],
    *,
    per_quartile: int = 20,
    seed: int | None = None,
) -> list[dict]:
    """Select pairs stratified across 4 similarity quartiles.

    Returns up to per_quartile pairs from each quartile, each annotated
    with its quartile label. Deterministic when seed is provided.
    """
    rng = random.Random(seed)

    # Bucket pairs by quartile
    buckets: dict[str, list[dict]] = {q.value: [] for q in PairQuartile}
    for pair in similarity_matrix:
        sim = pair["similarity"]
        for low, high, quartile in _QUARTILE_BOUNDS:
            if low <= sim < high:
                buckets[quartile.value].append(pair)
                break

    # Sample from each bucket
    selected = []
    for quartile in PairQuartile:
        bucket = buckets[quartile.value]
        n = min(per_quartile, len(bucket))
        sampled = rng.sample(bucket, n) if n > 0 else []
        for pair in sampled:
            selected.append({**pair, "quartile": quartile.value})

    return selected
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_pairs.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/pairs.py tests/test_forge_pairs.py
git commit -m "feat(forge): add embedding-stratified pair selection"
```

---

## Batch 3: Judge

**PRD:** Build the Forge judge module — prompt construction and response parsing. The judge scores principle-target pairs blind (no cluster info, no similarity info). Adapted from existing `eval/judge.py` prompt patterns but simplified for Forge's single-rubric focus.

### Task 6: Judge prompt building and response parsing

**Files:**

- Create: `ollama_queue/forge/judge.py`
- Test: `tests/test_forge_judge.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_judge.py
"""Tests for Forge judge — prompt building and response parsing."""
from ollama_queue.forge.judge import build_judge_prompt, parse_judge_response


def test_build_judge_prompt_contains_principle():
    prompt = build_judge_prompt(
        principle="Always log before returning a fallback",
        target={"title": "Silent catch", "one_liner": "Bare except hides errors", "description": "When..."},
    )
    assert "Always log before returning a fallback" in prompt
    assert "Silent catch" in prompt


def test_build_judge_prompt_no_cluster_info():
    """Prompt must not contain any cluster or similarity information."""
    prompt = build_judge_prompt(
        principle="Test principle",
        target={"title": "T", "one_liner": "O", "description": "D"},
    )
    lower = prompt.lower()
    assert "cluster" not in lower
    assert "similarity" not in lower
    assert "quartile" not in lower


def test_parse_judge_response_valid_json():
    text = '{"transfer": 4, "reasoning": "Good match because..."}'
    result = parse_judge_response(text)
    assert result["transfer"] == 4
    assert "reasoning" in result
    assert result["error"] is None


def test_parse_judge_response_with_think_block():
    text = '<think>Let me analyze...</think>{"transfer": 3, "reasoning": "Partial match"}'
    result = parse_judge_response(text)
    assert result["transfer"] == 3
    assert result["judge_reasoning"] == "Let me analyze..."


def test_parse_judge_response_clamps_score():
    text = '{"transfer": 7, "reasoning": "x"}'
    result = parse_judge_response(text)
    assert result["transfer"] == 5  # clamped to max


def test_parse_judge_response_parse_failure():
    text = "I think this is a good match overall."
    result = parse_judge_response(text)
    assert result["transfer"] == 1  # conservative default
    assert result["error"] == "parse_failed"


def test_parse_judge_response_extract_score_from_text():
    """Fallback: extract standalone digit 1-5 if JSON fails."""
    text = "The transfer score is 4 out of 5."
    result = parse_judge_response(text)
    assert result["transfer"] == 4
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_judge.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/judge.py
"""Forge judge — prompt construction and response parsing.

The judge scores principle-target pairs on a 1-5 transfer scale.
It receives NO cluster or similarity information — scoring is blind.
"""
from __future__ import annotations

import json
import re

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_JSON_RE = re.compile(r"\{[^{}]*\}")
_SCORE_RE = re.compile(r"\b([1-5])\b")


def build_judge_prompt(*, principle: str, target: dict) -> str:
    """Build a judge prompt for scoring transfer of a principle to a target.

    The prompt asks for a 1-5 transfer score with reasoning.
    No cluster, similarity, or group information is included.
    """
    title = target.get("title", "")
    one_liner = target.get("one_liner", "")
    description = target.get("description", "")

    return f"""You are evaluating whether a coding principle applies to a specific lesson.

PRINCIPLE: "{principle}"

TARGET LESSON:
  Title: {title}
  Summary: {one_liner}
  Description: {description}

Score how well this principle applies to the target lesson on a 1-5 scale:
  1 = Does not apply at all — different problem domain, different mechanism
  2 = Tangentially related but principle doesn't address this lesson's core issue
  3 = Somewhat applicable — overlapping concerns but not a direct match
  4 = Clearly applies — principle addresses the same type of problem
  5 = Perfect match — principle directly describes this lesson's failure/solution

Return JSON: {{"transfer": <1-5>, "reasoning": "<1-2 sentences explaining your score>"}}"""


def parse_judge_response(text: str) -> dict:
    """Parse judge response into {transfer, reasoning, judge_reasoning, error}.

    Handles: JSON responses, think blocks, fallback digit extraction.
    On parse failure: transfer=1 (conservative), error="parse_failed".
    """
    judge_reasoning = None
    error = None

    # Extract and remove think blocks
    think_match = _THINK_RE.search(text)
    if think_match:
        judge_reasoning = think_match.group(1).strip()
        text = _THINK_RE.sub("", text).strip()

    # Try JSON extraction
    json_match = _JSON_RE.search(text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            transfer = data.get("transfer")
            if isinstance(transfer, (int, float)):
                transfer = max(1, min(5, int(transfer)))
                return {
                    "transfer": transfer,
                    "reasoning": data.get("reasoning", ""),
                    "judge_reasoning": judge_reasoning,
                    "error": None,
                }
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: extract standalone 1-5 digit
    score_match = _SCORE_RE.search(text)
    if score_match:
        return {
            "transfer": int(score_match.group(1)),
            "reasoning": text[:200],
            "judge_reasoning": judge_reasoning,
            "error": None,
        }

    # Total parse failure — conservative score
    return {
        "transfer": 1,
        "reasoning": text[:200] if text else "",
        "judge_reasoning": judge_reasoning,
        "error": "parse_failed",
    }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_judge.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/judge.py tests/test_forge_judge.py
git commit -m "feat(forge): add judge module — prompt building + response parsing"
```

---

## Batch 4: Oracle + Calibration

**PRD:** The oracle re-scores a sample of judge results using a stronger LLM. Kappa measures agreement. Isotonic regression calibrates the judge's scale to match the oracle's. These two modules make the judge trustworthy.

### Task 7: Oracle scoring and kappa computation

**Files:**

- Create: `ollama_queue/forge/oracle.py`
- Test: `tests/test_forge_oracle.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_oracle.py
"""Tests for Forge oracle — scoring, kappa, per-group breakdown."""
from ollama_queue.forge.oracle import (
    compute_kappa,
    select_oracle_sample,
    compute_per_group_kappa,
)


def test_compute_kappa_perfect_agreement():
    judge = [1, 2, 3, 4, 5]
    oracle = [1, 2, 3, 4, 5]
    k = compute_kappa(judge, oracle, tolerance=0)
    assert k == 1.0


def test_compute_kappa_within_tolerance():
    judge =  [1, 2, 3, 4, 5]
    oracle = [2, 3, 4, 5, 5]  # all within 1
    k = compute_kappa(judge, oracle, tolerance=1)
    assert k == 1.0


def test_compute_kappa_no_agreement():
    judge =  [1, 1, 1, 1, 1]
    oracle = [5, 5, 5, 5, 5]
    k = compute_kappa(judge, oracle, tolerance=0)
    assert k < 0  # worse than chance


def test_compute_kappa_empty():
    assert compute_kappa([], [], tolerance=1) == 0.0


def test_select_oracle_sample_respects_fraction():
    results = [{"id": i, "judge_score": 3} for i in range(100)]
    sample = select_oracle_sample(results, fraction=0.2, budget=50, seed=42)
    assert len(sample) == 20  # 0.2 * 100 = 20, under budget


def test_select_oracle_sample_respects_budget():
    results = [{"id": i, "judge_score": 3} for i in range(100)]
    sample = select_oracle_sample(results, fraction=0.5, budget=10, seed=42)
    assert len(sample) == 10  # 0.5 * 100 = 50, capped to budget=10


def test_select_oracle_sample_deterministic():
    results = [{"id": i, "judge_score": 3} for i in range(50)]
    a = select_oracle_sample(results, fraction=0.2, budget=20, seed=42)
    b = select_oracle_sample(results, fraction=0.2, budget=20, seed=42)
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_compute_per_group_kappa():
    results = [
        {"group": "async", "judge_score": 4, "oracle_score": 4},
        {"group": "async", "judge_score": 3, "oracle_score": 3},
        {"group": "async", "judge_score": 2, "oracle_score": 2},
        {"group": "error", "judge_score": 4, "oracle_score": 1},
        {"group": "error", "judge_score": 3, "oracle_score": 1},
        {"group": "error", "judge_score": 2, "oracle_score": 1},
    ]
    breakdown = compute_per_group_kappa(results, tolerance=1)
    assert "async" in breakdown
    assert "error" in breakdown
    assert breakdown["async"]["kappa"] == 1.0  # perfect
    assert breakdown["error"]["kappa"] < 0.5   # poor


def test_compute_per_group_kappa_no_groups():
    results = [
        {"judge_score": 4, "oracle_score": 4},
        {"judge_score": 3, "oracle_score": 3},
    ]
    breakdown = compute_per_group_kappa(results, tolerance=1)
    assert breakdown == {}  # no group field = no breakdown
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_oracle.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/oracle.py
"""Forge oracle — cross-validation with a stronger LLM.

The oracle re-scores a sample of judge results. Cohen's kappa measures
agreement. Per-group breakdown available when group labels exist.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

# Uniform 5-class expected agreement
_PE_UNIFORM = 0.2


def compute_kappa(
    judge_scores: list[int],
    oracle_scores: list[int],
    *,
    tolerance: int = 1,
) -> float:
    """Cohen's kappa with optional tolerance window.

    tolerance=0: exact agreement only.
    tolerance=1: scores within 1 point count as agreement (default).
    """
    n = len(judge_scores)
    if n == 0:
        return 0.0

    agree = sum(1 for j, o in zip(judge_scores, oracle_scores) if abs(j - o) <= tolerance)
    po = agree / n
    pe = _PE_UNIFORM
    if pe >= 1.0:
        return 0.0
    return (po - pe) / (1 - pe)


def select_oracle_sample(
    results: list[dict],
    *,
    fraction: float = 0.2,
    budget: int = 20,
    seed: int | None = None,
) -> list[dict]:
    """Select a sample of judge results for oracle validation.

    Takes min(ceil(len * fraction), budget) results.
    Deterministic when seed is provided.
    """
    n = min(math.ceil(len(results) * fraction), budget)
    n = min(n, len(results))
    if n <= 0:
        return []
    rng = random.Random(seed)
    return rng.sample(results, n)


def compute_per_group_kappa(
    results: list[dict],
    *,
    tolerance: int = 1,
) -> dict[str, dict]:
    """Compute kappa per group label. Returns {} if no groups present.

    Each result must have judge_score and oracle_score.
    Group field is optional — results without it are skipped.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        group = r.get("group")
        if group and r.get("oracle_score") is not None:
            groups[group].append(r)

    breakdown = {}
    for group, items in groups.items():
        if len(items) < 2:
            continue
        judge = [r["judge_score"] for r in items]
        oracle = [r["oracle_score"] for r in items]
        kappa = compute_kappa(judge, oracle, tolerance=tolerance)
        breakdown[group] = {
            "kappa": round(kappa, 4),
            "pairs": len(items),
        }

    return breakdown
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_oracle.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/oracle.py tests/test_forge_oracle.py
git commit -m "feat(forge): add oracle module — kappa computation + per-group breakdown"
```

---

### Task 8: Calibrator module (isotonic regression)

**Files:**

- Create: `ollama_queue/forge/calibrator.py`
- Test: `tests/test_forge_calibrator.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_calibrator.py
"""Tests for Forge calibrator — isotonic regression judge→oracle mapping."""
from ollama_queue.forge.calibrator import fit_calibration, apply_calibration


def test_fit_calibration_identity():
    """When judge and oracle agree, calibration is identity-ish."""
    judge =  [1, 2, 3, 4, 5]
    oracle = [1, 2, 3, 4, 5]
    cal = fit_calibration(judge, oracle)
    assert cal is not None
    # Calibrated 3 should be close to 3
    assert abs(apply_calibration(cal, 3) - 3.0) < 0.5


def test_fit_calibration_bias_correction():
    """Judge consistently scores 1 higher than oracle."""
    judge =  [2, 3, 4, 5, 5]
    oracle = [1, 2, 3, 4, 4]
    cal = fit_calibration(judge, oracle)
    # Calibrated judge=4 should be closer to oracle=3
    calibrated = apply_calibration(cal, 4)
    assert calibrated < 4.0


def test_fit_calibration_too_few_pairs():
    """Returns None when fewer than 10 pairs."""
    judge = [1, 2, 3]
    oracle = [1, 2, 3]
    cal = fit_calibration(judge, oracle)
    assert cal is None


def test_apply_calibration_none_returns_raw():
    """When no calibration model, return raw score."""
    assert apply_calibration(None, 3) == 3.0


def test_fit_calibration_monotonic():
    """Calibrated scores should be monotonically non-decreasing."""
    judge =  [1, 1, 2, 3, 3, 4, 4, 5, 5, 5, 2, 3]
    oracle = [1, 2, 2, 2, 3, 4, 3, 5, 4, 5, 1, 4]
    cal = fit_calibration(judge, oracle)
    calibrated = [apply_calibration(cal, s) for s in range(1, 6)]
    for i in range(len(calibrated) - 1):
        assert calibrated[i] <= calibrated[i + 1] + 1e-6


def test_calibration_serialization():
    """Calibration model can be serialized to JSON."""
    judge =  [1, 2, 2, 3, 3, 4, 4, 5, 5, 5]
    oracle = [1, 1, 2, 3, 3, 4, 4, 5, 4, 5]
    cal = fit_calibration(judge, oracle)
    import json
    serialized = json.dumps(cal)
    deserialized = json.loads(serialized)
    assert abs(apply_calibration(deserialized, 3) - apply_calibration(cal, 3)) < 1e-6
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_calibrator.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/calibrator.py
"""Forge calibrator — isotonic regression maps judge scores to oracle scale.

Fits a monotonic function from judge→oracle scores so that calibrated
scores reflect the oracle's ground truth. Requires ≥10 pairs.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_MIN_PAIRS = 10


def fit_calibration(
    judge_scores: list[int],
    oracle_scores: list[int],
) -> dict | None:
    """Fit isotonic regression from judge scores to oracle scores.

    Returns a serializable dict {x_thresholds, y_values} or None if
    fewer than _MIN_PAIRS. The model is a piecewise-constant function:
    for input x, find the largest x_threshold ≤ x and return y_value.
    """
    if len(judge_scores) < _MIN_PAIRS:
        _log.info("calibrator: only %d pairs, need %d — skipping", len(judge_scores), _MIN_PAIRS)
        return None

    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        _log.warning("calibrator: scikit-learn not installed — skipping")
        return None

    ir = IsotonicRegression(y_min=1.0, y_max=5.0, out_of_bounds="clip")
    ir.fit(judge_scores, oracle_scores)

    return {
        "x_thresholds": ir.X_thresholds_.tolist(),
        "y_values": ir.y_thresholds_.tolist(),
    }


def apply_calibration(cal: dict | None, judge_score: int | float) -> float:
    """Apply calibration model to a judge score. Returns calibrated float.

    If cal is None, returns the raw score as a float.
    """
    if cal is None:
        return float(judge_score)

    thresholds = cal["x_thresholds"]
    values = cal["y_values"]

    # Binary search for the right bucket
    x = float(judge_score)
    if x <= thresholds[0]:
        return values[0]
    if x >= thresholds[-1]:
        return values[-1]

    # Find largest threshold ≤ x
    lo, hi = 0, len(thresholds) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if thresholds[mid] <= x:
            lo = mid
        else:
            hi = mid - 1

    # Linear interpolation between adjacent thresholds
    if lo < len(thresholds) - 1:
        t0, t1 = thresholds[lo], thresholds[lo + 1]
        v0, v1 = values[lo], values[lo + 1]
        if t1 > t0:
            frac = (x - t0) / (t1 - t0)
            return v0 + frac * (v1 - v0)

    return values[lo]
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_calibrator.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/calibrator.py tests/test_forge_calibrator.py
git commit -m "feat(forge): add calibrator module — isotonic regression judge→oracle"
```

---

## Batch 5: Metrics

**PRD:** Compute oracle-as-ground-truth F1, Spearman rank correlation (acquiescence diagnostic), and score variance. These replace the old cluster-based F1 entirely.

### Task 9: Metrics module

**Files:**

- Create: `ollama_queue/forge/metrics.py`
- Test: `tests/test_forge_metrics.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_metrics.py
"""Tests for Forge metrics — oracle-ground-truth F1, Spearman, variance."""
from ollama_queue.forge.metrics import (
    compute_forge_metrics,
    spearman_rank_correlation,
    score_variance,
)


def test_spearman_perfect_positive():
    assert abs(spearman_rank_correlation([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) - 1.0) < 1e-6


def test_spearman_perfect_negative():
    assert abs(spearman_rank_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) - (-1.0)) < 1e-6


def test_spearman_no_correlation():
    """Random-ish data should be near 0."""
    rho = spearman_rank_correlation([1, 2, 3, 4, 5], [3, 1, 5, 2, 4])
    assert abs(rho) < 0.5


def test_spearman_empty():
    assert spearman_rank_correlation([], []) == 0.0


def test_score_variance_all_same():
    assert score_variance([3, 3, 3, 3, 3]) == 0.0


def test_score_variance_spread():
    v = score_variance([1, 2, 3, 4, 5])
    assert v > 1.0  # should be 2.0


def test_compute_forge_metrics_basic():
    results = [
        {"judge_score": 4, "oracle_score": 4, "embedding_similarity": 0.9},
        {"judge_score": 4, "oracle_score": 5, "embedding_similarity": 0.8},
        {"judge_score": 2, "oracle_score": 1, "embedding_similarity": 0.2},
        {"judge_score": 1, "oracle_score": 1, "embedding_similarity": 0.1},
    ]
    m = compute_forge_metrics(results, positive_threshold=3)
    assert "f1" in m
    assert "precision" in m
    assert "recall" in m
    assert "kappa" in m
    assert "spearman" in m
    assert "score_variance" in m
    assert m["f1"] > 0  # should have some agreement


def test_compute_forge_metrics_perfect():
    results = [
        {"judge_score": 5, "oracle_score": 5, "embedding_similarity": 0.9},
        {"judge_score": 1, "oracle_score": 1, "embedding_similarity": 0.1},
    ]
    m = compute_forge_metrics(results, positive_threshold=3)
    assert m["f1"] == 1.0
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0


def test_compute_forge_metrics_no_oracle():
    """Results without oracle scores return partial metrics."""
    results = [
        {"judge_score": 4, "embedding_similarity": 0.9},
        {"judge_score": 2, "embedding_similarity": 0.2},
    ]
    m = compute_forge_metrics(results, positive_threshold=3)
    assert m["f1"] is None  # can't compute without oracle
    assert m["spearman"] is not None  # can compute from embeddings
    assert m["score_variance"] is not None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_metrics.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/metrics.py
"""Forge metrics — oracle-ground-truth F1, Spearman, score variance.

F1 uses oracle score as ground truth (oracle >= threshold = positive).
Spearman measures judge-embedding correlation (acquiescence diagnostic).
Variance measures score spread (all-same = acquiescing).
"""
from __future__ import annotations

import math
from ollama_queue.forge.oracle import compute_kappa


def spearman_rank_correlation(a: list, b: list) -> float:
    """Spearman's rank correlation coefficient. Returns -1.0 to 1.0."""
    n = len(a)
    if n < 2:
        return 0.0

    def _rank(vals):
        indexed = sorted(enumerate(vals), key=lambda x: x[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    ra = _rank(a)
    rb = _rank(b)

    d_sq = sum((x - y) ** 2 for x, y in zip(ra, rb))
    return 1 - (6 * d_sq) / (n * (n * n - 1))


def score_variance(scores: list[int | float]) -> float:
    """Population variance of scores. Returns 0.0 for empty/single."""
    n = len(scores)
    if n < 2:
        return 0.0
    mean = sum(scores) / n
    return sum((s - mean) ** 2 for s in scores) / n


def compute_forge_metrics(
    results: list[dict],
    *,
    positive_threshold: int = 3,
) -> dict:
    """Compute Forge metrics from scored results.

    Oracle-as-ground-truth: oracle_score >= threshold = positive class.
    Judge's job: agree with the oracle.

    Returns dict with: f1, precision, recall, kappa, spearman,
    score_variance, sample_size, oracle_sample_size.
    """
    judge_scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    sims = [r["embedding_similarity"] for r in results if r.get("embedding_similarity") is not None]

    # Always computable
    spearman = spearman_rank_correlation(judge_scores, sims) if len(judge_scores) >= 2 and len(sims) >= 2 else None
    variance = score_variance(judge_scores) if judge_scores else None

    # Oracle-dependent metrics
    oracle_pairs = [r for r in results if r.get("oracle_score") is not None and r.get("judge_score") is not None]

    if not oracle_pairs:
        return {
            "f1": None, "precision": None, "recall": None, "kappa": None,
            "spearman": round(spearman, 4) if spearman is not None else None,
            "score_variance": round(variance, 4) if variance is not None else None,
            "sample_size": len(judge_scores),
            "oracle_sample_size": 0,
        }

    # F1 with oracle as ground truth
    tp = fp = fn = tn = 0
    j_scores = []
    o_scores = []

    for r in oracle_pairs:
        j = r["judge_score"]
        o = r["oracle_score"]
        j_scores.append(j)
        o_scores.append(o)

        j_pos = j >= positive_threshold
        o_pos = o >= positive_threshold

        if j_pos and o_pos:
            tp += 1
        elif j_pos and not o_pos:
            fp += 1
        elif not j_pos and o_pos:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    kappa = compute_kappa(j_scores, o_scores, tolerance=1)

    return {
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "kappa": round(kappa, 4),
        "spearman": round(spearman, 4) if spearman is not None else None,
        "score_variance": round(variance, 4) if variance is not None else None,
        "sample_size": len(judge_scores),
        "oracle_sample_size": len(oracle_pairs),
    }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_metrics.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/metrics.py tests/test_forge_metrics.py
git commit -m "feat(forge): add metrics module — oracle-ground-truth F1, Spearman, variance"
```

---

## Batch 6: Engine (Pipeline Orchestrator)

**PRD:** The engine orchestrates the full Forge cycle: fetch items → embed → select pairs → judge → oracle → calibrate → compute metrics. Each step calls the modules built in Batches 1-5. The engine is the only module that knows about all the others.

### Task 10: Engine orchestration — pipeline core

**Files:**

- Create: `ollama_queue/forge/engine.py`
- Test: `tests/test_forge_engine.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_engine.py
"""Tests for Forge engine — pipeline orchestration."""
import json
from unittest.mock import patch, MagicMock

from ollama_queue.forge.engine import run_forge_cycle
from ollama_queue.forge.types import ForgeRunStatus


def _mock_items():
    return [
        {"id": "1", "title": "Exception swallowed", "one_liner": "Bare except", "description": "Hides errors"},
        {"id": "2", "title": "Missing await", "one_liner": "Async without await", "description": "Silently skips"},
        {"id": "3", "title": "Schema drift", "one_liner": "Producer changed", "description": "Consumer breaks"},
        {"id": "4", "title": "Cold start", "one_liner": "Works steady", "description": "Fails on restart"},
    ]


def test_run_forge_cycle_happy_path(db):
    """Full pipeline with mocked HTTP calls."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="test-judge",
        oracle_model="test-oracle",
        pairs_per_quartile=2,
        seed=42,
    )

    # Mock data source
    mock_fetch = MagicMock(return_value=_mock_items())
    # Mock embedder
    mock_embed = MagicMock(return_value={
        "1": [1.0, 0.0, 0.0],
        "2": [0.8, 0.2, 0.0],
        "3": [0.0, 1.0, 0.0],
        "4": [0.0, 0.0, 1.0],
    })
    # Mock judge LLM
    mock_judge_call = MagicMock(return_value=('{"transfer": 4, "reasoning": "good"}', {}, None))
    # Mock oracle LLM
    mock_oracle_call = MagicMock(return_value=('{"transfer": 4, "reasoning": "agree"}', {}, None))

    with patch("ollama_queue.forge.engine._fetch_items", mock_fetch), \
         patch("ollama_queue.forge.engine.embed_items", mock_embed), \
         patch("ollama_queue.forge.engine._call_judge", mock_judge_call), \
         patch("ollama_queue.forge.engine._call_oracle", mock_oracle_call):
        run_forge_cycle(db=db, run_id=run_id, http_base="http://127.0.0.1:7683")

    run = db.get_forge_run(run_id)
    assert run["status"] == "complete"
    assert run["metrics_json"] is not None
    metrics = json.loads(run["metrics_json"])
    assert "f1" in metrics
    assert "kappa" in metrics


def test_run_forge_cycle_marks_failed_on_error(db):
    """Pipeline marks run as failed on unhandled exception."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="test",
        oracle_model="test",
    )

    with patch("ollama_queue.forge.engine._fetch_items", side_effect=RuntimeError("boom")):
        run_forge_cycle(db=db, run_id=run_id, http_base="http://127.0.0.1:7683")

    run = db.get_forge_run(run_id)
    assert run["status"] == "failed"
    assert "boom" in run["error"]


def test_run_forge_cycle_respects_cancellation(db):
    """Pipeline exits early if run is cancelled mid-judge."""
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="test",
        oracle_model="test",
        pairs_per_quartile=5,
        seed=42,
    )

    call_count = 0

    def mock_judge(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            db.update_forge_run(run_id, status="cancelled")
        return ('{"transfer": 3, "reasoning": "ok"}', {}, None)

    mock_embed = MagicMock(return_value={
        "1": [1.0, 0.0], "2": [0.0, 1.0], "3": [0.5, 0.5], "4": [0.3, 0.7],
    })

    with patch("ollama_queue.forge.engine._fetch_items", return_value=_mock_items()), \
         patch("ollama_queue.forge.engine.embed_items", mock_embed), \
         patch("ollama_queue.forge.engine._call_judge", mock_judge):
        run_forge_cycle(db=db, run_id=run_id, http_base="http://127.0.0.1:7683")

    run = db.get_forge_run(run_id)
    assert run["status"] == "cancelled"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_engine.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/engine.py
"""Forge engine — orchestrates the full evaluation cycle.

Flow: fetch items → embed → select pairs → judge → oracle → calibrate → metrics.
Each step delegates to a focused module. The engine is the only module
that imports from all the others.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import httpx

from ollama_queue.forge.calibrator import apply_calibration, fit_calibration
from ollama_queue.forge.embedder import embed_items
from ollama_queue.forge.judge import build_judge_prompt, parse_judge_response
from ollama_queue.forge.metrics import compute_forge_metrics
from ollama_queue.forge.oracle import compute_kappa, compute_per_group_kappa, select_oracle_sample
from ollama_queue.forge.pairs import build_similarity_matrix, select_stratified_pairs
from ollama_queue.forge.settings import get_forge_setting

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


def _fetch_items(data_source_url: str, limit: int = 100) -> list[dict]:
    """Fetch items from a remote ForgeDataSource via HTTP."""
    resp = httpx.get(f"{data_source_url}/eval/items", params={"limit": limit}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _call_judge(*, http_base: str, model: str, prompt: str, temperature: float) -> tuple[str, dict, int | None]:
    """Call judge LLM via queue proxy."""
    from ollama_queue.eval.providers import get_provider
    provider = get_provider("ollama", http_base)
    return provider.generate(model=model, prompt=prompt, temperature=temperature)


def _call_oracle(*, http_base: str, provider_name: str, model: str, prompt: str, api_key: str | None = None) -> tuple[str, dict, int | None]:
    """Call oracle LLM via configured provider."""
    from ollama_queue.eval.providers import get_provider
    provider = get_provider(provider_name, http_base, api_key=api_key)
    return provider.generate(model=model, prompt=prompt, temperature=0.1)


def _is_cancelled(db: Database, run_id: int) -> bool:
    """Check if run has been cancelled or failed externally."""
    run = db.get_forge_run(run_id)
    if run is None:
        return True
    return run["status"] in ("cancelled", "failed")


def run_forge_cycle(*, db: Database, run_id: int, http_base: str) -> None:
    """Execute one full Forge evaluation cycle.

    Steps: embed → pair → judge → oracle → calibrate → metrics.
    Never raises — marks run as failed on error.
    """
    try:
        _run_forge_cycle_inner(db=db, run_id=run_id, http_base=http_base)
    except Exception as exc:
        _log.exception("forge engine: run %d failed: %s", run_id, exc)
        db.update_forge_run(run_id, status="failed", error=str(exc), completed_at=time.time())


def _run_forge_cycle_inner(*, db: Database, run_id: int, http_base: str) -> None:
    run = db.get_forge_run(run_id)
    if run is None:
        return

    db.update_forge_run(run_id, status="embedding", started_at=time.time())

    # --- Step 1: Fetch items ---
    items = _fetch_items(run["data_source_url"])
    if len(items) < 2:
        db.update_forge_run(run_id, status="failed", error="Need at least 2 items", completed_at=time.time())
        return

    # --- Step 2: Embed ---
    embedding_model = get_forge_setting(db, "forge.embedding_model", str)
    embeddings = embed_items(db=db, items=items, model=embedding_model, http_base=http_base)

    if len(embeddings) < 2:
        db.update_forge_run(run_id, status="failed", error="Could not embed enough items", completed_at=time.time())
        return

    # --- Step 3: Select pairs ---
    matrix = build_similarity_matrix(embeddings)
    per_q = run.get("pairs_per_quartile") or 20
    pairs = select_stratified_pairs(matrix, per_quartile=per_q, seed=run.get("seed"))

    if not pairs:
        db.update_forge_run(run_id, status="failed", error="No pairs selected", completed_at=time.time())
        return

    # --- Step 4: Judge ---
    db.update_forge_run(run_id, status="judging")
    judge_model = run["judge_model"]
    judge_temp = get_forge_setting(db, "forge.judge_temperature", float)

    # Build item lookup for prompt construction
    item_lookup = {item["id"]: item for item in items}

    for pair in pairs:
        if _is_cancelled(db, run_id):
            return

        source = item_lookup.get(pair["item_a"], {})
        target = item_lookup.get(pair["item_b"], {})

        # For now, use item text as "principle" (Phase 2 will use generated principles)
        principle = f"{source.get('title', '')}: {source.get('one_liner', '')}"

        prompt = build_judge_prompt(principle=principle, target=target)

        try:
            text, _, _ = _call_judge(
                http_base=http_base, model=judge_model,
                prompt=prompt, temperature=judge_temp,
            )
        except Exception as exc:
            _log.warning("forge judge: call failed for pair %s→%s: %s", pair["item_a"], pair["item_b"], exc)
            text = ""

        parsed = parse_judge_response(text)

        db.insert_forge_result(
            run_id=run_id,
            source_item_id=pair["item_a"],
            target_item_id=pair["item_b"],
            embedding_similarity=pair["similarity"],
            quartile=pair["quartile"],
            judge_score=parsed["transfer"],
            judge_reasoning=parsed.get("judge_reasoning") or parsed.get("reasoning"),
        )

    if _is_cancelled(db, run_id):
        return

    # --- Step 5: Oracle ---
    db.update_forge_run(run_id, status="oracle")
    results = db.get_forge_results(run_id)

    oracle_fraction = get_forge_setting(db, "forge.oracle_fraction", float)
    oracle_budget = get_forge_setting(db, "forge.oracle_budget", int)
    oracle_sample = select_oracle_sample(results, fraction=oracle_fraction, budget=oracle_budget, seed=run.get("seed"))

    oracle_model = run["oracle_model"]
    oracle_provider = get_forge_setting(db, "forge.oracle_provider", str)

    for result in oracle_sample:
        if _is_cancelled(db, run_id):
            return

        source = item_lookup.get(result["source_item_id"], {})
        target = item_lookup.get(result["target_item_id"], {})
        principle = f"{source.get('title', '')}: {source.get('one_liner', '')}"

        prompt = build_judge_prompt(principle=principle, target=target)

        try:
            text, _, _ = _call_oracle(
                http_base=http_base, provider_name=oracle_provider,
                model=oracle_model, prompt=prompt,
            )
        except Exception as exc:
            _log.warning("forge oracle: call failed for result %s: %s", result["id"], exc)
            continue

        parsed = parse_judge_response(text)
        db.update_forge_result_oracle(result["id"], oracle_score=parsed["transfer"], oracle_reasoning=parsed.get("reasoning"))

    if _is_cancelled(db, run_id):
        return

    # --- Step 6: Calibrate ---
    db.update_forge_run(run_id, status="calibrating")
    results = db.get_forge_results(run_id)

    oracle_pairs = [r for r in results if r.get("oracle_score") is not None]
    judge_scores = [r["judge_score"] for r in oracle_pairs]
    oracle_scores = [r["oracle_score"] for r in oracle_pairs]

    cal = fit_calibration(judge_scores, oracle_scores)
    cal_json = json.dumps(cal) if cal else None

    # Apply calibration to all results
    for r in results:
        if r.get("judge_score") is not None:
            calibrated = apply_calibration(cal, r["judge_score"])
            with db._lock:
                conn = db._connect()
                conn.execute(
                    "UPDATE forge_results SET calibrated_score = ? WHERE id = ?",
                    (round(calibrated, 4), r["id"]),
                )
                conn.commit()

    # --- Step 7: Metrics ---
    results = db.get_forge_results(run_id)
    threshold = get_forge_setting(db, "forge.positive_threshold", int)
    metrics = compute_forge_metrics(results, positive_threshold=threshold)

    # Oracle agreement JSON
    oracle_json = None
    if oracle_pairs:
        kappa = compute_kappa(judge_scores, oracle_scores, tolerance=1)
        oracle_json = json.dumps({
            "kappa": round(kappa, 4),
            "agreement_pct": round(sum(1 for j, o in zip(judge_scores, oracle_scores) if abs(j - o) <= 1) / len(judge_scores) * 100, 2),
            "sample_size": len(oracle_pairs),
            "oracle_model": oracle_model,
        })

    db.update_forge_run(
        run_id,
        status="complete",
        metrics_json=json.dumps(metrics),
        calibration_json=cal_json,
        oracle_json=oracle_json,
        completed_at=time.time(),
    )

    _log.info(
        "forge engine: run %d complete — F1=%s, kappa=%s, spearman=%s",
        run_id, metrics.get("f1"), metrics.get("kappa"), metrics.get("spearman"),
    )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_engine.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/engine.py tests/test_forge_engine.py
git commit -m "feat(forge): add engine module — full pipeline orchestration"
```

---

## Batch 7: API Routes

**PRD:** Wire Forge into the FastAPI app with endpoints for run management, calibration data, and settings. Follows existing `api/eval_*.py` patterns.

### Task 11: Forge run API endpoints

**Files:**

- Create: `ollama_queue/api/forge_runs.py`
- Modify: `ollama_queue/api/__init__.py` (add `forge_runs` to `register_routes`)
- Test: `tests/test_api_forge_runs.py`

**Step 1: Write the failing test**

```python
# tests/test_api_forge_runs.py
"""Tests for Forge run API endpoints."""
import json
import pytest
from fastapi.testclient import TestClient
from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_list_forge_runs_empty(client):
    c, _ = client
    resp = c.get("/api/forge/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_forge_run(client):
    c, _ = client
    resp = c.post("/api/forge/runs", json={
        "data_source_url": "http://127.0.0.1:7685",
        "variant_id": "A",
        "judge_model": "qwen3:14b",
        "oracle_model": "claude-sonnet-4-20250514",
    })
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_get_forge_run(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A", judge_model="m", oracle_model="o",
    )
    resp = c.get(f"/api/forge/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


def test_get_forge_run_not_found(client):
    c, _ = client
    resp = c.get("/api/forge/runs/9999")
    assert resp.status_code == 404


def test_cancel_forge_run(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A", judge_model="m", oracle_model="o",
    )
    db.update_forge_run(run_id, status="judging")
    resp = c.post(f"/api/forge/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert db.get_forge_run(run_id)["status"] == "cancelled"


def test_cancel_forge_run_terminal_returns_409(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A", judge_model="m", oracle_model="o",
    )
    db.update_forge_run(run_id, status="complete")
    resp = c.post(f"/api/forge/runs/{run_id}/cancel")
    assert resp.status_code == 409


def test_get_forge_run_results(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A", judge_model="m", oracle_model="o",
    )
    db.insert_forge_result(
        run_id=run_id, source_item_id="1", target_item_id="2",
        embedding_similarity=0.8, quartile="q1_likely", judge_score=4,
    )
    resp = c.get(f"/api/forge/runs/{run_id}/results")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_forge_calibration(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A", judge_model="m", oracle_model="o",
    )
    db.update_forge_run(run_id, oracle_json=json.dumps({"kappa": 0.75, "sample_size": 10}))
    resp = c.get(f"/api/forge/runs/{run_id}/calibration")
    assert resp.status_code == 200
    data = resp.json()
    assert data["oracle"]["kappa"] == 0.75
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_forge_runs.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/api/forge_runs.py
"""Forge run API endpoints — CRUD, cancel, results, calibration."""
from __future__ import annotations

import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ollama_queue.api as _api

router = APIRouter(tags=["forge"])


class CreateForgeRunRequest(BaseModel):
    data_source_url: str
    variant_id: str
    judge_model: str
    oracle_model: str
    pairs_per_quartile: int = 20
    label: str | None = None
    seed: int | None = None


@router.get("/api/forge/runs")
def list_forge_runs(limit: int = 50):
    return _api.db.list_forge_runs(limit=limit)


@router.post("/api/forge/runs", status_code=201)
def create_forge_run(req: CreateForgeRunRequest):
    run_id = _api.db.create_forge_run(
        data_source_url=req.data_source_url,
        variant_id=req.variant_id,
        judge_model=req.judge_model,
        oracle_model=req.oracle_model,
        pairs_per_quartile=req.pairs_per_quartile,
        label=req.label,
        seed=req.seed,
    )
    return {"id": run_id, "status": "queued"}


@router.get("/api/forge/runs/{run_id}")
def get_forge_run(run_id: int):
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    return run


@router.post("/api/forge/runs/{run_id}/cancel")
def cancel_forge_run(run_id: int):
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    if run["status"] in ("complete", "failed", "cancelled"):
        raise HTTPException(409, detail="Run is in a terminal state")
    _api.db.update_forge_run(run_id, status="cancelled", completed_at=time.time())
    return {"ok": True}


@router.get("/api/forge/runs/{run_id}/results")
def get_forge_run_results(run_id: int):
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    return _api.db.get_forge_results(run_id)


@router.get("/api/forge/runs/{run_id}/calibration")
def get_forge_calibration(run_id: int):
    import json
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    return {
        "oracle": json.loads(run["oracle_json"]) if run.get("oracle_json") else None,
        "calibration": json.loads(run["calibration_json"]) if run.get("calibration_json") else None,
        "metrics": json.loads(run["metrics_json"]) if run.get("metrics_json") else None,
    }
```

Add to `api/__init__.py` `register_routes()`:

```python
from ollama_queue.api import forge_runs
app.include_router(forge_runs.router)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_forge_runs.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add ollama_queue/api/forge_runs.py ollama_queue/api/__init__.py tests/test_api_forge_runs.py
git commit -m "feat(forge): add run API endpoints — CRUD, cancel, results, calibration"
```

---

### Task 12: Forge settings API endpoints

**Files:**

- Create: `ollama_queue/api/forge_settings.py`
- Modify: `ollama_queue/api/__init__.py` (add `forge_settings` router)
- Test: `tests/test_api_forge_settings.py`

**Step 1: Write the failing test**

```python
# tests/test_api_forge_settings.py
"""Tests for Forge settings API endpoints."""
import pytest
from fastapi.testclient import TestClient
from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_get_forge_settings(client):
    c, _ = client
    resp = c.get("/api/forge/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "forge.oracle_model" in data
    assert "forge.autonomy_level" in data


def test_put_forge_settings(client):
    c, db = client
    resp = c.put("/api/forge/settings", json={
        "forge.oracle_budget": "30",
        "forge.autonomy_level": "advisor",
    })
    assert resp.status_code == 200
    assert db.get_setting("forge.oracle_budget") == "30"
    assert db.get_setting("forge.autonomy_level") == "advisor"


def test_put_forge_settings_rejects_unknown_keys(client):
    c, _ = client
    resp = c.put("/api/forge/settings", json={
        "forge.unknown_key": "value",
    })
    assert resp.status_code == 400
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_forge_settings.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/api/forge_settings.py
"""Forge settings API endpoints — read/write forge.* settings."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import ollama_queue.api as _api
from ollama_queue.forge.settings import FORGE_DEFAULTS

router = APIRouter(tags=["forge"])


@router.get("/api/forge/settings")
def get_forge_settings():
    result = {}
    for key, default in FORGE_DEFAULTS.items():
        val = _api.db.get_setting(key)
        result[key] = val if val is not None else default
    return result


@router.put("/api/forge/settings")
def put_forge_settings(body: dict):
    for key in body:
        if key not in FORGE_DEFAULTS:
            raise HTTPException(400, detail=f"Unknown forge setting: {key}")
    for key, val in body.items():
        _api.db.set_setting(key, str(val))
    return {"ok": True}
```

Add to `api/__init__.py`:

```python
from ollama_queue.api import forge_settings
app.include_router(forge_settings.router)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_forge_settings.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add ollama_queue/api/forge_settings.py ollama_queue/api/__init__.py tests/test_api_forge_settings.py
git commit -m "feat(forge): add settings API endpoints"
```

---

## Batch 8: Integration Wiring + Update __init__ Re-exports

**PRD:** Final wiring: update `forge/__init__.py` re-exports, seed forge settings defaults, add forge to the SPA nav config (placeholder), and run the full test suite to confirm no regressions.

### Task 13: Final wiring and re-exports

**Files:**

- Modify: `ollama_queue/forge/__init__.py` (add re-exports for all public symbols)
- Modify: `ollama_queue/db/schema.py` (seed forge defaults in `initialize()`)

**Step 1: Update `forge/__init__.py`**

```python
# ollama_queue/forge/__init__.py
"""Forge v2 — evaluation engine with oracle-calibrated scoring."""
from ollama_queue.forge.calibrator import apply_calibration, fit_calibration
from ollama_queue.forge.embedder import content_hash, embed_items
from ollama_queue.forge.engine import run_forge_cycle
from ollama_queue.forge.judge import build_judge_prompt, parse_judge_response
from ollama_queue.forge.metrics import compute_forge_metrics, score_variance, spearman_rank_correlation
from ollama_queue.forge.oracle import compute_kappa, compute_per_group_kappa, select_oracle_sample
from ollama_queue.forge.pairs import build_similarity_matrix, cosine_similarity, select_stratified_pairs
from ollama_queue.forge.settings import FORGE_DEFAULTS, get_forge_setting
from ollama_queue.forge.types import AutonomyLevel, ForgeDataSource, ForgeResult, ForgeRunStatus, PairQuartile

__all__ = [
    "AutonomyLevel", "ForgeDataSource", "ForgeResult", "ForgeRunStatus", "PairQuartile",
    "FORGE_DEFAULTS", "get_forge_setting",
    "embed_items", "content_hash",
    "cosine_similarity", "build_similarity_matrix", "select_stratified_pairs",
    "build_judge_prompt", "parse_judge_response",
    "compute_kappa", "compute_per_group_kappa", "select_oracle_sample",
    "fit_calibration", "apply_calibration",
    "compute_forge_metrics", "spearman_rank_correlation", "score_variance",
    "run_forge_cycle",
]
```

**Step 2: Seed forge defaults in schema.py**

Add to `_seed_settings()` (or equivalent) in `db/schema.py`:

```python
# Forge v2 defaults
for key, val in (
    ("forge.oracle_provider", "claude"),
    ("forge.oracle_model", "claude-sonnet-4-20250514"),
    ("forge.oracle_budget", "20"),
    ("forge.oracle_fraction", "0.2"),
    ("forge.oracle_min_kappa", "0.6"),
    ("forge.judge_model", ""),
    ("forge.judge_provider", "ollama"),
    ("forge.judge_temperature", "0.1"),
    ("forge.pairs_per_quartile", "20"),
    ("forge.positive_threshold", "3"),
    ("forge.embedding_model", "nomic-embed-text"),
    ("forge.autonomy_level", "observer"),
    ("forge.f1_threshold", "0.7"),
    ("forge.auto_promote_min_improvement", "0.05"),
):
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        (key, str(val)),
    )
```

**Step 3: Run full test suite**

Run: `cd ~/Documents/projects/ollama-queue && python -m pytest --timeout=120 -x -q`
Expected: All existing 1965+ tests pass, plus ~60 new forge tests.

**Step 4: Commit**

```bash
git add ollama_queue/forge/__init__.py ollama_queue/db/schema.py
git commit -m "feat(forge): wire re-exports and seed default settings"
```

---

### Task 14: Verify all forge imports and run integration check

**Step 1: Verify all modules importable**

```python
# Quick smoke test — run in Python shell
from ollama_queue.forge import (
    run_forge_cycle, compute_forge_metrics, build_judge_prompt,
    compute_kappa, embed_items, select_stratified_pairs,
    ForgeDataSource, AutonomyLevel, ForgeRunStatus,
)
print("All forge imports OK")
```

**Step 2: Verify API endpoints registered**

```bash
cd ~/Documents/projects/ollama-queue
python -c "
from ollama_queue.db import Database
from ollama_queue.app import create_app
db = Database(':memory:')
db.initialize()
app = create_app(db)
routes = [r.path for r in app.routes if hasattr(r, 'path')]
forge_routes = [r for r in routes if '/forge/' in r]
print(f'Forge routes: {len(forge_routes)}')
for r in sorted(forge_routes):
    print(f'  {r}')
assert len(forge_routes) >= 6, f'Expected >= 6 forge routes, got {len(forge_routes)}'
print('API wiring OK')
"
```

**Step 3: Run full test suite one final time**

Run: `python -m pytest --timeout=120 -q`
Expected: All tests pass, 0 failures.

**Step 4: Final commit**

```bash
git add -A  # safe here — no other agents working
git commit -m "feat(forge): Phase 1 complete — Forge v2 eval engine with oracle calibration"
```

---

## Summary

| Batch | Tasks | Tests | Modules Created |
|-------|-------|-------|----------------|
| 1. Foundation | 1-3 | ~20 | types.py, settings.py, db/forge.py, schema changes |
| 2. Embedding + Pairs | 4-5 | ~12 | embedder.py, pairs.py |
| 3. Judge | 6 | ~7 | judge.py |
| 4. Oracle + Calibration | 7-8 | ~15 | oracle.py, calibrator.py |
| 5. Metrics | 9 | ~10 | metrics.py |
| 6. Engine | 10 | ~3 | engine.py |
| 7. API Routes | 11-12 | ~11 | api/forge_runs.py, api/forge_settings.py |
| 8. Wiring | 13-14 | ~2 | __init__.py updates, schema seeds |
| **Total** | **14 tasks** | **~80 tests** | **12 new files** |

**Dependency graph:**

```
types.py ← settings.py ← db/forge.py    (Batch 1: no deps)
         ← embedder.py ← pairs.py       (Batch 2: needs db)
         ← judge.py                      (Batch 3: standalone)
         ← oracle.py ← calibrator.py     (Batch 4: standalone math)
         ← metrics.py                    (Batch 5: uses oracle)
         ← engine.py                     (Batch 6: uses everything)
         ← api/forge_*.py               (Batch 7: uses engine + db)
```

Batches 2, 3, and 4 can run in parallel (no cross-dependencies).
