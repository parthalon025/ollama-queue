"""Tests for SystemSnapshot dataclass and classify_failure function."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from ollama_queue.system_snapshot import SystemSnapshot, classify_failure


class TestSnapshotDefaults:
    """Verify SystemSnapshot initialises with safe zero/empty defaults."""

    def test_snapshot_defaults(self):
        snap = SystemSnapshot()
        assert snap.timestamp == 0.0
        assert snap.ram_used_pct == 0.0
        assert snap.ram_available_gb == 0.0
        assert snap.vram_used_pct == 0.0
        assert snap.vram_available_gb == 0.0
        assert snap.gpu_temp_c is None
        assert snap.load_avg_1m == 0.0
        assert snap.swap_used_pct == 0.0
        assert snap.loaded_models == []
        assert snap.queue_depth == 0
        assert snap.current_job_model is None


class TestSnapshotCapture:
    """Verify capture() reads from a mock HealthMonitor."""

    def test_snapshot_capture_with_health_monitor(self):
        hm = MagicMock()
        hm.get_ram_pct.return_value = 65.2
        hm.get_swap_pct.return_value = 12.5
        hm.get_load_avg.return_value = 2.3
        hm.get_vram_pct.return_value = 78.0

        before = time.time()
        snap = SystemSnapshot.capture(health_monitor=hm)
        after = time.time()

        assert before <= snap.timestamp <= after
        assert snap.ram_used_pct == 65.2
        assert snap.swap_used_pct == 12.5
        assert snap.load_avg_1m == 2.3
        assert snap.vram_used_pct == 78.0

    def test_snapshot_capture_no_args(self):
        """capture() with no arguments returns a timestamped default snapshot."""
        before = time.time()
        snap = SystemSnapshot.capture()
        after = time.time()

        assert before <= snap.timestamp <= after
        assert snap.ram_used_pct == 0.0
        assert snap.vram_used_pct == 0.0

    def test_snapshot_capture_health_monitor_vram_none(self):
        """VRAM stays at 0.0 when the monitor returns None (no GPU)."""
        hm = MagicMock()
        hm.get_ram_pct.return_value = 40.0
        hm.get_swap_pct.return_value = 0.0
        hm.get_load_avg.return_value = 1.0
        hm.get_vram_pct.return_value = None

        snap = SystemSnapshot.capture(health_monitor=hm)
        assert snap.vram_used_pct == 0.0


class TestClassifyResourceFailure:
    """Resource failures: OOM, VRAM, disk full."""

    @pytest.mark.parametrize(
        "reason",
        [
            "CUDA out of memory",
            "OOM killed by kernel",
            "cannot allocate memory for tensor",
            "VRAM exhausted during model load",
            "No space left on device",
            "disk full",
            "insufficient memory to complete operation",
        ],
    )
    def test_classify_resource_failure(self, reason: str):
        assert classify_failure(reason) == "resource"

    def test_classify_resource_by_exit_code_137(self):
        """Exit code 137 (SIGKILL) implies OOM killer."""
        assert classify_failure("", exit_code=137) == "resource"


class TestClassifyTimeoutFailure:
    """Timeout failures."""

    @pytest.mark.parametrize(
        "reason",
        [
            "Job exceeded time limit of 120s",
            "Operation timed out",
            "timeout waiting for response",
            "deadline exceeded",
        ],
    )
    def test_classify_timeout_failure(self, reason: str):
        assert classify_failure(reason) == "timeout"


class TestClassifyModelError:
    """Model-related errors that won't fix themselves on retry."""

    @pytest.mark.parametrize(
        "reason",
        [
            "model 'llama99:latest' not found",
            "corrupt weight file detected",
            "invalid model format",
            "failed to load model from disk",
            "no such model: qwen99",
            "model xyz does not exist",
        ],
    )
    def test_classify_model_error(self, reason: str):
        assert classify_failure(reason) == "model_error"


class TestClassifyTransientFailure:
    """Transient / network errors that may resolve on retry."""

    @pytest.mark.parametrize(
        "reason",
        [
            "connection refused",
            "connection reset by peer",
            "network unreachable",
            "temporarily unavailable",
            "HTTP 503 service unavailable",
            "ECONNREFUSED",
        ],
    )
    def test_classify_transient_failure(self, reason: str):
        assert classify_failure(reason) == "transient"


class TestClassifyPermanentFailure:
    """Permanent errors that will never succeed on retry."""

    @pytest.mark.parametrize(
        "reason",
        [
            "syntax error near unexpected token",
            "bash: command not found",
            "permission denied",
            "no such file or directory",
            "missing script: run.sh",
            "exit code 127",
        ],
    )
    def test_classify_permanent_failure(self, reason: str):
        assert classify_failure(reason) == "permanent"

    def test_classify_permanent_by_exit_code_127(self):
        assert classify_failure("", exit_code=127) == "permanent"

    def test_classify_permanent_by_exit_code_126(self):
        assert classify_failure("", exit_code=126) == "permanent"


class TestClassifyUnknownFailure:
    """Unknown failures that don't match any pattern."""

    def test_classify_unknown_failure(self):
        assert classify_failure("something completely unexpected happened") == "unknown"

    def test_classify_empty_reason_no_exit_code(self):
        assert classify_failure("") == "unknown"

    def test_classify_none_like_reason(self):
        assert classify_failure("", exit_code=1) == "unknown"
