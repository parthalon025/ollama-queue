"""Tests for Forge embedder — embeds items via Ollama or cache."""

from unittest.mock import MagicMock, patch

from ollama_queue.forge.embedder import content_hash, embed_items


def test_content_hash_deterministic():
    item = {"title": "foo", "one_liner": "bar", "description": "baz"}
    h1 = content_hash(item)
    h2 = content_hash(item)
    assert h1 == h2
    assert len(h1) == 16  # sha256[:16]


def test_content_hash_changes_on_content():
    a = content_hash({"title": "a", "one_liner": "", "description": ""})
    b = content_hash({"title": "b", "one_liner": "", "description": ""})
    assert a != b


def test_embed_items_uses_cache(db):
    """Cached embeddings are returned without calling Ollama."""
    item = {"id": "101", "title": "foo", "one_liner": "bar", "description": "baz"}
    ch = content_hash(item)
    db.store_forge_embedding("101", ch, [0.1, 0.2, 0.3])

    result = embed_items(
        db=db,
        items=[item],
        model="nomic-embed-text",
        http_base="http://127.0.0.1:7683",
    )
    assert "101" in result
    assert len(result["101"]) == 3


def test_embed_items_calls_ollama_on_miss(db):
    """Cache miss triggers Ollama embed call."""
    item = {"id": "101", "title": "foo", "one_liner": "bar", "description": "baz"}

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"embedding": [0.4, 0.5, 0.6]}

    with patch("ollama_queue.forge.embedder.httpx.post", return_value=mock_resp):
        result = embed_items(
            db=db,
            items=[item],
            model="nomic-embed-text",
            http_base="http://127.0.0.1:7683",
        )

    assert "101" in result
    assert result["101"] == [0.4, 0.5, 0.6]
    # Verify cached
    ch = content_hash(item)
    cached = db.get_forge_embedding("101", ch)
    assert cached == [0.4, 0.5, 0.6]


def test_embed_items_skips_failed_embed(db):
    """Failed Ollama call skips item, doesn't crash."""
    item = {"id": "101", "title": "foo", "one_liner": "bar", "description": "baz"}

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("ollama_queue.forge.embedder.httpx.post", return_value=mock_resp):
        result = embed_items(
            db=db,
            items=[item],
            model="nomic-embed-text",
            http_base="http://127.0.0.1:7683",
        )

    assert "101" not in result
