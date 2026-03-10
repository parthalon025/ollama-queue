"""Daemon polling loop and job runner for ollama-queue.

Plain English: The heartbeat. Every 5 seconds it wakes up, asks "is the system
healthy, is anyone else using Ollama, and do we have capacity?" — then either
grabs the next job and runs it as a subprocess, or goes back to sleep with a
reason logged (paused_health, paused_interactive, idle).

Decision it drives: Should a job start right now, or should we wait — and why?

Uses mixin pattern to split implementation across domain files while preserving
a single Daemon class API.
"""

from __future__ import annotations

import threading
from collections import deque
from concurrent.futures import Future

from ollama_queue.daemon.executor import (  # noqa: F401
    _MAX_STDOUT_BYTES,
    ExecutorMixin,
    _drain_pipes_with_tracking,
)
from ollama_queue.daemon.loop import LoopMixin
from ollama_queue.db import Database
from ollama_queue.dlq import DLQManager
from ollama_queue.models.client import OllamaModels
from ollama_queue.models.estimator import DurationEstimator
from ollama_queue.models.runtime_estimator import RuntimeEstimator
from ollama_queue.scheduling.deferral import DeferralScheduler
from ollama_queue.scheduling.dlq_scheduler import DLQScheduler
from ollama_queue.scheduling.scheduler import Scheduler
from ollama_queue.sensing.burst import _default_detector as _burst_singleton
from ollama_queue.sensing.health import HealthMonitor
from ollama_queue.sensing.stall import StallDetector


class Daemon(LoopMixin, ExecutorMixin):
    """Main daemon that polls for jobs and executes them."""

    def __init__(self, db: Database, health_monitor: HealthMonitor | None = None):
        self.db = db
        self.health = health_monitor or HealthMonitor()
        self.estimator = DurationEstimator(db)
        self.scheduler = Scheduler(db)
        self.dlq = DLQManager(db)
        self._last_prune: float = 0.0
        self._recent_job_models: dict[str, float] = {}  # model -> last_completed_at
        self._recent_models_lock = threading.Lock()
        # Concurrency tracking
        self._running: dict[int, Future] = {}  # job_id -> Future
        self._running_models: dict[int, str] = {}  # job_id -> model
        self._running_lock = threading.Lock()
        self._executor = None
        self._concurrent_enabled_at: float | None = None  # for shadow mode
        self._ollama_models = OllamaModels()
        self.stall_detector = StallDetector()
        # Circuit breaker state
        self._cb_failures: int = 0  # consecutive Ollama failures
        self._cb_state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._cb_opened_at: float | None = None  # timestamp when circuit opened
        self._cb_open_attempts: int = 0  # how many times circuit has opened (for exponential cooldown)
        self._cb_probe_in_flight: bool = False  # True while a HALF_OPEN probe is dispatched
        self._cb_lock: threading.Lock = threading.Lock()  # protects all _cb_* state
        # Adaptive entropy tracking (in-memory rolling baseline)
        self._entropy_history: deque[float] = deque(maxlen=30)
        self._entropy_suspend_until: float = 0.0
        # Burst detection -- use the module-level singleton so the API's record_submission()
        # calls (on /api/queue/submit) feed into the same detector the daemon reads.
        self._burst_detector = _burst_singleton
        self._burst_regime: str = "unknown"  # cached for /api/health
        # DLQ auto-reschedule + deferral schedulers
        self._runtime_estimator = RuntimeEstimator(db)
        self._dlq_scheduler = DLQScheduler(db, self._runtime_estimator, self._get_load_map)
        self._deferral_scheduler = DeferralScheduler(db, self._runtime_estimator, self._get_load_map)
        self._last_dlq_sweep: float = 0.0
