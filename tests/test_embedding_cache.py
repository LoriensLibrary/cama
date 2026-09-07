"""Tests for the query-embedding cache.

The point of the cache is that a cold process must not have to import
sentence-transformers to answer a query it has already answered. The
behavioral test at the bottom is the one that pins that: it counts calls
into the encoder, not just rows in a table.
"""

import asyncio

import pytest

from cama.core import embedding_cache

VEC = [0.1, -0.2, 0.3, 0.4]
MODEL = "local:all-MiniLM-L6-v2"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMA_EMBEDDING_CACHE", str(tmp_path / "embedding_cache.db"))
    embedding_cache.clear()
    return embedding_cache


def test_round_trip(cache):
    cache.put("a query", MODEL, VEC)
    got = cache.get("a query", MODEL)
    assert got is not None
    assert len(got) == len(VEC)
    for a, b in zip(got, VEC):
        assert a == pytest.approx(b, abs=1e-6), "float32 round trip"


def test_miss_returns_none(cache):
    assert cache.get("never stored", MODEL) is None


def test_model_is_part_of_the_key(cache):
    """A local vector must never be served as an API vector: the two
    models produce different spaces and often different dimensions."""
    cache.put("same text", MODEL, VEC)
    assert cache.get("same text", "api:text-embedding-3-small") is None


def test_long_text_is_not_cached(cache):
    long_text = "x" * (embedding_cache.MAX_TEXT_CHARS + 1)
    cache.put(long_text, MODEL, VEC)
    assert cache.get(long_text, MODEL) is None
    assert cache.stats()["entries"] == 0


def test_empty_vector_is_not_cached(cache):
    cache.put("query", MODEL, [])
    assert cache.get("query", MODEL) is None


def test_repeated_put_overwrites_rather_than_duplicating(cache):
    cache.put("q", MODEL, [1.0, 2.0])
    cache.put("q", MODEL, [3.0, 4.0])
    assert cache.stats()["entries"] == 1
    assert cache.get("q", MODEL) == pytest.approx([3.0, 4.0])


def test_eviction_caps_the_table(cache, monkeypatch):
    monkeypatch.setattr(embedding_cache, "MAX_ENTRIES", 5)
    for i in range(12):
        cache.put(f"query-{i}", MODEL, [float(i), 1.0])
    entries = cache.stats()["entries"]
    assert entries <= 5, f"cache grew past the cap: {entries}"
    # The most recent writes are the ones that survive.
    assert cache.get("query-11", MODEL) is not None


def test_hits_are_counted(cache):
    cache.put("q", MODEL, VEC)
    cache.get("q", MODEL)
    cache.get("q", MODEL)
    assert cache.stats()["hits"] == 2


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMA_EMBEDDING_CACHE", "off")
    assert embedding_cache.cache_path() is None
    embedding_cache.put("q", MODEL, VEC)
    assert embedding_cache.get("q", MODEL) is None
    assert embedding_cache.stats()["enabled"] is False


def test_unwritable_location_does_not_raise(tmp_path, monkeypatch):
    """A boot must never fail because the cache could not be opened."""
    bad = tmp_path / "not-a-dir"
    bad.write_text("this is a file, not a directory")
    monkeypatch.setenv("CAMA_EMBEDDING_CACHE", str(bad / "cache.db"))
    embedding_cache.put("q", MODEL, VEC)
    assert embedding_cache.get("q", MODEL) is None


def test_corrupt_row_is_a_miss_not_a_crash(cache):
    import sqlite3

    cache.put("q", MODEL, VEC)
    c = sqlite3.connect(cache.cache_path())
    # Claim four dimensions but store one float's worth of bytes.
    c.execute("UPDATE query_embeddings SET vec = ?", (b"\x00\x00\x00\x00",))
    c.commit()
    c.close()
    assert cache.get("q", MODEL) is None


# ---------------------------------------------------------------------------
# The behavior the cache exists for
# ---------------------------------------------------------------------------
def test_get_embedding_hits_the_encoder_once(cache, monkeypatch):
    """Second call for the same text must not reach the encoder.

    In the boot hook the encoder is not merely slow to run, it is slow to
    reach: importing sentence-transformers costs about 22 seconds in a
    fresh process. Counting calls is what proves that import is skipped.
    """
    import cama_mcp

    calls = []

    def fake_local(text):
        calls.append(text)
        return [0.5, 0.25, 0.125]

    monkeypatch.setattr(cama_mcp, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(cama_mcp, "_get_embedding_local", fake_local)

    first = asyncio.run(cama_mcp._get_embedding("Angela is here. New thread."))
    second = asyncio.run(cama_mcp._get_embedding("Angela is here. New thread."))

    assert len(calls) == 1, f"encoder was reached {len(calls)} times, expected 1"
    assert first == pytest.approx(second)
    assert first == pytest.approx([0.5, 0.25, 0.125])


def test_get_embedding_does_not_cache_across_providers(cache, monkeypatch):
    import cama_mcp

    monkeypatch.setattr(cama_mcp, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(cama_mcp, "_get_embedding_local", lambda t: [1.0, 0.0])
    asyncio.run(cama_mcp._get_embedding("shared text"))

    api_calls = []

    async def fake_api(text):
        api_calls.append(text)
        return [0.0, 1.0]

    monkeypatch.setattr(cama_mcp, "EMBEDDING_PROVIDER", "api")
    monkeypatch.setattr(cama_mcp, "_get_embedding_api", fake_api)
    got = asyncio.run(cama_mcp._get_embedding("shared text"))

    assert api_calls == ["shared text"], "the local vector must not satisfy an API request"
    assert got == pytest.approx([0.0, 1.0])


def test_get_embedding_empty_text_never_touches_the_cache(cache, monkeypatch):
    import cama_mcp

    monkeypatch.setattr(cama_mcp, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(cama_mcp, "_get_embedding_local", lambda t: [1.0])
    assert asyncio.run(cama_mcp._get_embedding("")) == []
    assert cache.stats()["entries"] == 0


def test_get_embedding_failure_is_not_cached(cache, monkeypatch):
    """An encoder that returns nothing must leave no row behind, or the
    failure would be served forever."""
    import cama_mcp

    monkeypatch.setattr(cama_mcp, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(cama_mcp, "_get_embedding_local", lambda t: [])
    assert asyncio.run(cama_mcp._get_embedding("q")) == []
    assert cache.stats()["entries"] == 0
