"""Proxy endpoints: /api/generate, /api/embed, /v1/chat/completions, /v1/embeddings."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Body, HTTPException
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

import ollama_queue.api as _api
from ollama_queue.api.backend_router import select_backend
from ollama_queue.models.client import OllamaModels

_log = logging.getLogger(__name__)

router = APIRouter()

PROXY_WAIT_TIMEOUT = 600
PROXY_POLL_INTERVAL = 0.5

# HTTP hop-by-hop headers that must not be forwarded to clients.
_hop_by_hop = frozenset(
    [
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
    ]
)


async def _iter_ndjson(rp_resp, release_fn=None, metrics_fn=None):
    """Yield complete NDJSON lines from a streaming httpx response.

    Buffers aiter_raw() output — chunks are NOT guaranteed line-aligned.
    Calls release_fn() when done=true final chunk is seen (releases proxy claim).
    Calls metrics_fn(parsed_chunk) with the done=true chunk for backend metrics capture.
    """
    buffer = b""
    try:
        async for raw in rp_resp.aiter_raw():
            buffer += raw
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                yield line + b"\n"
                try:
                    obj = json.loads(line)
                    if obj.get("done"):
                        if metrics_fn:
                            with contextlib.suppress(Exception):
                                metrics_fn(obj)
                            metrics_fn = None
                        if release_fn:
                            release_fn()
                            release_fn = None
                except (ValueError, AttributeError):
                    pass
        if buffer.strip():
            yield buffer
    finally:
        if release_fn:
            release_fn()


async def _proxy_ollama_request(
    endpoint: str,
    command: str,
    body: dict,
    resource_profile: str,
    extract_stdout_fn,
    error_prefix: str,
):
    """Shared proxy logic for Ollama requests serialized through the queue.

    Handles: pause check, _priority/_source/_timeout extraction, claim polling,
    job logging, HTTP forwarding, and job completion/failure recording. Both
    proxy_generate and proxy_embed delegate to this helper; the caller is
    responsible for any endpoint-specific body mutations (e.g. stream=False)
    before calling this function.

    Args:
        endpoint: Full Ollama URL path (e.g. "/api/generate").
        command: Label stored in the jobs table (e.g. "proxy:/api/generate").
        body: Request body dict (queue-specific fields are popped inside).
        resource_profile: "ollama" for generate, "embed" for embed models.
        extract_stdout_fn: Callable[[dict], str] -- extracts a short summary
            from the Ollama JSON response for the stdout_tail column.
        error_prefix: Prefix for the 502 error detail message.
    """
    db = _api.db
    state = db.get_daemon_state()
    if state and state.get("state") == "paused_manual":
        raise HTTPException(status_code=503, detail="Queue is manually paused")

    priority = body.pop("_priority", 0)
    source = body.pop("_source", "proxy")
    req_timeout = body.pop("_timeout", 600)  # default matches default_timeout_seconds; callers may override

    # Track request against known consumer for health monitoring
    try:
        for _row in db.list_consumers():
            if _row.get("source_label") == source and _row.get("status") in ("patched", "included"):
                import time as _time_mod

                db.update_consumer(
                    _row["id"],
                    request_count=(_row["request_count"] or 0) + 1,
                    last_seen=int(_time_mod.time()),
                )
                break
    except Exception:
        _log.warning("request_count tracking failed for source=%s", source, exc_info=True)

    model = body.get("model", "")

    waited = 0.0
    claimed = False
    while waited < PROXY_WAIT_TIMEOUT:
        claimed = db.try_claim_for_proxy()
        if claimed:
            break
        await asyncio.sleep(PROXY_POLL_INTERVAL)
        waited += PROXY_POLL_INTERVAL

    if not claimed:
        raise HTTPException(status_code=504, detail="Timed out waiting for queue turn")

    job_id = db.submit_job(
        command=command,
        model=model,
        priority=priority,
        timeout=req_timeout,
        source=source,
        resource_profile=resource_profile,
    )
    db.start_job(job_id)

    try:
        backend = await select_backend(model)
        async with httpx.AsyncClient(timeout=httpx.Timeout(float(req_timeout))) as client:
            resp = await client.post(f"{backend}{endpoint}", json=body)
            result = resp.json()

        db.complete_job(
            job_id=job_id,
            exit_code=0,
            stdout_tail=extract_stdout_fn(result),
            stderr_tail="",
            outcome_reason=None,
        )
        if result.get("eval_count"):
            try:
                db.store_backend_metrics(backend_url=backend, model=model, metrics=result)
            except Exception:
                _log.warning("store_backend_metrics failed for job %d", job_id, exc_info=True)
        result["_queue_job_id"] = job_id
        return result
    except httpx.ReadTimeout as e:
        # ReadTimeout is expected for slow models (e.g. deepseek-r1); log as WARNING not ERROR
        _log.warning("%s timed out for job %d after %ss (pass _timeout to override)", command, job_id, req_timeout)
        db.complete_job(
            job_id=job_id,
            exit_code=1,
            stdout_tail="",
            stderr_tail=str(e)[:500],
            outcome_reason=f"proxy timeout after {req_timeout}s",
        )
        raise HTTPException(status_code=504, detail=f"{error_prefix}: read timeout after {req_timeout}s") from e
    except Exception as e:
        _log.error("%s failed for job %d: %s", command, job_id, e, exc_info=True)
        db.complete_job(
            job_id=job_id,
            exit_code=1,
            stdout_tail="",
            stderr_tail=str(e)[:500],
            outcome_reason=f"proxy error: {e}",
        )
        raise HTTPException(status_code=502, detail=f"{error_prefix}: {e}") from e
    finally:
        try:
            db.release_proxy_claim()
        except Exception:
            _log.exception("release_proxy_claim failed -- daemon may be stuck at sentinel job_id")


@router.post("/api/generate")
async def proxy_generate(body: dict = Body(...)):
    """Forward a generate request to Ollama, serializing through the queue.

    Queue-specific fields (extracted from body, not forwarded to Ollama):
      _priority: int (default 0) -- job priority (lower = higher priority)
      _source: str (default "proxy") -- caller identifier for history/debugging
      _timeout: int (default 600) -- request timeout in seconds; increase for slow reasoning models

    Streaming: if the caller sets stream=True, the response is a StreamingResponse of
    NDJSON chunks exactly as Ollama emits them. If stream is absent or False, the
    existing single-JSON-blob path is used unchanged.
    """
    db = _api.db
    is_streaming = body.get("stream", False)

    if not is_streaming:
        # Non-streaming path: preserve existing behaviour exactly.
        body["stream"] = False
        return await _proxy_ollama_request(
            endpoint="/api/generate",
            command="proxy:/api/generate",
            body=body,
            resource_profile="ollama",
            extract_stdout_fn=lambda r: str(r.get("response", ""))[:500],
            error_prefix="Ollama request failed",
        )

    # --- Streaming path ---
    state = db.get_daemon_state()
    if state and state.get("state") == "paused_manual":
        raise HTTPException(status_code=503, detail="Queue is manually paused")

    priority = body.pop("_priority", 0)
    source = body.pop("_source", "proxy")
    req_timeout = body.pop("_timeout", 600)

    # Track request against known consumer for health monitoring (streaming path)
    try:
        for _row in db.list_consumers():
            if _row.get("source_label") == source and _row.get("status") in ("patched", "included"):
                import time as _time_mod

                db.update_consumer(
                    _row["id"],
                    request_count=(_row["request_count"] or 0) + 1,
                    last_seen=int(_time_mod.time()),
                )
                break
    except Exception:
        _log.warning("request_count tracking failed for source=%s", source, exc_info=True)

    model = body.get("model", "")

    waited = 0.0
    claimed = False
    while waited < PROXY_WAIT_TIMEOUT:
        claimed = db.try_claim_for_proxy()
        if claimed:
            break
        await asyncio.sleep(PROXY_POLL_INTERVAL)
        waited += PROXY_POLL_INTERVAL

    if not claimed:
        raise HTTPException(status_code=504, detail="Timed out waiting for queue turn")

    job_id = db.submit_job(
        command="proxy:/api/generate",
        model=model,
        priority=priority,
        timeout=req_timeout,
        source=source,
        resource_profile="ollama",
    )
    db.start_job(job_id)

    try:
        # Use build_request + send(stream=True) so httpx doesn't buffer the body.
        backend = await select_backend(model)
        async_client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))
        rp_req = async_client.build_request("POST", f"{backend}/api/generate", json=body)
        rp_resp = await async_client.send(rp_req, stream=True)
    except Exception as e:
        _log.error("proxy:/api/generate streaming setup failed for job %d: %s", job_id, e, exc_info=True)
        await async_client.aclose()
        db.complete_job(
            job_id, exit_code=1, stdout_tail="", stderr_tail=str(e)[:500], outcome_reason=f"proxy error: {e}"
        )
        db.release_proxy_claim()
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}") from e

    _released = False

    def _release():
        nonlocal _released
        try:
            db.complete_job(job_id, exit_code=0, stdout_tail="(streaming)", stderr_tail="", outcome_reason=None)
        except Exception:
            _log.exception("complete_job failed for streaming job %d", job_id)
        try:
            db.release_proxy_claim()
            _released = True
        except Exception:
            _log.exception("release_proxy_claim failed for streaming job %d", job_id)

    def _on_streaming_metrics(chunk):
        if chunk.get("eval_count"):
            try:
                db.store_backend_metrics(backend_url=backend, model=model, metrics=chunk)
            except Exception:
                _log.warning("store_backend_metrics (streaming) failed for job %d", job_id, exc_info=True)

    headers = {k: v for k, v in rp_resp.headers.items() if k.lower() not in _hop_by_hop}

    async def _cleanup_streaming_resources():
        """Guaranteed cleanup: close httpx resources + force-release if generator didn't."""
        try:
            await rp_resp.aclose()
        except Exception:
            _log.debug("rp_resp.aclose() failed during cleanup", exc_info=True)
        try:
            await async_client.aclose()
        except Exception:
            _log.debug("async_client.aclose() failed during cleanup", exc_info=True)
        if not _released:
            _log.warning(
                "Streaming proxy release not confirmed for job %d — forcing cleanup",
                job_id,
            )
            try:
                db.release_proxy_claim()
            except Exception:
                _log.exception("forced release_proxy_claim also failed for job %d", job_id)

    return StreamingResponse(
        _iter_ndjson(rp_resp, release_fn=_release, metrics_fn=_on_streaming_metrics),
        status_code=rp_resp.status_code,
        headers=headers,
        media_type="application/x-ndjson",
        background=BackgroundTask(_cleanup_streaming_resources),
    )


