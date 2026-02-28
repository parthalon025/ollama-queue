# Smart Scheduling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add pin flags, priority-weighted load map, auto-suggest, and rebalance enforcement so cron jobs can claim protected time slots and the scheduler optimally places new jobs.

**Architecture:** A 48-slot 30-min load map (built by `Scheduler.load_map()`) is the single algorithm used for all three features: pinned jobs write score 999 to their slots, `suggest_time()` finds lowest-score slots, and `rebalance()` skips blocked slots when placing interval jobs. Priority adjustment is wired through the existing `update_recurring_job()` path plus a new dashboard control.

**Tech Stack:** Python 3.12, SQLite (threading.RLock, WAL), Click CLI, FastAPI, Preact 10 + @preact/signals, croniter 6.0.0

---

## Task 1: DB — add `pinned` column

**Files:**
- Modify: `ollama_queue/db.py`
- Test: `tests/test_db.py`

**Step 1: Write the failing test**

In `tests/test_db.py`, add inside the existing `TestRecurringJobs` class (or at module level):

```python
def test_pinned_column_default_false(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    rj_id = db.add_recurring_job("j1", "echo hi", interval_seconds=3600)
    rj = db.get_recurring_job(rj_id)
    assert rj["pinned"] == 0  # default unpinned

def test_add_recurring_job_with_pin(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    rj_id = db.add_recurring_job("j1", "echo hi", interval_seconds=3600, pinned=True)
    rj = db.get_recurring_job(rj_id)
    assert rj["pinned"] == 1

def test_update_recurring_job_pinned(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    rj_id = db.add_recurring_job("j1", "echo hi", interval_seconds=3600)
    db.update_recurring_job(rj_id, pinned=1)
    rj = db.get_recurring_job(rj_id)
    assert rj["pinned"] == 1
```

**Step 2: Run to verify fail**

```bash
cd ~/Documents/projects/ollama-queue
.venv/bin/pytest tests/test_db.py::test_pinned_column_default_false -v
```
Expected: FAIL — `pinned` key missing from result dict.

**Step 3: Implement**

In `ollama_queue/db.py`, in the `initialize()` CREATE TABLE for `recurring_jobs`, add the column (after `enabled`):

```sql
pinned INTEGER DEFAULT 0,
```

Also add the migration after the existing `cron_expression` migration block:

```python
try:
    conn.execute("ALTER TABLE recurring_jobs ADD COLUMN pinned INTEGER DEFAULT 0")
    conn.commit()
except Exception:
    pass  # Column already exists
```

In `add_recurring_job()`, add `pinned: bool = False` parameter and include it in the INSERT:

```python
# signature change:
def add_recurring_job(
    self,
    name: str,
    command: str,
    interval_seconds: int | None = None,
    cron_expression: str | None = None,
    pinned: bool = False,
    model: str | None = None,
    ...
) -> int:
```

Add `pinned` to the INSERT columns and values (after `max_retries`):

```python
# In the INSERT statement, add to column list:
#   ..., max_retries, pinned, created_at
# And in the values tuple:
#   ..., max_retries, 1 if pinned else 0, now,
```

In `update_recurring_job()`, add `"pinned"` to the `allowed` set.

**Step 4: Run to verify pass**

```bash
.venv/bin/pytest tests/test_db.py::test_pinned_column_default_false tests/test_db.py::test_add_recurring_job_with_pin tests/test_db.py::test_update_recurring_job_pinned -v
```
Expected: 3 PASS.

**Step 5: Full suite check**

```bash
.venv/bin/pytest tests/ -q --timeout=30
```
Expected: all pass (count increases by 3).

