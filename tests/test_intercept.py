# tests/test_intercept.py
from unittest.mock import MagicMock, patch

from ollama_queue.config.intercept import (
    _rule_present,
    disable_intercept,
    enable_intercept,
)


def test_enable_intercept_runs_iptables():
    with patch("ollama_queue.config.intercept.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = enable_intercept(uid=1000, queue_port=7683)
    assert result["enabled"] is True
    assert mock_run.called
    # Verify iptables was called with correct args
    cmd = " ".join(mock_run.call_args_list[0][0][0])
    assert "11434" in cmd
    assert "7683" in cmd
    assert "uid-owner" in cmd


def test_disable_intercept_removes_rule():
    with patch("ollama_queue.config.intercept.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        disable_intercept(uid=1000, queue_port=7683)
    cmd = " ".join(mock_run.call_args[0][0])
    assert "-D" in cmd  # DELETE not APPEND


def test_rule_present_parses_iptables_output():
    sample = (
        "Chain OUTPUT (policy ACCEPT)\n"
        "target  prot  opt  source  destination\n"
        "REDIRECT tcp  --  anywhere  anywhere  tcp dpt:11434 owner UID match !1000 redir ports 7683\n"
    )
    with patch("ollama_queue.config.intercept.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=sample)
        assert _rule_present(uid=1000, queue_port=7683) is True


def test_rule_not_present_when_absent():
    with patch("ollama_queue.config.intercept.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Chain OUTPUT\n")
        assert _rule_present(uid=1000, queue_port=7683) is False


def test_enable_intercept_fails_on_non_linux():
    with patch("ollama_queue.config.intercept.platform.system", return_value="Darwin"):
        result = enable_intercept(uid=1000, queue_port=7683)
    assert result["enabled"] is False
    assert "linux" in result["error"].lower()


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------

from ollama_queue.config.intercept import _persist_rules, get_intercept_status


class TestEnableInterceptErrors:
    """enable_intercept error branches (lines 27, 30-31)."""

    def test_enable_returns_error_on_nonzero_exit(self):
        """iptables returns nonzero — line 27."""
        with (
            patch("ollama_queue.config.intercept.platform.system", return_value="Linux"),
            patch("ollama_queue.config.intercept.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="iptables: bad rule")
            result = enable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is False
        assert "iptables: bad rule" in result["error"]

    def test_enable_returns_error_on_oserror(self):
        """subprocess raises OSError — lines 30-31."""
        with (
            patch("ollama_queue.config.intercept.platform.system", return_value="Linux"),
            patch("ollama_queue.config.intercept.subprocess.run", side_effect=OSError("no sudo")),
        ):
            result = enable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is False
        assert "no sudo" in result["error"]

    def test_enable_returns_error_on_timeout(self):
        """subprocess raises TimeoutExpired — lines 30-31."""
        import subprocess as sp

        with (
            patch("ollama_queue.config.intercept.platform.system", return_value="Linux"),
            patch(
                "ollama_queue.config.intercept.subprocess.run",
                side_effect=sp.TimeoutExpired(cmd="iptables", timeout=10),
            ),
        ):
            result = enable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is False

    def test_enable_calls_persist_on_success(self):
        """After successful iptables -A, _persist_rules is called (line 28-29)."""
        with (
            patch("ollama_queue.config.intercept.platform.system", return_value="Linux"),
            patch("ollama_queue.config.intercept.subprocess.run") as mock_run,
            patch("ollama_queue.config.intercept._persist_rules") as mock_persist,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = enable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is True
        mock_persist.assert_called_once()


class TestDisableInterceptErrors:
    """disable_intercept error branches (lines 37, 48-49, 51-53)."""

    def test_disable_non_linux(self):
        """Non-Linux returns immediately — line 37."""
        with patch("ollama_queue.config.intercept.platform.system", return_value="Darwin"):
            result = disable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is False
        assert "Linux-only" in result["error"]

    def test_disable_nonzero_exit(self):
        """iptables -D fails with nonzero — lines 48-49."""
        with (
            patch("ollama_queue.config.intercept.platform.system", return_value="Linux"),
            patch("ollama_queue.config.intercept.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="rule not found")
            result = disable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is True
        assert "rule not found" in result["error"]

    def test_disable_oserror(self):
        """subprocess raises OSError — lines 51-53."""
        with (
            patch("ollama_queue.config.intercept.platform.system", return_value="Linux"),
            patch("ollama_queue.config.intercept.subprocess.run", side_effect=OSError("no sudo")),
        ):
            result = disable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is True
        assert "no sudo" in result["error"]

    def test_disable_timeout(self):
        """subprocess raises TimeoutExpired — lines 51-53."""
        import subprocess as sp

        with (
            patch("ollama_queue.config.intercept.platform.system", return_value="Linux"),
            patch(
                "ollama_queue.config.intercept.subprocess.run",
                side_effect=sp.TimeoutExpired(cmd="iptables", timeout=10),
            ),
        ):
            result = disable_intercept(uid=1000, queue_port=7683)
        assert result["enabled"] is True


class TestGetInterceptStatus:
    """get_intercept_status exercises lines 58-59."""

    def test_status_enabled(self):
        with patch("ollama_queue.config.intercept._rule_present", return_value=True):
            result = get_intercept_status(uid=1000, queue_port=7683)
        assert result["enabled"] is True
        assert result["rule_present"] is True
        assert result["uid"] == 1000

    def test_status_disabled(self):
        with patch("ollama_queue.config.intercept._rule_present", return_value=False):
            result = get_intercept_status(uid=1000, queue_port=7683)
        assert result["enabled"] is False
        assert result["rule_present"] is False


class TestRulePresentErrors:
    """_rule_present error branches (lines 72-73, 75-77)."""

    def test_rule_present_nonzero_exit(self):
        """iptables -L returns nonzero — lines 72-73."""
        with patch("ollama_queue.config.intercept.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
            assert _rule_present(uid=1000, queue_port=7683) is False

    def test_rule_present_exception(self):
        """subprocess raises — lines 75-77."""
        with patch("ollama_queue.config.intercept.subprocess.run", side_effect=OSError("no iptables")):
            assert _rule_present(uid=1000, queue_port=7683) is False


class TestPersistRules:
    """_persist_rules exercises line 106."""

    def test_persist_rules_success(self, tmp_path):
        """Successful iptables-save writes to rules file — line 106."""
        rules_content = "*nat\n:OUTPUT ACCEPT\n-A OUTPUT ...\nCOMMIT\n"
        with (
            patch("ollama_queue.config.intercept.subprocess.run") as mock_run,
            patch("builtins.open", create=True) as mock_open,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=rules_content)
            _persist_rules()
        mock_open.assert_called_once_with("/etc/iptables/rules.v4", "w")

    def test_persist_rules_nonzero_no_write(self):
        """iptables-save nonzero — does not write."""
        with (
            patch("ollama_queue.config.intercept.subprocess.run") as mock_run,
            patch("builtins.open", create=True) as mock_open,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            _persist_rules()
        mock_open.assert_not_called()

    def test_persist_rules_exception_caught(self):
        """Exception in iptables-save is caught (best-effort)."""
        with patch("ollama_queue.config.intercept.subprocess.run", side_effect=OSError("no iptables-save")):
            # Should not raise
            _persist_rules()
