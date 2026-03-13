"""Schema definitions, migrations, and seed data for ollama-queue.

Plain English: The blueprint for the database. Creates tables, runs ALTER TABLE
migrations, and populates default settings and eval prompt templates/variants.
"""

import json
import logging
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
    # DLQ auto-reschedule
    "dlq.auto_reschedule": True,
    "dlq.sweep_fallback_minutes": 30,
    "dlq.chronic_failure_threshold": 5,
    # Proactive deferral
    "defer.enabled": True,
    "defer.burst_priority_threshold": 3,
    "defer.thermal_threshold_c": 85,
    "defer.resource_wait_timeout_s": 120,
    "max_pause_duration_seconds": 600,
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
    # Provider settings — which backend to use for each pipeline role
    "eval.generator_provider": "ollama",
    "eval.generator_model": "",
    "eval.judge_provider": "ollama",
    "eval.optimizer_provider": "claude",
    "eval.optimizer_model": "claude-sonnet-4-6",
    "eval.oracle_provider": "claude",
    "eval.oracle_model": "claude-sonnet-4-6",
    "eval.oracle_enabled": "false",
    "eval.claude_api_key": "",
    "eval.openai_api_key": "",
    "eval.openai_base_url": "",
    "eval.max_cost_per_run_usd": "1.00",
}


class SchemaMixin:
    """Schema creation, migrations, and seed data."""

    def _run_migrations(self, conn):  # noqa: PLR0915
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
        # Eval enhancement: variant params, system_prompt, training_config, provider
        self._add_column_if_missing(conn, "eval_variants", "system_prompt", "TEXT")
        self._add_column_if_missing(conn, "eval_variants", "params", "TEXT DEFAULT '{}'")
        self._add_column_if_missing(conn, "eval_variants", "training_config", "TEXT")
        self._add_column_if_missing(conn, "eval_variants", "provider", "TEXT DEFAULT 'ollama'")
        # Eval enhancement: run-level tracking columns
        self._add_column_if_missing(conn, "eval_runs", "cost_json", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "oracle_json", "TEXT")
        self._add_column_if_missing(conn, "eval_runs", "suggestions_json", "TEXT")
        # Judge parse failure tracking (#22)
        self._add_column_if_missing(conn, "eval_runs", "judge_parse_failures", "INTEGER DEFAULT 0")
        # Backfill pre-existing rows
        conn.execute("UPDATE eval_variants SET params = '{}' WHERE params IS NULL")
        conn.execute("UPDATE eval_variants SET provider = 'ollama' WHERE provider IS NULL")
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
            "E": ("Chunked input with the 14B model — combines the focused context of C with the capacity of D."),
            "F": ("Asks the model to state when the principle does NOT apply. Sharper scope reduces false positives."),
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

    def initialize(self):
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

            CREATE TABLE IF NOT EXISTS backend_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backend_url TEXT NOT NULL,
                model TEXT NOT NULL,
                eval_count INTEGER,
                eval_duration_ns INTEGER,
                load_duration_ns INTEGER,
                prompt_eval_count INTEGER,
                prompt_eval_duration_ns INTEGER,
                total_duration_ns INTEGER,
                tok_per_min REAL,
                recorded_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_backend_metrics_backend_model
                ON backend_metrics(backend_url, model);

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

    def seed_eval_defaults(self, conn=None):
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

    def _do_seed_eval_defaults(self, conn, created_at):
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