**Step 6: Commit**

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add pinned column to recurring_jobs"
```

---

## Task 2: Scheduler — `load_map()` method

**Files:**
- Modify: `ollama_queue/scheduler.py`
- Test: `tests/test_scheduler.py`

### What is the load map?

A list of 48 floats (indices 0–47), each representing a 30-minute slot in a 24h day.
- Slot 0 = 00:00–00:30, slot 1 = 00:30–01:00, ..., slot 47 = 23:30–00:00
- Score per slot = sum of contributions from each job scheduled near that slot
- Pinned cron job: slot at fire_time ±1 slot = **999** (hard block)
- Non-pinned cron job: slot at fire_time = `11 - priority`
- Interval job: fire times distributed across 24h window; each hit slot += `11 - priority`
- A job contributes to a slot if its fire time falls within that slot's 30-minute window

**Step 1: Write the failing test**

Add to `tests/test_scheduler.py`:

```python
class TestLoadMap:
    def test_returns_48_slots(self, db, scheduler):
        lm = scheduler.load_map()
        assert len(lm) == 48
        assert all(isinstance(s, (int, float)) for s in lm)

    def test_empty_schedule_all_zero(self, db, scheduler):
        lm = scheduler.load_map()
        assert all(s == 0 for s in lm)

    def test_pinned_cron_job_blocks_slot_and_neighbors(self, db, scheduler):
        import datetime
        # Add a pinned cron job at 06:00 (slot 12)
        db.add_recurring_job(
            "pinned", "echo hi",
            cron_expression="0 6 * * *",
            pinned=True,
            next_run=datetime.datetime(2025, 1, 1, 6, 0, 0).timestamp(),
        )
        lm = scheduler.load_map()
        # Slot 12 (06:00) and adjacent slot 11 (05:30) or 13 (06:30) should be 999
        assert lm[12] == 999 or lm[11] == 999 or lm[13] == 999

    def test_unpinned_cron_job_scores_by_priority(self, db, scheduler):
        import datetime
        db.add_recurring_job(
            "cron1", "echo hi",
            cron_expression="0 6 * * *",
            priority=3,
            pinned=False,
            next_run=datetime.datetime(2025, 1, 1, 6, 0, 0).timestamp(),
        )
        lm = scheduler.load_map()
        # Score for priority 3 = 11 - 3 = 8
        assert lm[12] == 8  # slot 12 = 06:00

    def test_interval_job_distributes_across_24h(self, db, scheduler):
        # 6h interval job should contribute to ~4 slots across 24h
        db.add_recurring_job("interval1", "echo hi", interval_seconds=6 * 3600, priority=5)
        lm = scheduler.load_map()
        nonzero = [s for s in lm if s > 0]
        assert len(nonzero) >= 3  # at least 3 slots hit
```

**Step 2: Run to verify fail**

```bash
.venv/bin/pytest tests/test_scheduler.py::TestLoadMap -v
```
Expected: FAIL — `Scheduler` has no `load_map` method.

**Step 3: Implement `load_map()`**

Add to `Scheduler` class in `ollama_queue/scheduler.py`:

```python
_SLOT_COUNT = 48        # 30-min slots across 24h
_SLOT_SECONDS = 1800    # 30 minutes per slot
_DAY_SECONDS = 86400
_PIN_SCORE = 999

def _time_to_slot(self, unix_ts: float) -> int:
    """Convert a Unix timestamp to a 30-min slot index (0–47) based on local time."""
    import datetime
    dt = datetime.datetime.fromtimestamp(unix_ts)
    seconds_in_day = dt.hour * 3600 + dt.minute * 60 + dt.second
    return (seconds_in_day % self._DAY_SECONDS) // self._SLOT_SECONDS

