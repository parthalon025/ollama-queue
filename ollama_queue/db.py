"""SQLite database layer for ollama-queue.

Plain English: The queue's filing cabinet. Every job, setting, health reading,
and schedule lives in a single SQLite file (~/.local/share/ollama-queue/queue.db).
All other modules read and write through this one — nothing talks to disk directly
except here.

Decision it drives: What data persists across restarts, and how long is it kept?
"""

import json
import logging
import sqlite3
import threading
import time

_log = logging.getLogger(__name__)

DEFAULTS = {
    "poll_interval_seconds": 5,
    "ram_pause_pct": 85,
    "ram_resume_pct": 75,
    "vram_pause_pct": 90,
    "vram_resume_pct": 80,
    "load_pause_multiplier": 2.0,
    "load_resume_multiplier": 1.5,
    "swap_pause_pct": 50,
    "swap_resume_pct": 40,
    "yield_to_interactive": True,
    "health_log_retention_days": 7,
    "job_log_retention_days": 30,
    "duration_stats_retention_days": 90,
    "default_timeout_seconds": 600,
    "default_priority": 5,
    "default_max_retries": 0,
    "retry_backoff_base_seconds": 60,
    "retry_backoff_multiplier": 2.0,
    "retry_backoff_cap_seconds": 3600,  # max DLQ retry interval (1 hour)
    "stall_posterior_threshold": 0.8,
    "stall_action": "log",
    "stall_kill_grace_seconds": 60,
    "priority_categories": '{"critical":[1,2],"high":[3,4],"normal":[5,6],"low":[7,8],"background":[9,10]}',
    "priority_category_colors": '{"critical":"#ef4444","high":"#f97316","normal":"#3b82f6","low":"#6b7280","background":"#374151"}',  # noqa: E501
    "resource_profiles": '{"ollama":{"check_vram":true,"check_ram":true,"check_load":true},"any":{"check_vram":false,"check_ram":false,"check_load":false}}',  # noqa: E501
    "max_concurrent_jobs": 1,
    "concurrent_shadow_hours": 24,
    "vram_safety_factor": 1.3,
    # PR2: Admission & Reliability
    "cpu_offload_efficiency": 0.3,  # fraction of CPU RAM usable as VRAM substitute
    "cb_failure_threshold": 3,  # consecutive Ollama failures before circuit opens
    "cb_base_cooldown": 30,  # initial cooldown seconds when circuit opens
    "cb_max_cooldown": 600,  # maximum cooldown seconds (10 min)
    "max_queue_depth": 50,  # HTTP 429 when pending count exceeds this
    "min_model_vram_mb": 2000,  # minimum VRAM estimate when model is unknown
    "sjf_aging_factor": 3600,  # PR3: seconds of wait to halve effective duration; 0=pure SJF
    "aoi_weight": 0.3,  # PR3: fraction of scheduling score from information staleness (0=pure priority, 1=pure AoI)
    "preemption_enabled": False,  # PR4: opt-in preemption; off by default
    "preemption_window_seconds": 120,  # PR4: only preempt jobs running < N seconds
    "max_preemptions_per_job": 2,  # PR4: prevent infinite preemption loops
    "entropy_alert_window": 30,  # PR4: polls for rolling entropy baseline
    "entropy_alert_sigma": 2.0,  # PR4: std deviations for anomaly detection
    "entropy_suspend_low_priority": True,  # PR4: suspend p8-10 promotion on critical_backlog
}

EVAL_SETTINGS_DEFAULTS = {
    "eval.data_source_url": "http://127.0.0.1:7685",
    "eval.data_source_token": "",
    "eval.per_cluster": 4,
    "eval.same_cluster_targets": 2,
    "eval.diff_cluster_targets": 2,
    "eval.judge_model": "deepseek-r1:8b",
    "eval.judge_backend": "ollama",
    "eval.judge_temperature": 0.1,
    "eval.f1_threshold": 0.75,
    "eval.stability_window": 3,
    "eval.error_budget": 0.30,
    "eval.setup_complete": False,
    "eval.analysis_model": "",  # empty = fall back to judge model
    "eval.auto_promote": False,  # explicit opt-in only
    "eval.auto_promote_min_improvement": 0.05,  # min F1 delta over current production
    "eval.positive_threshold": 3,  # score_transfer >= this counts as positive for F1 calc
}