@router.post("/api/embed")
async def proxy_embed(body: dict = Body(...)):
    """Forward an embed request to Ollama, serializing through the queue.

    Queue-specific fields (extracted from body, not forwarded to Ollama):
      _priority: int (default 0) -- job priority (lower = higher priority)
      _source: str (default "proxy") -- caller identifier for history/debugging
      _timeout: int (default 600) -- request timeout in seconds; increase for slow reasoning models

    Supports both single-string and array input:
      {"model": "nomic-embed-text", "input": "text"}
      {"model": "nomic-embed-text", "input": ["text1", "text2"]}
    """
    body["stream"] = False  # Embed API does not stream, but force it defensively
    model = body.get("model", "")
    # Use OllamaModels.classify() to derive resource_profile from the model name,
    # matching the daemon's own concurrency logic (embed profile = 4 concurrent slots,
    # no VRAM gate). classify() returns "embed" for embed/nomic/bge/mxbai/all-minilm
    # models; if model is empty or unknown, classify() returns "ollama" so we fall back
    # to "embed" since /api/embed is always an embed workload.
    resource_profile = OllamaModels().classify(model)["resource_profile"] if model else "embed"
    return await _proxy_ollama_request(
        endpoint="/api/embed",
        command="proxy:/api/embed",
        body=body,
        resource_profile=resource_profile,
        extract_stdout_fn=lambda r: f"embeddings: {len(r.get('embeddings', []))} vectors",
        error_prefix="Ollama embed request failed",
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoints — translates OpenAI wire format to/from Ollama
# so that LangChain's ChatOpenAI can route through the queue without fork changes.
# ---------------------------------------------------------------------------


def _openai_to_ollama_chat_request(body: dict) -> dict:
    """Translate an OpenAI /v1/chat/completions request body to Ollama /api/chat format.

    Preserves queue metadata fields (_priority, _source, _timeout) so that
    _proxy_ollama_request can pop them from the translated body as normal.
    """
    ollama: dict = {
        "model": body.get("model", ""),
        "messages": body.get("messages", []),
        "stream": False,
    }
    options: dict = {}
    if "temperature" in body:
        options["temperature"] = body["temperature"]
    if "max_tokens" in body:
        options["num_predict"] = body["max_tokens"]
    if options:
        ollama["options"] = options
    for key in ("_priority", "_source", "_timeout"):
        if key in body:
            ollama[key] = body[key]
    return ollama


def _ollama_chat_to_openai_response(result: dict, model: str) -> dict:
    """Translate an Ollama /api/chat response to OpenAI /v1/chat/completions format."""
    message = result.get("message", {})
    done_reason = result.get("done_reason", "stop")
    # Ollama uses "stop" and "length"; map others to "stop" for compatibility.
    finish_reason = done_reason if done_reason in ("stop", "length") else "stop"
    prompt_tokens = result.get("prompt_eval_count", 0) or 0
    completion_tokens = result.get("eval_count", 0) or 0
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or result.get("model", ""),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": message.get("role", "assistant"),
                    "content": message.get("content", ""),
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _ollama_embed_to_openai_response(result: dict) -> dict:
    """Translate an Ollama /api/embed response to OpenAI /v1/embeddings format."""
    embeddings = result.get("embeddings", [])
    prompt_tokens = result.get("prompt_eval_count", 0) or 0
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": vec} for i, vec in enumerate(embeddings)],
        "model": result.get("model", ""),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "total_tokens": prompt_tokens,
        },
    }