def load_map(self, now: float | None = None) -> list[float]:
    """Build a 48-slot priority-weighted load map for the next 24 hours.

    Slots are 30-minute windows starting at 00:00.
    Pinned cron jobs write 999 to their slot and adjacent slots (±15 min buffer).
    Non-pinned cron jobs write (11 - priority) to their slot.
    Interval jobs distribute fire times across 24h and score each hit slot.
    """
    import datetime
    from croniter import croniter

    if now is None:
        now = time.time()

    scores: list[float] = [0.0] * self._SLOT_COUNT
    rjs = [r for r in self.db.list_recurring_jobs() if r["enabled"]]

    for rj in rjs:
        priority = rj.get("priority") or 5
        job_score = 11 - priority  # priority 1 → 10, priority 10 → 1
        pinned = bool(rj.get("pinned"))
        cron_expr = rj.get("cron_expression")

        if cron_expr:
            # Find all fire times for this cron expression in the next 24h
            start_dt = datetime.datetime.fromtimestamp(now)
            c = croniter(cron_expr, start_dt)
            fire_times = []
            for _ in range(48):  # max 48 firings in 24h (every 30 min)
                nxt = c.get_next(datetime.datetime)
                if nxt.timestamp() > now + self._DAY_SECONDS:
                    break
                fire_times.append(nxt.timestamp())
            if not fire_times:
                # Job fires less than once per day — use next_run from DB
                next_run = rj.get("next_run")
                if next_run:
                    fire_times = [next_run]

            for ft in fire_times:
                slot = self._time_to_slot(ft)
                if pinned:
                    # Block this slot and its neighbors (±1 slot = ±30 min, covering ±15 min buffer)
                    for adj in [slot - 1, slot, slot + 1]:
                        scores[adj % self._SLOT_COUNT] = self._PIN_SCORE
                else:
                    scores[slot] = min(
                        self._PIN_SCORE - 1,
                        scores[slot] + job_score,
                    )

        elif rj.get("interval_seconds"):
            interval = rj["interval_seconds"]
            # How many times does this job fire in 24h?
            firings_per_day = max(1, self._DAY_SECONDS // interval)
            # Distribute evenly across 24h
            for i in range(firings_per_day):
                offset = (i * interval) % self._DAY_SECONDS
                slot = int(offset // self._SLOT_SECONDS) % self._SLOT_COUNT
                if scores[slot] < self._PIN_SCORE:
                    scores[slot] = min(self._PIN_SCORE - 1, scores[slot] + job_score)

    return scores
```

**Step 4: Run to verify pass**

```bash
.venv/bin/pytest tests/test_scheduler.py::TestLoadMap -v
```
Expected: all TestLoadMap tests pass.

**Step 5: Full suite**

```bash
.venv/bin/pytest tests/ -q --timeout=30
```

**Step 6: Commit**

```bash
git add ollama_queue/scheduler.py tests/test_scheduler.py
git commit -m "feat: add load_map() — 48-slot priority-weighted schedule grid"
```

---

## Task 3: Scheduler — `suggest_time()` method

**Files:**
- Modify: `ollama_queue/scheduler.py`
- Test: `tests/test_scheduler.py`

**Step 1: Write the failing test**

```python
class TestSuggestTime:
    def test_returns_list_of_suggestions(self, db, scheduler):
        suggestions = scheduler.suggest_time(priority=5)
        assert isinstance(suggestions, list)
        assert len(suggestions) >= 1

    def test_suggestions_avoid_pinned_slots(self, db, scheduler):
        import datetime
        # Pin every hour except 03:00
        for h in range(24):
            if h == 3:
                continue
            db.add_recurring_job(
                f"pinned-{h}", "echo hi",
                cron_expression=f"0 {h} * * *",
                pinned=True,
                next_run=datetime.datetime(2025, 1, 1, h, 0, 0).timestamp(),
            )
        suggestions = scheduler.suggest_time(priority=5, top_n=3)
        # All suggestions should be near 03:00 (slot 6)
        for cron_expr, score in suggestions:
            assert score < 999

    def test_suggestion_format(self, db, scheduler):
        suggestions = scheduler.suggest_time(priority=5)
        for cron_expr, score in suggestions:
            assert isinstance(cron_expr, str)
            assert isinstance(score, (int, float))
            # Should be a valid 5-field cron expression
            parts = cron_expr.split()
            assert len(parts) == 5
```

**Step 2: Run to verify fail**

```bash
.venv/bin/pytest tests/test_scheduler.py::TestSuggestTime -v
```
Expected: FAIL.

**Step 3: Implement `suggest_time()`**

Add to `Scheduler` class:

```python
def suggest_time(
    self,
    priority: int = 5,
    top_n: int = 3,
    now: float | None = None,
) -> list[tuple[str, float]]:
    """Return top_n suggested cron expressions for a new job at the given priority.

    Returns list of (cron_expression, load_score) tuples, lowest score first.
    Excludes slots with score >= _PIN_SCORE (pinned blocks).
    """
    if now is None:
        now = time.time()
    scores = self.load_map(now)
    # Build (score, slot_index) pairs, excluding hard blocks
    candidates = [
        (scores[i], i)
        for i in range(self._SLOT_COUNT)
        if scores[i] < self._PIN_SCORE
    ]
    # Sort by score ascending, then slot index (prefer earlier in day on ties)
    candidates.sort(key=lambda x: (x[0], x[1]))
    results = []
    for score, slot in candidates[:top_n]:
        # Convert slot index to HH:MM cron expression
        total_minutes = slot * 30
        hour = total_minutes // 60
        minute = total_minutes % 60
        cron_expr = f"{minute} {hour} * * *"
        results.append((cron_expr, score))
    return results
```

**Step 4: Run to verify pass**

```bash
.venv/bin/pytest tests/test_scheduler.py::TestSuggestTime -v
```

**Step 5: Full suite**

```bash
.venv/bin/pytest tests/ -q --timeout=30
```

**Step 6: Commit**

```bash
git add ollama_queue/scheduler.py tests/test_scheduler.py
git commit -m "feat: add suggest_time() — top-N optimal slot suggestions by priority"
```

---

## Task 4: Scheduler — rebalance with pin enforcement

**Files:**
- Modify: `ollama_queue/scheduler.py`
- Test: `tests/test_scheduler.py`

**Step 1: Write the failing test**

```python
class TestRebalancePinEnforcement:
    def test_rebalance_avoids_pinned_slots(self, db, scheduler):
        import datetime
        now = datetime.datetime(2025, 1, 1, 0, 0, 0).timestamp()

        # Pin a cron job at 06:00 (slot 12)
        db.add_recurring_job(
            "pinned-aria", "aria run",
            cron_expression="0 6 * * *",
            pinned=True,
            next_run=datetime.datetime(2025, 1, 1, 6, 0, 0).timestamp(),
        )
        # Add a 24h interval job
        db.add_recurring_job("daily-sync", "sync run", interval_seconds=86400)

        scheduler.rebalance(now)

        rj = db.get_recurring_job_by_name("daily-sync")
        placed_slot = scheduler._time_to_slot(rj["next_run"])
        # Should not land on slot 11, 12, or 13 (06:00 ± buffer)
        assert placed_slot not in {11, 12, 13}, \
            f"Interval job landed on blocked slot {placed_slot}"

    def test_rebalance_logs_skipped_conflict(self, db, scheduler):
        import datetime
        now = datetime.datetime(2025, 1, 1, 0, 0, 0).timestamp()
        # Pin all slots — force a conflict
        for h in range(24):
            db.add_recurring_job(
                f"pin-{h}", "cmd",
                cron_expression=f"0 {h} * * *",
                pinned=True,
                next_run=datetime.datetime(2025, 1, 1, h, 0, 0).timestamp(),
            )
        db.add_recurring_job("interval", "cmd", interval_seconds=3600)
        # Should not raise — just place as best as possible
        changes = scheduler.rebalance(now)
        assert isinstance(changes, list)
```

**Step 2: Run to verify fail**

```bash
.venv/bin/pytest tests/test_scheduler.py::TestRebalancePinEnforcement -v
```
Expected: FAIL (rebalance currently ignores pinned slots).

**Step 3: Implement rebalance enforcement**

Replace the `rebalance()` method body in `ollama_queue/scheduler.py`.
The key change: after computing the evenly-spaced candidate `next_run` offset, check if it lands in a pinned slot. If so, find the nearest non-blocked slot within the interval window.

```python
def rebalance(self, now: float | None = None) -> list[dict]:
    """Rebalance interval jobs, avoiding pinned cron job time slots."""
    if now is None:
        now = time.time()
    rjs = [r for r in self.db.list_recurring_jobs() if r["enabled"]]
    if not rjs:
        return []

    # Only rebalance interval jobs (cron jobs have explicit times)
    rjs = [r for r in rjs if not r.get("cron_expression") and r.get("interval_seconds")]
    if not rjs:
        return []

    # Build the load map to identify blocked slots (score >= _PIN_SCORE)
    blocked_slots = {
        i for i, s in enumerate(self.load_map(now)) if s >= self._PIN_SCORE
    }

    groups: dict[int, list[dict]] = {}
    for rj in rjs:
        groups.setdefault(rj["interval_seconds"], []).append(rj)
    for group in groups.values():
        group.sort(key=lambda r: (r["priority"], r["name"]))

    changes = []
    for interval, group in sorted(groups.items()):
        n = len(group)
        for i, rj in enumerate(group):
            old_next_run = rj["next_run"]
            # Start with even spread candidate
            candidate_offset = interval * i / n
            new_next_run = now + candidate_offset

            # Check if candidate lands in a blocked slot; if so, nudge forward
            if blocked_slots:
                for nudge in range(self._SLOT_COUNT):
                    slot = self._time_to_slot(new_next_run)
                    if slot not in blocked_slots:
                        break
                    new_next_run += self._SLOT_SECONDS  # advance by one slot (30 min)
                else:
                    _log.warning(
                        "Rebalance: all slots blocked for %r — placing at best-effort position",
                        rj["name"],
                    )

            with self.db._lock:
                conn = self.db._connect()
                conn.execute(
                    "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                    (new_next_run, rj["id"]),
                )
                conn.commit()
            change = {
                "name": rj["name"],
                "old_next_run": old_next_run,
                "new_next_run": new_next_run,
            }
            changes.append(change)
            self.db.log_schedule_event("rebalanced", recurring_job_id=rj["id"], details=change)
            _log.info(
                "Rebalanced %r: next_run shifted by %.0fs",
                rj["name"],
                new_next_run - (old_next_run or now),
            )
    return changes
```

**Step 4: Run to verify pass**

```bash
.venv/bin/pytest tests/test_scheduler.py::TestRebalancePinEnforcement -v
```

**Step 5: Full suite**

```bash
.venv/bin/pytest tests/ -q --timeout=30
```

**Step 6: Commit**

```bash
git add ollama_queue/scheduler.py tests/test_scheduler.py
git commit -m "feat: rebalance enforces pinned slot avoidance (±15 min buffer)"
```

---

## Task 5: CLI — `--pin` flag, `--at auto`, `schedule suggest`, `schedule edit`

**Files:**
- Modify: `ollama_queue/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing tests**

Add to `tests/test_cli.py`:

```python
from click.testing import CliRunner
from ollama_queue.cli import main

def test_schedule_add_pin_flag(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, [
        "--db", db_path, "schedule", "add",
        "--name", "pinned-aria",
        "--at", "23:30", "--pin",
        "--", "aria", "run"
    ])
    assert result.exit_code == 0, result.output
    assert "pinned" in result.output.lower() or "★" in result.output

def test_schedule_add_at_auto(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, [
        "--db", db_path, "schedule", "add",
        "--name", "auto-job",
        "--at", "auto", "--priority", "5",
        "--", "cmd"
    ])
    assert result.exit_code == 0, result.output
    assert "Suggested" in result.output or "cron=" in result.output

def test_schedule_suggest(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "schedule", "suggest", "--priority", "5"])
    assert result.exit_code == 0, result.output
    # Should output at least one time suggestion
    assert ":" in result.output  # e.g. "03:30"

def test_schedule_edit_priority(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    # First add a job
    runner.invoke(main, ["--db", db_path, "schedule", "add",
                         "--name", "myjob", "--interval", "1h", "--", "cmd"])
    # Then edit its priority
    result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "myjob", "--priority", "2"])
    assert result.exit_code == 0, result.output
    # Verify in list
    result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
    assert "myjob" in result.output
```

**Step 2: Run to verify fail**

```bash
.venv/bin/pytest tests/test_cli.py::test_schedule_add_pin_flag tests/test_cli.py::test_schedule_add_at_auto tests/test_cli.py::test_schedule_suggest tests/test_cli.py::test_schedule_edit_priority -v
```
Expected: FAIL.

**Step 3: Implement CLI changes**

**3a. `schedule add` — add `--pin` flag and `--at auto` mode**

In `_parse_schedule_spec()`, handle `at == "auto"` as a sentinel (don't parse as HH:MM):

```python
def _parse_schedule_spec(interval, at, cron, days, priority=5, db=None):
    # ... existing mutual-exclusion checks ...
    if at == "auto":
        if db is None:
            raise click.UsageError("--at auto requires a database context.")
        from ollama_queue.scheduler import Scheduler
        suggestions = Scheduler(db).suggest_time(priority=priority, top_n=1)
        if not suggestions:
            raise click.UsageError("No available time slots found.")
        cron_expr, score = suggestions[0]
        return None, cron_expr, score  # third return: score for display
    # ... rest of existing logic, return None as score for non-auto paths
```

Adjust callers to handle the 3-tuple. In `schedule_add`:

```python
interval_seconds, cron_expression, auto_score = _parse_schedule_spec(
    interval, at, cron, days, priority=priority, db=db
)
# ... rest of add logic ...
if auto_score is not None:
    click.echo(f"Suggested {cron_expression} (load score={auto_score:.1f}) — placed.")
```

Add `--pin` option to `schedule add`:
```python
@click.option("--pin", is_flag=True, default=False,
              help="Pin this job's time slot — other jobs rebalance around it (cron jobs only)")
```

Pass `pinned=pin` to `db.add_recurring_job()`.

In the confirmation echo, show ★ if pinned:
```python
pin_str = " ★ pinned" if pin else ""
click.echo(f"Added recurring job '{name}' (id={rj_id}) — {schedule_str}{pin_str}.")
```

In `schedule_list`, show ★ for pinned jobs in the NAME column:
```python
pin_indicator = "★ " if rj.get("pinned") else "  "
click.echo(f"{pin_indicator}{rj['name']:<18} ...")
```

**3b. New `schedule suggest` subcommand**

```python
@schedule.command("suggest")
@click.option("--priority", default=5, type=int,
              help="Priority for the hypothetical job (1=highest)")
@click.option("--top", default=3, type=int, help="Number of suggestions to show")
@click.pass_context
def schedule_suggest(ctx, priority, top):
    """Show optimal time slots for a new job at the given priority."""
    db = ctx.obj["db"]
    from ollama_queue.scheduler import Scheduler
    suggestions = Scheduler(db).suggest_time(priority=priority, top_n=top)
    if not suggestions:
        click.echo("No available slots (all blocked by pinned jobs).")
        return
    click.echo(f"Top {len(suggestions)} suggested times for priority {priority}:")
    click.echo(f"{'SLOT':<12} {'CRON':<18} SCORE")
    click.echo("-" * 40)
    for cron_expr, score in suggestions:
        # Convert cron back to HH:MM for display
        parts = cron_expr.split()
        minute, hour = int(parts[0]), int(parts[1])
        time_str = f"{hour:02d}:{minute:02d}"
        click.echo(f"{time_str:<12} {cron_expr:<18} {score:.1f}")
```

**3c. New `schedule edit` subcommand**

```python
@schedule.command("edit")
@click.argument("name")
@click.option("--priority", default=None, type=int)
@click.option("--interval", default=None, help="New interval: 6h, 30m, etc.")
@click.option("--command", default=None, help="New command string")
@click.option("--pin/--no-pin", default=None)
@click.pass_context
def schedule_edit(ctx, name, priority, interval, command, pin):
    """Edit a recurring job's fields."""
    db = ctx.obj["db"]
    rj = db.get_recurring_job_by_name(name)
    if rj is None:
        click.echo(f"Job '{name}' not found.", err=True)
        return
    fields = {}
    if priority is not None:
        fields["priority"] = priority
    if interval is not None:
        fields["interval_seconds"] = _parse_interval(interval)
    if command is not None:
        fields["command"] = command
    if pin is not None:
        fields["pinned"] = 1 if pin else 0
    if not fields:
        click.echo("Nothing to update — specify at least one option.")
        return
    db.update_recurring_job(rj["id"], **fields)
    # Re-rebalance so priority changes take effect immediately
    from ollama_queue.scheduler import Scheduler
    Scheduler(db).rebalance()
    click.echo(f"Updated '{name}': {', '.join(f'{k}={v}' for k, v in fields.items())}")
```

**Step 4: Run to verify pass**

```bash
.venv/bin/pytest tests/test_cli.py::test_schedule_add_pin_flag tests/test_cli.py::test_schedule_add_at_auto tests/test_cli.py::test_schedule_suggest tests/test_cli.py::test_schedule_edit_priority -v
```

**Step 5: Full suite**

```bash
.venv/bin/pytest tests/ -q --timeout=30
```

**Step 6: Commit**

```bash
git add ollama_queue/cli.py tests/test_cli.py
git commit -m "feat: schedule suggest, schedule edit, --pin flag, --at auto"
```

---

## Task 6: API — `pinned` field + `/api/schedule/load-map` endpoint

**Files:**
- Modify: `ollama_queue/api.py`
- Test: `tests/test_api.py`

**Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
def test_load_map_endpoint(client):
    resp = client.get("/api/schedule/load-map")
    assert resp.status_code == 200
    data = resp.json()
    assert "slots" in data
    assert len(data["slots"]) == 48
    assert all(isinstance(s, (int, float)) for s in data["slots"])

def test_create_schedule_with_pin(client):
    resp = client.post("/api/schedule", json={
        "name": "pinned-job",
        "command": "echo hi",
        "cron_expression": "0 23 * * *",
        "pinned": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["pinned"] == 1

def test_update_schedule_pin(client):
    # Create job first
    client.post("/api/schedule", json={
        "name": "job1",
        "command": "echo hi",
        "interval_seconds": 3600,
    })
    jobs = client.get("/api/schedule").json()
    rj_id = jobs[0]["id"]
    # Pin it
    resp = client.put(f"/api/schedule/{rj_id}", json={"pinned": True})
    assert resp.status_code == 200
    # Verify
    jobs = client.get("/api/schedule").json()
    assert jobs[0]["pinned"] == 1
```

Note: the `client` fixture in `tests/test_api.py` already exists (using `httpx.TestClient`). Check the existing fixture pattern and match it.

**Step 2: Run to verify fail**

```bash
.venv/bin/pytest tests/test_api.py::test_load_map_endpoint tests/test_api.py::test_create_schedule_with_pin -v
```

**Step 3: Implement**

In `ollama_queue/api.py`:

**Add `pinned` to Pydantic models:**

```python
class RecurringJobCreate(BaseModel):
    name: str
    command: str
    interval_seconds: int | None = None
    cron_expression: str | None = None
    pinned: bool = False        # ← add this
    model: str | None = None
    ...

class RecurringJobUpdate(BaseModel):
    ...
    pinned: bool | None = None  # ← add this
```

**Add load-map endpoint** (add before the `delete_schedule` endpoint):

```python
@app.get("/api/schedule/load-map")
def get_load_map():
    from ollama_queue.scheduler import Scheduler
    slots = Scheduler(db).load_map()
    return {"slots": slots, "slot_minutes": 30, "count": len(slots)}
```

**Step 4: Run to verify pass**

```bash
.venv/bin/pytest tests/test_api.py::test_load_map_endpoint tests/test_api.py::test_create_schedule_with_pin tests/test_api.py::test_update_schedule_pin -v
```

**Step 5: Full suite**

```bash
.venv/bin/pytest tests/ -q --timeout=30
```

**Step 6: Commit**

```bash
git add ollama_queue/api.py tests/test_api.py
git commit -m "feat: API — pinned field, /api/schedule/load-map endpoint"
```

---

## Task 7: Dashboard — pin toggle + priority input

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`
- No test (UI — verify by visual inspection after `npm run build`)

**Context:** The dashboard is a Preact SPA. `updateScheduleJob(id, fields)` already calls `PUT /api/schedule/{id}` and re-fetches. The `scheduleJobs` signal holds the job array.

**WARNING — JSX rule:** Never use `h` or `Fragment` as a variable name inside JSX. The JSX factory is injected as `h` by esbuild. Use `hrs`, `mins`, `pct`, etc. instead.

**Step 1: Add priority inline-edit state**

In `ScheduleTab`, add alongside `editingInterval`:
```jsx
const [editingPriority, setEditingPriority] = useState(null); // { id, value }
```

Add handler:
```jsx
async function handlePrioritySave(rjId) {
    if (!editingPriority || editingPriority.id !== rjId) return;
    const val = parseInt(editingPriority.value, 10);
    if (isNaN(val) || val < 1 || val > 10) { setEditingPriority(null); return; }
    try {
        await updateScheduleJob(rjId, { priority: val });
    } catch (e) {
        console.error('Priority update failed:', e);
        setRunError('Failed to update priority');
    }
    setEditingPriority(null);
}
```

**Step 2: Add pin toggle handler**

```jsx
async function handlePinToggle(rj) {
    try {
        await updateScheduleJob(rj.id, { pinned: !rj.pinned });
    } catch (e) {
        console.error('Pin toggle failed:', e);
        setRunError(`Failed to toggle pin for "${rj.name}"`);
    }
}
```

**Step 3: Update the table headers**

Change the `['Name', 'Tag', 'Interval', 'Priority', 'Next Run', 'Enabled', '']` array to:
```jsx
['Name', 'Tag', 'Schedule', 'Priority', 'Next Run', '★', 'Enabled', '']
```

**Step 4: Update the row to show schedule (cron or interval), pin toggle, and priority input**

In the row's "Interval" cell, rename to "Schedule" and display `rj.cron_expression || formatInterval(rj.interval_seconds)`.

Change the priority `<td>` from a static badge to an inline-editable number:

```jsx
<td style={{ textAlign: 'center' }}>
    {editingPriority && editingPriority.id === rj.id ? (
        <input
            type="number" min="1" max="10"
            value={editingPriority.value}
            onInput={ev => setEditingPriority({ id: rj.id, value: ev.target.value })}
            onBlur={() => handlePrioritySave(rj.id)}
            onKeyDown={ev => {
                if (ev.key === 'Enter') handlePrioritySave(rj.id);
                if (ev.key === 'Escape') setEditingPriority(null);
            }}
            ref={el => el && el.focus()}
            style={{ width: '3rem', textAlign: 'center',
                     fontFamily: 'var(--font-mono)',
                     background: 'var(--bg-inset)',
                     color: 'var(--text-primary)',
                     border: '1px solid var(--accent)',
                     borderRadius: 'var(--radius)', padding: '0.1rem 0.2rem' }}
        />
    ) : (
        <span
            style={{ background: color, color: 'var(--accent-text)',
                     padding: '0.1rem 0.5rem', borderRadius: 'var(--radius)',
                     fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)',
                     fontWeight: 600, cursor: 'pointer',
                     borderBottom: '1px dashed var(--accent-text)' }}
            title="Click to edit priority (1=highest, 10=lowest)"
            onClick={() => setEditingPriority({ id: rj.id, value: String(rj.priority) })}>
            {cat} ({rj.priority})
        </span>
    )}
</td>
```

Add pin toggle cell after priority:
```jsx
<td style={{ textAlign: 'center' }}>
    <button
        title={rj.pinned ? "Pinned — click to unpin" : "Click to pin this time slot"}
        onClick={() => handlePinToggle(rj)}
        style={{
            background: 'none', border: 'none', cursor: 'pointer',
            fontSize: '1.1rem',
            color: rj.pinned ? 'var(--status-warning)' : 'var(--text-tertiary)',
            opacity: rj.pinned ? 1 : 0.4,
        }}>
        ★
    </button>
</td>
```

**Step 5: Also update TimelineBar to show pinned jobs with a different marker**

In `TimelineBar`, change the marker for pinned jobs:
```jsx
<div key={rj.id}
     title={`${rj.pinned ? '★ PINNED: ' : ''}${rj.name} — ${formatCountdown(rj.next_run)}`}
     style={{
         position: 'absolute', left: `${pct}%`,
         width: rj.pinned ? 5 : 3,
         top: rj.pinned ? 0 : 4,
         bottom: rj.pinned ? 0 : 4,
         background: color,
         opacity: rj.pinned ? 1.0 : 0.75,
         borderRadius: rj.pinned ? 0 : 2,
     }} />
```

**Step 6: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build
```
Expected: build succeeds with no errors. Restart `ollama-queue serve` and open `/queue/ui/` — verify ★ toggles and priority editing work.

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx \
        ollama_queue/dashboard/spa/dist/
git commit -m "feat: dashboard — pin toggle and priority inline edit on Schedule tab"
```

---

## Task 8: Final verification

**Step 1: Run full test suite**

```bash
cd ~/Documents/projects/ollama-queue
.venv/bin/pytest tests/ -q --timeout=30
```
Expected: all tests pass (target: 140+).

**Step 2: Smoke test CLI end-to-end**

```bash
# Pin a job at 23:30
ollama-queue schedule add --name aria-full --at 23:30 --pin -- aria run

# Auto-suggest for a lower-priority job
ollama-queue schedule add --name notion-sync --at auto --priority 7 -- notion-sync run

# Check the schedule — aria-full should show ★
ollama-queue schedule list

# Get raw suggestions
ollama-queue schedule suggest --priority 5

# Edit priority
ollama-queue schedule edit aria-full --priority 2

# Rebalance (should respect pinned slot)
ollama-queue schedule rebalance
```

**Step 3: Restart service and verify API**

```bash
systemctl --user restart ollama-queue
curl http://127.0.0.1:7683/api/schedule/load-map | python3 -m json.tool | head -10
```
Expected: JSON with `slots` array of 48 numbers.

**Step 4: Final commit**

```bash
git add -p  # stage only ollama-queue files
git commit -m "feat: smart scheduling complete — pin, auto-suggest, priority-aware rebalance"
```
