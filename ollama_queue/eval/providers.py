"""Multi-provider abstraction for eval pipeline.

Providers: Ollama (via queue proxy), Claude (Anthropic SDK), OpenAI (OpenAI SDK).
All providers return (text, usage_metadata, job_id_or_none).
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

_log = logging.getLogger(__name__)

_RETRYABLE_CODES = {429, 502, 503, 504}
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 2.0


def _call_proxy_raw(
    body: dict[str, Any],
    http_base: str,
    timeout: int,
) -> tuple[str | None, dict, int | None]:
    """Low-level POST to ollama-queue proxy. Returns (text, usage, job_id)."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=timeout + 30) as client:
                resp = client.post(
                    f"{http_base}/api/generate",
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code in _RETRYABLE_CODES and attempt < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                _log.warning("proxy %d retry in %.0fs", resp.status_code, delay)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("response", "")
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            usage = {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_duration_ns": data.get("total_duration", 0),
            }
            job_id = data.get("_queue_job_id")
            return raw if raw else None, usage, job_id
        except httpx.HTTPStatusError:
            _log.exception("proxy call failed (HTTP error)")
            return None, {}, None
        except Exception:
            _log.exception("proxy call failed")
            if attempt >= _MAX_RETRIES:
                return None, {}, None
            time.sleep(_RETRY_BASE_DELAY * (2**attempt))
    # unreachable: all loop paths return explicitly above
    return None, {}, None  # pragma: no cover


class EvalProvider(ABC):
    """Unified interface for LLM calls across providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str | None,
        model: str,
        temperature: float,
        num_ctx: int,
        params: dict | None,
        timeout: int,
        source: str,
        priority: int = 2,
    ) -> tuple[str | None, dict, int | None]:
        """Generate text. Returns (text, usage_metadata, provider_job_id)."""
        ...


class OllamaProvider(EvalProvider):
    """Routes through ollama-queue proxy."""

    def __init__(self, http_base: str = "http://127.0.0.1:7683"):
        self.http_base = http_base

    def generate(self, prompt, system, model, temperature, num_ctx, params, timeout, source, priority=2):
        options: dict[str, Any] = {"temperature": temperature, "num_ctx": num_ctx}
        if params:
            for k, v in params.items():
                if k not in ("temperature", "num_ctx"):
                    options[k] = v

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options,
            "_priority": priority,
            "_source": source,
            "_timeout": timeout,
        }
        if system is not None:
            body["system"] = system

        return _call_proxy_raw(body, self.http_base, timeout)


class ClaudeProvider(EvalProvider):
    """Anthropic SDK provider."""

    def __init__(self, api_key: str | None = None):
        try:
            import anthropic

            if anthropic is None:
                raise ImportError("anthropic module is None")
        except (ImportError, TypeError) as exc:
            raise ImportError("Install `anthropic` to use Claude provider: pip install anthropic") from exc
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def generate(self, prompt, system, model, temperature, num_ctx, params, timeout, source, priority=2):
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": min(num_ctx, 4096),
            "temperature": temperature,
        }
        if system is not None:
            kwargs["system"] = system

        try:
            response = self._client.messages.create(**kwargs)
            text = response.content[0].text if response.content else None
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            }
            return text, usage, None
        except Exception:
            _log.exception("Claude API call failed")
            return None, {}, None


class OpenAIProvider(EvalProvider):
    """OpenAI SDK provider (also works with OpenAI-compatible servers)."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        try:
            import openai

            if openai is None:
                raise ImportError("openai module is None")
        except (ImportError, TypeError) as exc:
            raise ImportError("Install `openai` to use OpenAI provider: pip install openai") from exc
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    def generate(self, prompt, system, model, temperature, num_ctx, params, timeout, source, priority=2):
        messages = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(num_ctx, 4096),
        }

        try:
            response = self._client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content if response.choices else None
            usage = {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            }
            return text, usage, None
        except Exception:
            _log.exception("OpenAI API call failed")
            return None, {}, None


def get_provider(
    provider_name: str,
    http_base: str = "http://127.0.0.1:7683",
    api_key: str | None = None,
    base_url: str | None = None,
) -> EvalProvider:
    """Factory function to get a provider instance."""
    if provider_name == "ollama":
        return OllamaProvider(http_base=http_base)
    elif provider_name == "claude":
        return ClaudeProvider(api_key=api_key)
    elif provider_name == "openai":
        return OpenAIProvider(api_key=api_key, base_url=base_url)
    else:
        raise ValueError(f"Unknown provider '{provider_name}'. Use: ollama, claude, openai")