@router.post("/v1/chat/completions")
async def proxy_chat_completions(body: dict = Body(...)):
    """OpenAI-compatible chat completions endpoint, serialized through the queue.

    Accepts OpenAI /v1/chat/completions format; translates to Ollama /api/chat,
    queues through the daemon sentinel, then translates the response back to
    OpenAI format. Allows LangChain ChatOpenAI (and any OpenAI-compat client)
    to route through the queue without modifying the caller.

    Queue-specific fields (extracted from body, not forwarded to Ollama):
      _priority: int (default 0)
      _source: str (default "proxy")
      _timeout: int (default 600)
    """
    ollama_body = _openai_to_ollama_chat_request(body)
    ollama_body.setdefault("_source", "gpt-researcher")
    model = ollama_body.get("model", "")
    result = await _proxy_ollama_request(
        endpoint="/api/chat",
        command="proxy:/v1/chat/completions",
        body=ollama_body,
        resource_profile="ollama",
        extract_stdout_fn=lambda r: str(r.get("message", {}).get("content", ""))[:500],
        error_prefix="Chat completion failed",
    )
    return _ollama_chat_to_openai_response(result, model=model)


@router.post("/v1/embeddings")
async def proxy_embeddings(body: dict = Body(...)):
    """OpenAI-compatible embeddings endpoint, serialized through the queue.

    Accepts OpenAI /v1/embeddings format; the request body shape is identical
    to Ollama /api/embed (model + input), so no request translation is needed.
    The response is wrapped in OpenAI list format.

    Queue-specific fields (extracted from body, not forwarded to Ollama):
      _priority: int (default 0)
      _source: str (default "proxy")
      _timeout: int (default 600)
    """
    body["stream"] = False
    body.setdefault("_source", "gpt-researcher")
    model = body.get("model", "")
    resource_profile = OllamaModels().classify(model)["resource_profile"] if model else "embed"
    result = await _proxy_ollama_request(
        endpoint="/api/embed",
        command="proxy:/v1/embeddings",
        body=body,
        resource_profile=resource_profile,
        extract_stdout_fn=lambda r: f"embeddings: {len(r.get('embeddings', []))} vectors",
        error_prefix="Embeddings request failed",
    )
    return _ollama_embed_to_openai_response(result)
