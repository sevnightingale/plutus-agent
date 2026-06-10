"""Tests for tools/core/embedder.py — real Voyage AI calls.

Per the Phase 4a plan, embedder tests run against the live voyage-finance-2
API (no mocking). Cost is trivial; mocking would add no signal at this volume
and would risk drifting from the real API contract.
"""

import os

import pytest

from trading.perception.core.embedder import (
    EmbedderError,
    OpenAIEmbedder,
    VoyageFinanceEmbedder,
    get_embedder,
    reset_embedder_singleton,
)


def _cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


@pytest.fixture(autouse=True)
def _clear_singleton():
    reset_embedder_singleton()
    yield
    reset_embedder_singleton()


@pytest.fixture()
def voyage_key_required(monkeypatch):
    """Restore VOYAGE_API_KEY from the real ~/.plutus-agent/.env.

    The autouse ``_hermetic_environment`` fixture in tests/conftest.py
    deliberately scrubs every ``*_API_KEY`` env var so accidental network
    calls in tests can't burn real credentials. These embedder tests opt
    back IN by reading the operator's real .env directly (bypassing the
    redirected HERMES_HOME) and reseating the key for this test only.

    Skips if the file/key is unavailable so CI without a key still passes.
    """
    from pathlib import Path
    from dotenv import dotenv_values

    real_env = Path.home() / ".plutus-agent" / ".env"
    if not real_env.exists():
        pytest.skip(f"{real_env} not present; skipping live embedder tests")
    values = dotenv_values(real_env)
    key = (values.get("VOYAGE_API_KEY") or "").strip()
    if not key:
        pytest.skip("VOYAGE_API_KEY not set in ~/.plutus-agent/.env")
    monkeypatch.setenv("VOYAGE_API_KEY", key)


# =========================================================================
# VoyageFinanceEmbedder — real API
# =========================================================================

class TestVoyageFinanceEmbedder:
    def test_model_metadata(self, voyage_key_required):
        e = VoyageFinanceEmbedder()
        assert e.model_name == "voyage-finance-2"
        assert e.dimension == 1024

    def test_embed_documents_returns_1024_dims(self, voyage_key_required):
        e = VoyageFinanceEmbedder()
        vectors = e.embed_documents([
            "BTC funding rate flipped negative; coiled below 70k.",
            "ETH breakout above 3500 with strong delta imbalance.",
        ])
        assert len(vectors) == 2
        assert all(len(v) == 1024 for v in vectors)
        assert all(isinstance(x, float) for x in vectors[0])

    def test_embed_documents_empty_list(self, voyage_key_required):
        assert VoyageFinanceEmbedder().embed_documents([]) == []

    def test_embed_query_returns_1024_dims(self, voyage_key_required):
        v = VoyageFinanceEmbedder().embed_query(
            "Find similar trades to: BTC short on funding flip"
        )
        assert len(v) == 1024
        assert all(isinstance(x, float) for x in v)

    def test_embed_query_empty_raises(self, voyage_key_required):
        with pytest.raises(EmbedderError):
            VoyageFinanceEmbedder().embed_query("")

    def test_finance_domain_quality_signal(self, voyage_key_required):
        """Sanity check that finance paraphrases cluster closer than unrelated text.

        Finance-specialized model should score the two perp-funding theses
        closer to each other than to a fully off-topic gardening sentence.
        """
        e = VoyageFinanceEmbedder()
        v_a, v_b, v_c = e.embed_documents([
            "BTC perp funding flipped negative — shorts paying longs.",
            "Negative funding on Bitcoin perpetuals; longs accumulating.",
            "Tomato seedlings prefer indirect light during early spring.",
        ])
        sim_paraphrase = _cosine(v_a, v_b)
        sim_offtopic = _cosine(v_a, v_c)
        assert sim_paraphrase > sim_offtopic, (
            f"Expected paraphrase similarity ({sim_paraphrase:.3f}) "
            f"> off-topic similarity ({sim_offtopic:.3f})"
        )

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        # Also override _load_env so the fixture file doesn't repopulate it
        from trading.perception.core import embedder as em
        monkeypatch.setattr(em, "_load_env", lambda: None)
        with pytest.raises(EmbedderError, match="VOYAGE_API_KEY"):
            VoyageFinanceEmbedder()


# =========================================================================
# get_embedder factory
# =========================================================================

class TestGetEmbedderFactory:
    def test_returns_voyage_by_default(self, voyage_key_required):
        e = get_embedder()
        assert isinstance(e, VoyageFinanceEmbedder)

    def test_singleton_identity(self, voyage_key_required):
        a = get_embedder()
        b = get_embedder()
        assert a is b

    def test_reset_drops_singleton(self, voyage_key_required):
        a = get_embedder()
        reset_embedder_singleton()
        b = get_embedder()
        assert a is not b

    def test_factory_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        from trading.perception.core import embedder as em
        monkeypatch.setattr(em, "_load_env", lambda: None)
        reset_embedder_singleton()
        with pytest.raises(EmbedderError):
            get_embedder()


# =========================================================================
# OpenAIEmbedder skeleton (constructor only — not exercised against API in 4a)
# =========================================================================

class TestOpenAIEmbedderSkeleton:
    def test_metadata(self):
        # Don't construct — that requires OPENAI_API_KEY. Just check the class
        # carries the documented metadata so future swap is a config change.
        assert OpenAIEmbedder.model_name == "text-embedding-3-small"
        assert OpenAIEmbedder.dimension == 1536

    def test_missing_openai_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(EmbedderError, match="OPENAI_API_KEY"):
            OpenAIEmbedder()