class Database:
    """Synchronous SQLite database for the ollama-queue daemon."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                # Performance hardening
                self._conn.execute("PRAGMA synchronous = NORMAL")
                self._conn.execute("PRAGMA temp_store = MEMORY")
                self._conn.execute("PRAGMA mmap_size = 536870912")  # 512MB
                self._conn.execute("PRAGMA cache_size = -64000")  # 64MB page cache
                self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
                # busy_timeout protects against cross-process contention (e.g., sqlite3 CLI,
                # migration scripts); same-process thread safety is handled by self._lock.
                self._conn.execute("PRAGMA busy_timeout = 5000")
        return self._conn

    def _add_column_if_missing(self, conn: sqlite3.Connection, table: str, col: str, defn: str) -> None:
        """ALTER TABLE … ADD COLUMN, ignoring duplicate-column errors."""
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                _log.debug("%s.%s already exists — skipping migration", table, col)
            else:
                raise

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply all incremental schema migrations (idempotent)."""
        self._add_column_if_missing(conn, "recurring_jobs", "cron_expression", "TEXT")
        self._add_column_if_missing(conn, "recurring_jobs", "pinned", "INTEGER DEFAULT 0")
        self._add_column_if_missing(conn, "jobs", "pid", "INTEGER")
        self._add_column_if_missing(conn, "jobs", "stall_signals", "TEXT")
        self._add_column_if_missing(conn, "recurring_jobs", "check_command", "TEXT")
        self._add_column_if_missing(conn, "recurring_jobs", "max_runs", "INTEGER")
        self._add_column_if_missing(conn, "recurring_jobs", "outcome_reason", "TEXT")
        self._add_column_if_missing(conn, "jobs", "last_retry_delay", "REAL")  # PR1: decorrelated jitter
        self._add_column_if_missing(conn, "recurring_jobs", "description", "TEXT")  # layman description
        self._add_column_if_missing(conn, "jobs", "preemption_count", "INTEGER DEFAULT 0")  # PR4: preemption tracking
        # Task 4: eval_runs lifecycle fields
        self._add_column_if_missing(conn, "eval_runs", "variant_id", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "label", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "cluster_id", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "scheduled_by", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "created_at", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "data_source_token", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "analysis_md", "TEXT")
        self._add_column_if_missing(conn, "eval_prompt_templates", "is_contrastive", "INTEGER DEFAULT 0")
        self._add_column_if_missing(conn, "eval_prompt_templates", "is_multi_stage", "INTEGER DEFAULT 0")
        self._add_column_if_missing(conn, "eval_results", "target_cluster_id", "TEXT")
        self._add_column_if_missing(conn, "eval_results", "source_cluster_id", "TEXT")
        # Eval V2: Bayesian fusion columns
        self._add_column_if_missing(conn, "eval_results", "score_paired_winner", "TEXT")  # 'same'/'diff'/'neither'
        self._add_column_if_missing(conn, "eval_results", "score_mechanism_match", "INTEGER")  # 0/1/NULL
        self._add_column_if_missing(conn, "eval_results", "score_embedding_sim", "REAL")
        self._add_column_if_missing(conn, "eval_results", "score_posterior", "REAL")
        self._add_column_if_missing(conn, "eval_results", "mechanism_trigger", "TEXT")
        self._add_column_if_missing(conn, "eval_results", "mechanism_target", "TEXT")
        self._add_column_if_missing(conn, "eval_results", "mechanism_fix", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "judge_mode", "TEXT DEFAULT 'rubric'")
        self._add_column_if_missing(conn, "eval_variants", "description", "TEXT")
        # Eval analysis columns (2026-03-09)
        self._add_column_if_missing(conn, "eval_results", "source_item_title", "TEXT")
        self._add_column_if_missing(conn, "eval_results", "target_item_title", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "analysis_json", "TEXT")
        # DLQ auto-reschedule columns
        self._add_column_if_missing(conn, "dlq", "auto_reschedule_count", "INTEGER DEFAULT 0")
        self._add_column_if_missing(conn, "dlq", "auto_rescheduled_at", "REAL")
        self._add_column_if_missing(conn, "dlq", "rescheduled_job_id", "INTEGER")
        self._add_column_if_missing(conn, "dlq", "rescheduled_for", "REAL")
        self._add_column_if_missing(conn, "dlq", "reschedule_reasoning", "TEXT")
        # Backfill descriptions for system variants that existed before the column was added.
        # INSERT OR IGNORE skips rows that already exist, so existing rows need an explicit UPDATE.
        _system_descriptions = {
            "A": (
                "Control config — few-shot examples anchor the output format. "
                "Smallest context window. Compare all others against this."
            ),
            "B": (
                "Asks the model to reason about why a failure happened, not just what happened. "
                "No examples — pure reasoning."
            ),
            "C": (
                "Splits each lesson into small chunks before generating. "
                "Prevents the model from losing context in long lessons."
            ),
            "D": (
                "Same causal reasoning as B but with a 14B model. "
                "Tests whether more model capacity improves principle quality."
            ),
            "E": ("Chunked input with the 14B model — combines the focused context of C " "with the capacity of D."),
            "F": (
                "Asks the model to state when the principle does NOT apply. " "Sharper scope reduces false positives."
            ),
            "G": (
                "Contrastive prompt with the 14B model. "
                "Tests whether a bigger model follows scope constraints more precisely."
            ),
            "H": (
                "Two-pass: first extract the abstract pattern, then distill a principle. "
                "Most deliberate output, slowest (2x LLM calls)."
            ),
            "M": (
                "Captures root-cause mechanisms (trigger -> failure -> consequence) "
                "instead of surface rules. Orthogonal approach."
            ),
        }
        for _var_id, _desc in _system_descriptions.items():
            conn.execute(
                "UPDATE eval_variants SET description = ? WHERE id = ? AND description IS NULL",
                (_desc, _var_id),
            )

    def initialize(self) -> None:
        """Create all tables and seed defaults."""
        with self._lock:
            conn = self._connect()
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                model TEXT,
                priority INTEGER DEFAULT 5,
                timeout INTEGER DEFAULT 600,
                source TEXT,
                status TEXT DEFAULT 'pending',
                submitted_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                exit_code INTEGER,
                outcome_reason TEXT,
                stdout_tail TEXT,
                stderr_tail TEXT,
                estimated_duration REAL,
                tag TEXT,
                max_retries INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                retry_after REAL,
                stall_detected_at REAL,
                recurring_job_id INTEGER REFERENCES recurring_jobs(id),
                resource_profile TEXT DEFAULT 'ollama'
            );

            CREATE TABLE IF NOT EXISTS duration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                model TEXT,
                duration REAL NOT NULL,
                exit_code INTEGER,
                recorded_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS health_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                ram_pct REAL,
                vram_pct REAL,
                load_avg REAL,
                swap_pct REAL,
                ollama_model TEXT,
                queue_depth INTEGER,
                daemon_state TEXT
            );

            CREATE TABLE IF NOT EXISTS daemon_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'idle',
                current_job_id INTEGER,
                paused_reason TEXT,
                paused_since REAL,
                last_poll_at REAL,
                jobs_completed_today INTEGER DEFAULT 0,
                jobs_failed_today INTEGER DEFAULT 0,
                uptime_since REAL,
                burst_regime TEXT DEFAULT 'unknown'
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS recurring_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                command TEXT NOT NULL,
                model TEXT,
                priority INTEGER DEFAULT 5,
                timeout INTEGER DEFAULT 600,
                source TEXT,
                tag TEXT,
                resource_profile TEXT DEFAULT 'ollama',
                interval_seconds INTEGER,
                cron_expression TEXT,
                next_run REAL,
                last_run REAL,
                last_job_id INTEGER,
                max_retries INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                pinned INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedule_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                recurring_job_id INTEGER REFERENCES recurring_jobs(id),
                job_id INTEGER,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS dlq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_job_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                model TEXT,
                source TEXT,
                tag TEXT,
                priority INTEGER,
                timeout INTEGER NOT NULL DEFAULT 600,
                resource_profile TEXT DEFAULT 'ollama',
                failure_reason TEXT NOT NULL,
                stdout_tail TEXT,
                stderr_tail TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 0,
                moved_at REAL NOT NULL,
                resolved_at REAL,
                resolution TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_recurring_job_id
                ON jobs (recurring_job_id) WHERE recurring_job_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS job_metrics (
                job_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                command TEXT,
                resource_profile TEXT,
                load_duration_ns INTEGER,
                prompt_eval_count INTEGER,
                prompt_eval_duration_ns INTEGER,
                eval_count INTEGER,
                eval_duration_ns INTEGER,
                total_duration_ns INTEGER,
                model_size_gb REAL,
                completed_at REAL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_job_metrics_model
                ON job_metrics(model);

            CREATE TABLE IF NOT EXISTS model_registry (
                name              TEXT PRIMARY KEY,
                size_bytes        INTEGER,
                vram_observed_mb  REAL,
                resource_profile  TEXT DEFAULT 'ollama',
                type_tag          TEXT DEFAULT 'general',
                last_seen         REAL
            );

            -- Deferrals table — tracks proactively deferred jobs
            CREATE TABLE IF NOT EXISTS deferrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                context TEXT,
                deferred_at REAL NOT NULL,
                estimated_ready_at REAL,
                scheduled_for REAL,
                scoring_snapshot TEXT,
                resumed_at REAL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_deferrals_job_id ON deferrals(job_id);

            CREATE TABLE IF NOT EXISTS model_pulls (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                model        TEXT NOT NULL,
                status       TEXT DEFAULT 'pulling',
                progress_pct REAL DEFAULT 0,
                pid          INTEGER,
                started_at   REAL,
                completed_at REAL,
                error        TEXT
            );

            CREATE TABLE IF NOT EXISTS eval_prompt_templates (
                id              TEXT PRIMARY KEY,
                label           TEXT NOT NULL,
                instruction     TEXT NOT NULL,
                format_spec     TEXT,
                examples        TEXT,
                is_chunked      INTEGER DEFAULT 0,
                is_contrastive  INTEGER DEFAULT 0,
                is_multi_stage  INTEGER DEFAULT 0,
                is_system       INTEGER DEFAULT 1,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS eval_variants (
                id                  TEXT PRIMARY KEY,
                label               TEXT NOT NULL,
                prompt_template_id  TEXT NOT NULL REFERENCES eval_prompt_templates(id),
                model               TEXT NOT NULL,
                temperature         REAL NOT NULL DEFAULT 0.6,
                num_ctx             INTEGER NOT NULL DEFAULT 8192,
                is_recommended      INTEGER DEFAULT 0,
                is_production       INTEGER DEFAULT 0,
                is_system           INTEGER DEFAULT 0,
                is_active           INTEGER DEFAULT 1,
                description         TEXT,
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS eval_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                data_source_url TEXT NOT NULL,
                data_source_token TEXT,
                variants        TEXT NOT NULL,
                variant_id      TEXT REFERENCES eval_variants(id),
                per_cluster     INTEGER NOT NULL DEFAULT 4,
                label           TEXT,
                cluster_id      TEXT,
                scheduled_by    TEXT,
                status     TEXT NOT NULL CHECK (
                    status IN ('queued','pending','generating','judging','complete','failed','cancelled')
                ),
                stage      TEXT,
                run_mode   TEXT NOT NULL DEFAULT 'batch' CHECK (
                    run_mode IN ('batch','opportunistic','fill-open-slots','scheduled')
                ),
                item_count      INTEGER,
                item_ids        TEXT,
                seed            INTEGER,
                judge_model     TEXT,
                judge_backend   TEXT CHECK (judge_backend IN ('ollama','openai')),
                error_budget    REAL DEFAULT 0.30,
                metrics         TEXT,
                winner_variant  TEXT,
                report_md       TEXT,
                error           TEXT,
                created_at      TEXT,
                started_at      TEXT,
                completed_at    TEXT,
                max_runs        INTEGER,
                max_time_s      INTEGER,
                runs_completed  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS eval_results (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                   INTEGER NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
                variant                  TEXT NOT NULL,
                source_item_id           TEXT NOT NULL,
                principle                TEXT,
                judge_reasoning          TEXT,
                target_item_id           TEXT NOT NULL,
                is_same_cluster          INTEGER NOT NULL,
                target_cluster_id        TEXT,
                source_cluster_id        TEXT,
                row_type                 TEXT NOT NULL DEFAULT 'judge',
                score_transfer           INTEGER,
                score_precision          INTEGER,
                score_action             INTEGER,
                override_score_transfer  INTEGER,
                override_score_precision INTEGER,
                override_score_action    INTEGER,
                override_reason          TEXT,
                generation_time_s        REAL,
                queue_job_id             INTEGER,
                error                    TEXT,
                UNIQUE (run_id, variant, source_item_id, target_item_id, row_type)
            );

            CREATE INDEX IF NOT EXISTS idx_eval_results_run_variant
                ON eval_results(run_id, variant);

            CREATE TABLE IF NOT EXISTS judge_attempts (
                id            TEXT PRIMARY KEY,
                run_id        INTEGER NOT NULL REFERENCES eval_runs(id),
                judge_model   TEXT NOT NULL,
                judge_backend TEXT NOT NULL,
                judge_temp    REAL,
                metrics       TEXT,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consumers (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT NOT NULL,
                type                TEXT NOT NULL,
                platform            TEXT NOT NULL,
                source_label        TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'discovered',
                streaming_confirmed INTEGER DEFAULT 0,
                streaming_suspect   INTEGER DEFAULT 0,
                is_managed_job      INTEGER DEFAULT 0,
                patch_type          TEXT,
                restart_policy      TEXT DEFAULT 'deferred',
                patch_applied       INTEGER DEFAULT 0,
                patch_path          TEXT,
                patch_snippet       TEXT,
                health_status       TEXT DEFAULT 'unknown',
                health_checked_at   INTEGER,
                request_count       INTEGER DEFAULT 0,
                last_seen           INTEGER,
                last_live_seen      INTEGER,
                detected_at         INTEGER NOT NULL,
                onboarded_at        INTEGER
            );
        """)

            self._run_migrations(conn)

            # Seed settings defaults
            now = time.time()
            for key, value in DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value), now),
                )

            # Seed eval settings defaults (JSON-encoded, consistent with DEFAULTS)
            for key, value in EVAL_SETTINGS_DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value), now),
                )

            # Seed daemon_state singleton
            conn.execute("INSERT OR IGNORE INTO daemon_state (id, state) VALUES (1, 'idle')")

            # Seed eval prompt templates and variants (inside lock so all init is atomic)
            import datetime as _dt

            self._do_seed_eval_defaults(conn, _dt.datetime.now(_dt.UTC).isoformat())

            conn.commit()

    def seed_eval_defaults(self, conn: sqlite3.Connection | None = None) -> None:
        """Seed system eval prompt templates and variants (idempotent via INSERT OR IGNORE)."""
        import datetime as _dt

        created_at = _dt.datetime.now(_dt.UTC).isoformat()

        if conn is None:
            with self._lock:
                conn = self._connect()
                self._do_seed_eval_defaults(conn, created_at)
                conn.commit()
        else:
            self._do_seed_eval_defaults(conn, created_at)
            conn.commit()

    def _do_seed_eval_defaults(self, conn: sqlite3.Connection, created_at: str) -> None:
        """Insert system eval prompt templates and variants (called inside an existing lock)."""
        # 3 system prompt templates
        templates = [
            (
                "fewshot",
                "Learn from examples first",
                "You are extracting transferable principles. Review these examples first, then extract a principle from the source lesson.",  # noqa: E501
                0,
                1,
            ),
            (
                "zero-shot-causal",
                "Figure it out",
                "You are extracting transferable principles. Reason from cause to effect: what went wrong, why, and what rule prevents it?",  # noqa: E501
                0,
                1,
            ),
            (
                "chunked",
                "Show examples in groups",
                "You are extracting transferable principles. You will receive grouped examples from the same cluster. Extract a principle that captures the shared pattern.",  # noqa: E501
                1,
                1,
            ),
        ]
        for tmpl_id, label, instruction, is_chunked, is_system in templates:
            conn.execute(
                """INSERT OR IGNORE INTO eval_prompt_templates
                   (id, label, instruction, is_chunked, is_system, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (tmpl_id, label, instruction, is_chunked, is_system, created_at),
            )

        # Contrastive template — shows same-cluster AND diff-cluster items to force specificity
        conn.execute(
            """INSERT OR IGNORE INTO eval_prompt_templates
               (id, label, instruction, is_chunked, is_contrastive, is_system, created_at)
               VALUES (?, ?, ?, 0, 1, 1, ?)""",
            (
                "contrastive",
                "Compare and distinguish",
                "You are extracting a principle that distinguishes one failure pattern from others. "
                "You will see examples from the SAME failure cluster and from DIFFERENT clusters.",
                created_at,
            ),
        )

        # Contrastive + self-critique template
        conn.execute(
            """INSERT OR IGNORE INTO eval_prompt_templates
               (id, label, instruction, is_chunked, is_contrastive, is_multi_stage, is_system, created_at)
               VALUES (?, ?, ?, 0, 1, 1, 1, ?)""",
            (
                "contrastive-multistage",
                "Compare, distinguish, then self-critique",
                "You are extracting a principle that distinguishes one failure pattern from others. "
                "You will see examples from the SAME failure cluster and from DIFFERENT clusters.",
                created_at,
            ),
        )

        # Mechanism extraction template — captures root-cause trigger/failure/consequence chains
        conn.execute(
            """INSERT OR IGNORE INTO eval_prompt_templates
               (id, label, instruction, is_chunked, is_system, created_at)
               VALUES (?, ?, ?, 0, 1, ?)""",
            (
                "mechanism",
                "Extract root-cause mechanism",
                "You are extracting the root-cause mechanism behind a failure. "
                "Identify: (1) the trigger — what condition activated the failure, "
                "(2) the failure — what went wrong, and (3) the consequence — what impact it caused. "
                "Express as a transferable rule that prevents recurrence.",
                created_at,
            ),
        )

        # System variants A-H + M
        variants = [
            (
                "A",
                "Baseline",
                "fewshot",
                "deepseek-r1:8b",
                0.7,
                4096,
                0,
                1,
                "Control config — few-shot examples anchor the output format. "
                "Smallest context window. Compare all others against this.",
            ),
            (
                "B",
                "Causal reasoning",
                "zero-shot-causal",
                "deepseek-r1:8b",
                0.6,
                8192,
                0,
                1,
                "Asks the model to reason about why a failure happened, not just what happened. "
                "No examples — pure reasoning.",
            ),
            (
                "C",
                "Grouped context",
                "chunked",
                "deepseek-r1:8b",
                0.6,
                8192,
                0,
                1,
                "Splits each lesson into small chunks before generating. "
                "Prevents the model from losing context in long lessons.",
            ),
            (
                "D",
                "Causal + large model",
                "zero-shot-causal",
                "qwen3:14b",
                0.6,
                8192,
                1,
                1,
                "Same causal reasoning as B but with a 14B model. "
                "Tests whether more model capacity improves principle quality.",
            ),
            (
                "E",
                "Grouped + large model",
                "chunked",
                "qwen3:14b",
                0.6,
                8192,
                1,
                1,
                "Chunked input with the 14B model — combines the focused context of C with the capacity of D.",
            ),
            (
                "F",
                "Contrastive",
                "contrastive",
                "deepseek-r1:8b",
                0.6,
                8192,
                1,
                1,
                "Asks the model to state when the principle does NOT apply. Sharper scope reduces false positives.",
            ),
            (
                "G",
                "Contrastive + large model",
                "contrastive",
                "qwen3:14b",
                0.6,
                8192,
                1,
                1,
                "Contrastive prompt with the 14B model. "
                "Tests whether a bigger model follows scope constraints more precisely.",
            ),
            (
                "H",
                "Contrastive + self-critique",
                "contrastive-multistage",
                "deepseek-r1:8b",
                0.6,
                8192,
                1,
                1,
                "Two-pass: first extract the abstract pattern, then distill a principle. "
                "Most deliberate output, slowest (2x LLM calls).",
            ),
            (
                "M",
                "Mechanism extraction",
                "mechanism",
                "qwen3.5:9b",
                0.6,
                8192,
                0,
                1,
                "Captures root-cause mechanisms (trigger -> failure -> consequence) "
                "instead of surface rules. Orthogonal approach.",
            ),
        ]
        for var_id, label, tmpl_id, model, temperature, num_ctx, is_recommended, is_system, description in variants:
            conn.execute(
                """INSERT OR IGNORE INTO eval_variants
                   (id, label, prompt_template_id, model, temperature, num_ctx,
                    is_recommended, is_system, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    var_id,
                    label,
                    tmpl_id,
                    model,
                    temperature,
                    num_ctx,
                    is_recommended,
                    is_system,
                    description,
                    created_at,
                ),
            )

    # --- Jobs ---

    def submit_job(
        self,
        command: str,
        model: str,
        priority: int,
        timeout: int,
        source: str,
        tag: str | None = None,
        max_retries: int = 0,
        resource_profile: str = "ollama",
        recurring_job_id: int | None = None,
    ) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """INSERT INTO jobs
                   (command, model, priority, timeout, source, submitted_at,
                    tag, max_retries, resource_profile, recurring_job_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    command,
                    model,
                    priority,
                    timeout,
                    source,
                    time.time(),
                    tag,
                    max_retries,
                    resource_profile,
                    recurring_job_id,
                ),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return cur.lastrowid

    def get_job(self, job_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_next_job(self) -> dict | None:
        """Return the highest-priority pending job for execution.

        .. deprecated::
            No longer used by the daemon (replaced by Daemon._dequeue_next_job()
            which implements SJF + aging). Retained for the proxy/embed path and
            backwards compatibility with callers outside the daemon.
        """
        with self._lock:
            conn = self._connect()
            now = time.time()
            row = conn.execute(
                """SELECT * FROM jobs
                   WHERE status = 'pending'
                   AND (retry_after IS NULL OR retry_after <= ?)
                   ORDER BY priority ASC,
                            CASE WHEN model LIKE '%embed%' OR model LIKE '%nomic%' OR model LIKE '%bge%'
                                      OR model LIKE '%mxbai%' OR model LIKE '%all-minilm%'
                                      OR command LIKE '%/api/embed%'
                                 THEN 0 ELSE 1 END ASC,
                            submitted_at ASC
                   LIMIT 1""",
                (now,),
            ).fetchone()
            return dict(row) if row else None

    def start_job(self, job_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (time.time(), job_id),
            )
            conn.commit()

    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
        outcome_reason: str | None = None,
    ) -> None:
        status = "completed" if exit_code == 0 else "failed"
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET status = ?, exit_code = ?, stdout_tail = ?, stderr_tail = ?,
                       outcome_reason = ?, completed_at = ?
                   WHERE id = ?""",
                (status, exit_code, stdout_tail, stderr_tail, outcome_reason, time.time(), job_id),
            )
            conn.commit()

    def kill_job(self, job_id: int, reason: str, stdout_tail: str = "", stderr_tail: str = "") -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET status = 'killed', outcome_reason = ?, completed_at = ?,
                       stdout_tail = ?, stderr_tail = ?
                   WHERE id = ?""",
                (reason, time.time(), stdout_tail, stderr_tail, job_id),
            )
            conn.commit()

    def get_running_jobs(self) -> list[dict]:
        """Return all jobs currently in 'running' status."""
        with self._lock:
            rows = self._connect().execute("SELECT * FROM jobs WHERE status = 'running'").fetchall()
        return [dict(r) for r in rows]

    def reset_job_to_pending(self, job_id: int) -> None:
        """Reset a job from running back to pending (orphan recovery)."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET status='pending', started_at=NULL, pid=NULL WHERE id=?",
                (job_id,),
            )
            conn.commit()

    def requeue_preempted_job(self, job_id: int) -> None:
        """Reset a preempted job to pending and increment preemption_count.

        IMPORTANT: Never touches DLQ. Preempted jobs are healthy work interrupted
        deliberately. DLQ means 'permanent failure requiring human review' — using
        it for preemption corrupts its semantic meaning and requires manual recovery.
        """
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs SET
                       status = 'pending',
                       started_at = NULL,
                       pid = NULL,
                       submitted_at = ?,
                       preemption_count = COALESCE(preemption_count, 0) + 1
                   WHERE id = ?""",
                (time.time(), job_id),
            )
            conn.commit()

    def cancel_job(self, job_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET status = 'cancelled', outcome_reason = 'user cancelled', completed_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (time.time(), job_id),
            )
            conn.commit()

    def set_job_priority(self, job_id: int, priority: int) -> bool:
        """Update priority of a pending job. Returns True if updated."""
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE jobs SET priority = ? WHERE id = ? AND status = 'pending'",
                (priority, job_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_pending_jobs(self, exclude_sentinel: bool = True) -> list[dict]:
        """Return pending jobs ordered by priority then submission time.

        Args:
            exclude_sentinel: When True (default), omits proxy sentinel jobs
                (command LIKE 'proxy:%'). Pass False only when you explicitly
                need to inspect sentinel rows (e.g. proxy tests, recovery logic).
        """
        sentinel_clause = "AND command NOT LIKE 'proxy:%'" if exclude_sentinel else ""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"""SELECT * FROM jobs
                   WHERE status = 'pending'
                     {sentinel_clause}
                   ORDER BY priority ASC, submitted_at ASC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def count_pending_jobs(self) -> int:
        """Return count of jobs currently waiting in the queue (status='pending')."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'pending' AND (retry_after IS NULL OR retry_after <= ?)",
                (time.time(),),
            ).fetchone()
            return row[0]

    def get_history(self, limit: int = 20, offset: int = 0, source: str | None = None) -> list[dict]:
        with self._lock:
            conn = self._connect()
            if source is not None:
                rows = conn.execute(
                    """SELECT * FROM jobs
                       WHERE status IN ('completed', 'failed', 'killed', 'cancelled')
                         AND source = ?
                       ORDER BY completed_at DESC
                       LIMIT ? OFFSET ?""",
                    (source, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM jobs
                       WHERE status IN ('completed', 'failed', 'killed', 'cancelled')
                       ORDER BY completed_at DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    # --- Settings ---

    def get_setting(self, key: str):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            return json.loads(row["value"])

    def set_setting(self, key: str, value) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            conn.commit()

    def get_all_settings(self) -> dict:
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: json.loads(row["value"]) for row in rows}

    def set_stall_detected(self, job_id: int, now: float, signals: dict) -> None:
        """Record stall detection timestamp and signal breakdown for a job."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET stall_detected_at = ?, stall_signals = ? WHERE id = ?",
                (now, json.dumps(signals), job_id),
            )
            conn.commit()

    # --- Duration History ---

    def record_duration(self, source: str, model: str, duration: float, exit_code: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO duration_history (source, model, duration, exit_code, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (source, model, duration, exit_code, time.time()),
            )
            conn.commit()

    def get_duration_history(self, source: str, limit: int = 5) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT * FROM duration_history
                   WHERE source = ?
                   ORDER BY recorded_at DESC
                   LIMIT ?""",
                (source, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def estimate_duration(self, source: str) -> float | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT AVG(duration) as avg_dur
                   FROM (
                       SELECT duration FROM duration_history
                       WHERE source = ? AND exit_code = 0
                       ORDER BY recorded_at DESC
                       LIMIT 5
                   )""",
                (source,),
            ).fetchone()
            if row is None or row["avg_dur"] is None:
                return None
            return row["avg_dur"]

    def estimate_duration_bulk(self, sources: list[str]) -> dict[str, float]:
        """Return mean duration per source in a single query.

        Only counts successful runs (exit_code=0). Used by SJF sort to avoid
        N separate DB queries per dequeue cycle.
        """
        if not sources:
            return {}
        with self._lock:
            conn = self._connect()
            placeholders = ",".join("?" * len(sources))
            rows = conn.execute(
                f"""SELECT source, AVG(duration) as avg_dur
                    FROM duration_history
                    WHERE source IN ({placeholders}) AND exit_code = 0
                    GROUP BY source""",
                sources,
            ).fetchall()
            return {row["source"]: row["avg_dur"] for row in rows if row["avg_dur"] is not None}

    def estimate_duration_stats(self, source: str) -> tuple[float, float] | None:
        """Return (mean, variance) from last 10 successful runs for a source.

        Uses the computational formula: Var = E[X^2] - E[X]^2
        Returns None if no history exists.
        Used by estimate_with_variance() for risk-adjusted SJF sort.
        """
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT AVG(duration) as mean_dur,
                          AVG(duration * duration) - AVG(duration) * AVG(duration) as variance
                   FROM (
                       SELECT duration FROM duration_history
                       WHERE source = ? AND exit_code = 0
                       ORDER BY recorded_at DESC
                       LIMIT 10
                   )""",
                (source,),
            ).fetchone()
            if row is None or row["mean_dur"] is None:
                return None
            return float(row["mean_dur"]), max(0.0, float(row["variance"]) if row["variance"] is not None else 0.0)

    # --- Health Log ---

    def log_health(
        self,
        ram_pct: float,
        vram_pct: float,
        load_avg: float,
        swap_pct: float,
        ollama_model: str,
        queue_depth: int,
        daemon_state: str,
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO health_log
                   (timestamp, ram_pct, vram_pct, load_avg, swap_pct, ollama_model, queue_depth, daemon_state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), ram_pct, vram_pct, load_avg, swap_pct, ollama_model, queue_depth, daemon_state),
            )
            conn.commit()

    def get_health_log(self, hours: int = 24) -> list[dict]:
        with self._lock:
            conn = self._connect()
            cutoff = time.time() - (hours * 3600)
            rows = conn.execute(
                """SELECT * FROM health_log
                   WHERE timestamp >= ?
                   ORDER BY timestamp DESC""",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Daemon State ---

    _DAEMON_STATE_FIELDS = frozenset(
        {
            "state",
            "current_job_id",
            "paused_reason",
            "paused_since",
            "last_poll_at",
            "jobs_completed_today",
            "jobs_failed_today",
            "uptime_since",
            "burst_regime",
        }
    )

    def update_daemon_state(self, **kwargs) -> None:
        if not kwargs:
            return
        unknown = set(kwargs) - self._DAEMON_STATE_FIELDS
        if unknown:
            raise ValueError(f"Unknown daemon_state fields: {unknown}")
        with self._lock:
            conn = self._connect()
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values())
            conn.execute(f"UPDATE daemon_state SET {sets} WHERE id = 1", vals)
            conn.commit()

    def get_daemon_state(self) -> dict:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM daemon_state WHERE id = 1").fetchone()
            if row is None:
                return {"state": "idle", "current_job_id": None, "paused_since": None, "paused_reason": None}
            return dict(row)

    # --- Proxy ---

    def try_claim_for_proxy(self) -> bool:
        """Claim a queue slot for a proxy /api/generate request.

        Respects max_concurrent_jobs. Returns True if claimed.
        """
        with self._lock:
            conn = self._connect()
            max_slots = int(self.get_setting("max_concurrent_jobs") or 1)
            # Count running jobs from jobs table
            running = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status = 'running'").fetchone()["cnt"]
            if running >= max_slots:
                return False
            # Block only when another proxy is already claimed (sentinel -1).
            # Real running jobs are already counted via the jobs table above.
            state = conn.execute("SELECT current_job_id FROM daemon_state WHERE id=1").fetchone()
            if state and state["current_job_id"] == -1:
                return False
            conn.execute("UPDATE daemon_state SET state='running', current_job_id=-1 WHERE id=1")
            conn.commit()
            return True

    def release_proxy_claim(self) -> None:
        """Release proxy claim back to idle."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE daemon_state SET state = 'idle', current_job_id = NULL WHERE id = 1 AND current_job_id = -1"
            )
            conn.commit()

    # --- Maintenance ---

    def prune_old_data(self) -> None:
        with self._lock:
            conn = self._connect()
            now = time.time()

            job_retention = self.get_setting("job_log_retention_days") or 30
            health_retention = self.get_setting("health_log_retention_days") or 7
            duration_retention = self.get_setting("duration_stats_retention_days") or 90

            conn.execute(
                "DELETE FROM jobs WHERE completed_at IS NOT NULL AND completed_at < ?",
                (now - job_retention * 86400,),
            )
            conn.execute(
                "DELETE FROM health_log WHERE timestamp < ?",
                (now - health_retention * 86400,),
            )
            conn.execute(
                "DELETE FROM duration_history WHERE recorded_at < ?",
                (now - duration_retention * 86400,),
            )
            conn.commit()

    # --- Recurring Jobs ---

    def add_recurring_job(
        self,
        name: str,
        command: str,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        model: str | None = None,
        priority: int = 5,
        timeout: int = 600,
        source: str | None = None,
        tag: str | None = None,
        resource_profile: str = "ollama",
        max_retries: int = 0,
        next_run: float | None = None,
        pinned: bool = False,
        check_command: str | None = None,
        max_runs: int | None = None,
        description: str | None = None,
    ) -> int:
        if interval_seconds is None and cron_expression is None:
            raise ValueError("Either interval_seconds or cron_expression must be provided")
        with self._lock:
            conn = self._connect()
            now = time.time()
            if next_run is None and cron_expression:
                import datetime

                from croniter import croniter

                start_dt = datetime.datetime.fromtimestamp(now)
                next_run = croniter(cron_expression, start_dt).get_next(datetime.datetime).timestamp()
            elif next_run is None:
                next_run = now
            cur = conn.execute(
                """INSERT INTO recurring_jobs
                   (name, command, model, priority, timeout, source, tag,
                    resource_profile, interval_seconds, cron_expression, next_run,
                    max_retries, pinned, check_command, max_runs, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    command,
                    model,
                    priority,
                    timeout,
                    source,
                    tag,
                    resource_profile,
                    interval_seconds,
                    cron_expression,
                    next_run,
                    max_retries,
                    1 if pinned else 0,
                    check_command,
                    max_runs,
                    description,
                    now,
                ),
            )
            conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_recurring_job(self, rj_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM recurring_jobs WHERE id = ?", (rj_id,)).fetchone()
            return dict(row) if row else None

    def get_recurring_job_by_name(self, name: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM recurring_jobs WHERE name = ?", (name,)).fetchone()
            return dict(row) if row else None

    def list_recurring_jobs(self) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT * FROM recurring_jobs ORDER BY priority ASC, name ASC").fetchall()
            return [dict(r) for r in rows]

    def get_due_recurring_jobs(self, now: float) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT * FROM recurring_jobs
                   WHERE enabled = 1 AND next_run <= ?
                   ORDER BY priority ASC, next_run ASC""",
                (now,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_recurring_next_run(self, rj_id: int, completed_at: float, job_id: int | None = None) -> None:
        with self._lock:
            conn = self._connect()
            rj_row = conn.execute("SELECT * FROM recurring_jobs WHERE id = ?", (rj_id,)).fetchone()
            if rj_row is None:
                _log.error("update_recurring_next_run: recurring job id=%d not found (deleted?)", rj_id)
                return
            rj = dict(rj_row)
            cron_expr = rj.get("cron_expression")
            if cron_expr:
                import datetime

                from croniter import croniter

                start_dt = datetime.datetime.fromtimestamp(completed_at)
                next_run = croniter(cron_expr, start_dt).get_next(datetime.datetime).timestamp()
            else:
                next_run = completed_at + rj["interval_seconds"]
            conn.execute(
                """UPDATE recurring_jobs
                   SET next_run = ?, last_run = ?, last_job_id = ?
                   WHERE id = ?""",
                (next_run, completed_at, job_id, rj_id),
            )
            conn.commit()

    def set_recurring_job_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            conn = self._connect()
            if enabled:
                cur = conn.execute(
                    "UPDATE recurring_jobs SET enabled = 1, outcome_reason = NULL WHERE name = ?",
                    (name,),
                )
            else:
                cur = conn.execute(
                    "UPDATE recurring_jobs SET enabled = 0 WHERE name = ?",
                    (name,),
                )
            conn.commit()
            return cur.rowcount > 0

    def disable_recurring_job(self, rj_id: int, reason: str) -> None:
        """Auto-disable a recurring job and record the reason."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE recurring_jobs SET enabled = 0, outcome_reason = ? WHERE id = ?",
                (reason, rj_id),
            )
            conn.commit()

    def delete_recurring_job(self, name: str) -> bool:
        with self._lock:
            conn = self._connect()
            rj = conn.execute("SELECT id FROM recurring_jobs WHERE name = ?", (name,)).fetchone()
            if rj is None:
                return False
            rj_id = rj["id"]
            conn.execute(
                "UPDATE jobs SET recurring_job_id = NULL WHERE recurring_job_id = ?",
                (rj_id,),
            )
            conn.execute("DELETE FROM schedule_events WHERE recurring_job_id = ?", (rj_id,))
            cur = conn.execute("DELETE FROM recurring_jobs WHERE id = ?", (rj_id,))
            conn.commit()
            return cur.rowcount > 0

    def update_recurring_job(self, rj_id: int, **fields: object) -> bool:
        """Update allowed fields on a recurring job. Returns True if found."""
        allowed = {
            "name",
            "command",
            "interval_seconds",
            "cron_expression",
            "model",
            "priority",
            "timeout",
            "source",
            "tag",
            "enabled",
            "next_run",
            "pinned",
            "max_retries",
            "check_command",
            "max_runs",
            "outcome_reason",
            "description",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        with self._lock:
            conn = self._connect()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = [*list(updates.values()), rj_id]
            cur = conn.execute(
                f"UPDATE recurring_jobs SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_recurring_job_by_id(self, rj_id: int) -> bool:
        """Delete a recurring job by ID with full cascade cleanup."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET recurring_job_id = NULL WHERE recurring_job_id = ?",
                (rj_id,),
            )
            conn.execute("DELETE FROM schedule_events WHERE recurring_job_id = ?", (rj_id,))
            cur = conn.execute("DELETE FROM recurring_jobs WHERE id = ?", (rj_id,))
            conn.commit()
            return cur.rowcount > 0

    def log_schedule_event(
        self,
        event_type: str,
        recurring_job_id: int | None = None,
        job_id: int | None = None,
        details: dict | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO schedule_events
                   (timestamp, event_type, recurring_job_id, job_id, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (time.time(), event_type, recurring_job_id, job_id, json.dumps(details) if details else None),
            )
            conn.commit()

    def get_schedule_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM schedule_events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def has_pending_or_running_recurring(self, recurring_job_id: int) -> bool:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT 1 FROM jobs
                   WHERE recurring_job_id = ? AND status IN ('pending', 'running')
                   LIMIT 1""",
                (recurring_job_id,),
            ).fetchone()
            return row is not None

    def get_last_successful_run_time(self, recurring_job_id: int) -> float | None:
        """Return timestamp of most recent successful (exit_code=0) job for a recurring job.

        Uses exit_code=0 (not last_run which includes failures) for AoI accuracy.
        Returns None if the recurring job has never completed successfully.
        """
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT MAX(completed_at) as last_success
                   FROM jobs
                   WHERE recurring_job_id = ? AND exit_code = 0""",
                (recurring_job_id,),
            ).fetchone()
            if row is None or row["last_success"] is None:
                return None
            return float(row["last_success"])

    def _set_recurring_next_run(self, rj_id: int, next_run: float) -> None:
        """Update next_run for a recurring job. Single-purpose DB API — no direct _connect() outside this class."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                (next_run, rj_id),
            )
            conn.commit()

    def batch_set_recurring_next_runs(self, updates: dict[int, float]) -> None:
        """Update next_run for multiple recurring jobs in a single transaction.

        Accepts a mapping of {recurring_job_id: next_run_timestamp}.
        Uses executemany to reduce round-trips; all rows commit atomically.
        No-op if updates is empty.
        """
        if not updates:
            return
        with self._lock:
            conn = self._connect()
            conn.executemany(
                "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                [(next_run, rj_id) for rj_id, next_run in updates.items()],
            )
            conn.commit()

    def _set_job_retry(self, job_id: int, retry_after: float, delay: float) -> None:
        """Atomically increment retry_count, reset status, and set retry timing."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET retry_count = retry_count + 1,
                       retry_after = ?,
                       last_retry_delay = ?,
                       status = 'pending'
                   WHERE id = ?""",
                (retry_after, delay, job_id),
            )
            conn.commit()

    # --- DLQ ---

    def move_to_dlq(self, job_id: int, failure_reason: str) -> int | None:
        with self._lock:
            conn = self._connect()
            job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job_row:
                return None
            job = dict(job_row)
            cur = conn.execute(
                """INSERT INTO dlq
                   (original_job_id, command, model, source, tag, priority,
                    timeout, resource_profile, failure_reason, stdout_tail, stderr_tail,
                    retry_count, max_retries, moved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    job["command"],
                    job["model"],
                    job["source"],
                    job.get("tag"),
                    job["priority"],
                    job.get("timeout", 600),
                    job.get("resource_profile", "ollama"),
                    failure_reason,
                    job.get("stdout_tail", ""),
                    job.get("stderr_tail", ""),
                    job.get("retry_count", 0),
                    job.get("max_retries", 0),
                    time.time(),
                ),
            )
            conn.execute(
                "UPDATE jobs SET status = 'dead', completed_at = ? WHERE id = ?",
                (time.time(), job_id),
            )
            conn.commit()
            return cur.lastrowid

    def get_dlq_entry(self, dlq_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM dlq WHERE id = ?", (dlq_id,)).fetchone()
            return dict(row) if row else None

    def list_dlq(self, include_resolved: bool = False) -> list[dict]:
        with self._lock:
            conn = self._connect()
            if include_resolved:
                rows = conn.execute("SELECT * FROM dlq ORDER BY moved_at DESC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM dlq WHERE resolution IS NULL ORDER BY moved_at DESC").fetchall()
            return [dict(r) for r in rows]

    def dismiss_dlq_entry(self, dlq_id: int) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE dlq SET resolution = 'dismissed', resolved_at = ? WHERE id = ?",
                (time.time(), dlq_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def retry_dlq_entry(self, dlq_id: int) -> int | None:
        with self._lock:
            conn = self._connect()
            entry_row = conn.execute("SELECT * FROM dlq WHERE id = ?", (dlq_id,)).fetchone()
            if not entry_row:
                return None
            entry = dict(entry_row)
            # M2: guard against already-resolved entries
            if entry.get("resolution") is not None:
                return None
            cur = conn.execute(
                """INSERT INTO jobs
                   (command, model, priority, timeout, source, submitted_at,
                    tag, max_retries, resource_profile, recurring_job_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["command"],
                    entry["model"],
                    entry["priority"] or 5,
                    entry.get("timeout") or 600,
                    entry["source"] or "dlq-retry",
                    time.time(),
                    entry.get("tag"),
                    entry.get("max_retries", 0),
                    entry.get("resource_profile", "ollama"),
                    None,
                ),
            )
            new_job_id = cur.lastrowid
            conn.execute(
                """UPDATE dlq SET resolution = 'retried', resolved_at = ?,
                   retry_count = retry_count + 1 WHERE id = ?""",
                (time.time(), dlq_id),
            )
            conn.commit()
            assert new_job_id is not None
            return new_job_id

    def clear_dlq(self) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM dlq WHERE resolution IS NOT NULL")
            conn.commit()
            return cur.rowcount

    # ── job_metrics CRUD ─────────────────────────────────────────────

    def store_job_metrics(self, job_id: int, metrics: dict) -> None:
        """INSERT OR REPLACE a row in job_metrics from a dict of Ollama response fields."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR REPLACE INTO job_metrics
                   (job_id, model, command, resource_profile,
                    load_duration_ns, prompt_eval_count, prompt_eval_duration_ns,
                    eval_count, eval_duration_ns, total_duration_ns,
                    model_size_gb, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    metrics.get("model", ""),
                    metrics.get("command"),
                    metrics.get("resource_profile"),
                    metrics.get("load_duration_ns"),
                    metrics.get("prompt_eval_count"),
                    metrics.get("prompt_eval_duration_ns"),
                    metrics.get("eval_count"),
                    metrics.get("eval_duration_ns"),
                    metrics.get("total_duration_ns"),
                    metrics.get("model_size_gb"),
                    metrics.get("completed_at", time.time()),
                ),
            )
            conn.commit()

    def get_job_metrics(self, job_id: int) -> dict | None:
        """Return the job_metrics row as a dict, or None if not found."""
        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM job_metrics WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def get_tok_per_min(self, model: str) -> list[float]:
        """Derive tok/min from eval_count and eval_duration_ns for recent jobs."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT eval_count, eval_duration_ns FROM job_metrics
                   WHERE model = ? AND eval_count IS NOT NULL
                     AND eval_duration_ns IS NOT NULL AND eval_duration_ns > 0
                   ORDER BY completed_at DESC LIMIT 50""",
                (model,),
            ).fetchall()
            return [(r[0] / (r[1] / 1_000_000_000)) * 60 for r in rows]

    def get_job_durations(self, model: str, command: str | None = None) -> list[float]:
        """Wall-clock durations (seconds) from the jobs table (completed_at - started_at)."""
        with self._lock:
            conn = self._connect()
            if command is not None:
                rows = conn.execute(
                    """SELECT completed_at - started_at FROM jobs
                       WHERE model = ? AND command = ?
                         AND completed_at IS NOT NULL AND started_at IS NOT NULL
                       ORDER BY completed_at DESC LIMIT 50""",
                    (model, command),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT completed_at - started_at FROM jobs
                       WHERE model = ?
                         AND completed_at IS NOT NULL AND started_at IS NOT NULL
                       ORDER BY completed_at DESC LIMIT 50""",
                    (model,),
                ).fetchall()
            return [r[0] for r in rows]

    def get_load_durations(self, model: str) -> list[float]:
        """Convert load_duration_ns to seconds for recent jobs."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT load_duration_ns FROM job_metrics
                   WHERE model = ? AND load_duration_ns IS NOT NULL
                   ORDER BY completed_at DESC LIMIT 50""",
                (model,),
            ).fetchall()
            return [r[0] / 1_000_000_000 for r in rows]

    def get_model_stats(self) -> dict[str, dict]:
        """Aggregate stats per model: run_count, avg_tok_per_min, avg_warmup_s, model_size_gb."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT model,
                          COUNT(*) as run_count,
                          AVG(CASE WHEN eval_count IS NOT NULL AND eval_duration_ns IS NOT NULL
                                    AND eval_duration_ns > 0
                               THEN (CAST(eval_count AS REAL) / (eval_duration_ns / 1e9)) * 60
                               ELSE NULL END) as avg_tok_per_min,
                          AVG(CASE WHEN load_duration_ns IS NOT NULL
                               THEN load_duration_ns / 1e9
                               ELSE NULL END) as avg_warmup_s,
                          MAX(model_size_gb) as model_size_gb
                   FROM job_metrics
                   GROUP BY model"""
            ).fetchall()
            result = {}
            for r in rows:
                result[r[0]] = {
                    "run_count": r[1],
                    "avg_tok_per_min": r[2],
                    "avg_warmup_s": r[3],
                    "model_size_gb": r[4],
                }
            return result

    def has_pulling_model(self, model_name: str) -> bool:
        """Return True if any pull for model_name is currently in 'pulling' status."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT 1 FROM model_pulls WHERE model = ? AND status = 'pulling' LIMIT 1",
                (model_name,),
            ).fetchone()
            return row is not None

    # --- Utility ---

    def list_tables(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return [row["name"] for row in rows]

    # --- Consumers ---

    def upsert_consumer(self, data: dict) -> int:
        """Insert or update a consumer by (name, platform). Returns id."""
        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT id FROM consumers WHERE name = ? AND platform = ?",
                (data["name"], data["platform"]),
            ).fetchone()
            if existing:
                sets = ", ".join(f"{k} = ?" for k in data if k not in ("name", "platform"))
                if not sets:
                    return existing["id"]  # nothing to update beyond the key fields
                vals = [v for k, v in data.items() if k not in ("name", "platform")]
                conn.execute(f"UPDATE consumers SET {sets} WHERE id = ?", [*vals, existing["id"]])
                conn.commit()
                return existing["id"]
            cols = ", ".join(data.keys())
            placeholders = ", ".join("?" * len(data))
            cur = conn.execute(
                f"INSERT INTO consumers ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
            conn.commit()
            return cur.lastrowid

    def list_consumers(self) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT * FROM consumers ORDER BY detected_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_consumer(self, consumer_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM consumers WHERE id = ?", (consumer_id,)).fetchone()
            return dict(row) if row else None

    def update_consumer(self, consumer_id: int, **kwargs) -> None:
        with self._lock:
            conn = self._connect()
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            conn.execute(
                f"UPDATE consumers SET {sets} WHERE id = ?",
                [*kwargs.values(), consumer_id],
            )
            conn.commit()
