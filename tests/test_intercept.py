# tests/test_intercept.py
from unittest.mock import MagicMock, patch

from ollama_queue.intercept import (
    _rule_present,
    disable_intercept,
    enable_intercept,
)


def test_enable_intercept_runs_iptables():
    with patch("ollama_queue.intercept.subprocess.run") as mock_run:
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
    with patch("ollama_queue.intercept.subprocess.run") as mock_run:
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
    with patch("ollama_queue.intercept.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=sample)
        assert _rule_present(uid=1000, queue_port=7683) is True


def test_rule_not_present_when_absent():
    with patch("ollama_queue.intercept.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Chain OUTPUT\n")
        assert _rule_present(uid=1000, queue_port=7683) is False


def test_enable_intercept_fails_on_non_linux():
    with patch("ollama_queue.intercept.platform.system", return_value="Darwin"):
        result = enable_intercept(uid=1000, queue_port=7683)
    assert result["enabled"] is False
    assert "linux" in result["error"].lower()
