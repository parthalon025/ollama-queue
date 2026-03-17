"""Embed items via Ollama's /api/embed endpoint, with DB caching."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


def content_hash(item: dict) -> str:
    """Deterministic hash of item text fields. Used as cache key."""
    text = f"{item.get('title', '')}|{item.get('one_liner', '')}|{item.get('description', '')}"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def embed_items(
    *,
    db: Database,
    items: list[dict],
    model: str,
    http_base: str,
    timeout: int = 30,
) -> dict[str, list[float]]:
    """Embed items, using DB cache where available.

    Returns: {item_id: vector} for all successfully embedded items.
    Missing items (cache miss + Ollama failure) are silently skipped.
    """
    result: dict[str, list[float]] = {}

    for item in items:
        item_id = item["id"]
        ch = content_hash(item)

        # Check cache first
        cached = db.get_forge_embedding(item_id, ch)
        if cached is not None:
            result[item_id] = cached
            continue

        # Cache miss — call Ollama
        text = f"{item.get('title', '')} {item.get('one_liner', '')} {item.get('description', '')}"
        try:
            resp = httpx.post(
                f"{http_base}/api/embed",
                json={"model": model, "input": text},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            _log.warning("forge embedder: HTTP error for item %s: %s", item_id, exc)
            continue

        if resp.status_code != 200:
            _log.warning("forge embedder: status %d for item %s", resp.status_code, item_id)
            continue

        data = resp.json()
        vector = data.get("embedding") or (data.get("embeddings") or [None])[0]
        if vector is None:
            _log.warning("forge embedder: no embedding in response for item %s", item_id)
            continue

        db.store_forge_embedding(item_id, ch, vector)
        result[item_id] = vector

    _log.info("forge embedder: %d/%d items embedded", len(result), len(items))
    return result
