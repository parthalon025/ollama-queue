"""Tests for eval provider abstraction."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ollama_queue.eval.providers import (
    OllamaProvider,
    get_provider,
)


class TestOllamaProvider:
    def test_generate_calls_proxy(self):
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("result text", {"tokens": 100}, 123)
            text, usage, job_id = provider.generate(
                prompt="test prompt",
                system=None,
                model="qwen2.5:7b",
                temperature=0.6,
                num_ctx=8192,
                params=None,
                timeout=300,
                source="test",
            )
        assert text == "result text"
        assert usage["tokens"] == 100

    def test_generate_merges_extra_params(self):
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("text", {}, None)
            provider.generate(
                prompt="test",
                system="Be precise",
                model="qwen2.5:7b",
                temperature=0.6,
                num_ctx=8192,
                params={"top_k": 40, "top_p": 0.9},
                timeout=300,
                source="test",
            )
        body = mock.call_args[0][0]
        assert body["options"]["top_k"] == 40
        assert body["options"]["top_p"] == 0.9
        assert body["options"]["temperature"] == 0.6
        assert body["system"] == "Be precise"

    def test_generate_omits_system_when_none(self):
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("text", {}, None)
            provider.generate(
                prompt="test",
                system=None,
                model="m",
                temperature=0.6,
                num_ctx=8192,
                params=None,
                timeout=300,
                source="test",
            )
        body = mock.call_args[0][0]
        assert "system" not in body

    def test_generate_empty_string_system_is_included(self):
        """system='' should still be included (it's not None)."""
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("text", {}, None)
            provider.generate(
                prompt="test",
                system="",
                model="m",
                temperature=0.6,
                num_ctx=8192,
                params=None,
                timeout=300,
                source="test",
            )
        body = mock.call_args[0][0]
        assert "system" in body
        assert body["system"] == ""

    def test_flat_columns_win_over_params_bag(self):
        """temperature/num_ctx from flat columns should override params bag."""
        provider = OllamaProvider(http_base="http://127.0.0.1:7683")
        with patch("ollama_queue.eval.providers._call_proxy_raw") as mock:
            mock.return_value = ("text", {}, None)
            provider.generate(
                prompt="test",
                system=None,
                model="m",
                temperature=0.6,
                num_ctx=8192,
                params={"top_k": 40, "temperature": 999},
                timeout=300,
                source="test",
            )
        body = mock.call_args[0][0]
        assert body["options"]["temperature"] == 0.6
        assert body["options"]["top_k"] == 40


def test_call_proxy_raw_returns_queue_job_id():
    """_call_proxy_raw must read _queue_job_id (with underscore) from proxy response."""
    from unittest.mock import MagicMock, patch

    from ollama_queue.eval.providers import _call_proxy_raw

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": "hello world",
        "_queue_job_id": 42,
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp
        text, usage, job_id = _call_proxy_raw(
            {"model": "qwen2.5:7b", "prompt": "test", "stream": False},
            "http://127.0.0.1:7683",
            60,
        )

    assert job_id == 42, f"Expected job_id=42, got {job_id!r}"


class TestGetProvider:
    def test_ollama_returns_ollama_provider(self):
        p = get_provider("ollama", http_base="http://localhost:7683")
        assert isinstance(p, OllamaProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("invalid")

    def test_claude_without_sdk_raises(self):
        import sys

        real = sys.modules.get("anthropic")
        sys.modules["anthropic"] = None  # type: ignore[assignment]
        try:
            with pytest.raises((ImportError, TypeError)):
                get_provider("claude")
        finally:
            if real is not None:
                sys.modules["anthropic"] = real
            else:
                del sys.modules["anthropic"]

    def test_openai_without_sdk_raises(self):
        import sys

        real = sys.modules.get("openai")
        sys.modules["openai"] = None  # type: ignore[assignment]
        try:
            with pytest.raises((ImportError, TypeError)):
                get_provider("openai")
        finally:
            if real is not None:
                sys.modules["openai"] = real
            else:
                del sys.modules["openai"]
