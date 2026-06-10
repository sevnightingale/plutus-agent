"""Pluggable text-embedding interface for plutus-agent.

Plutus uses voyage-finance-2 (Voyage AI's finance-domain-specialized model,
1024 dims) as the default embedder. Theses and reflections are embedded
synchronously inside ``record_event`` so the row and its vec0 entry are
written atomically; ``find_similar_*`` queries embed the query text the
same way.

The interface keeps providers swappable (we expect to revisit if voyage
pricing/availability changes). The OpenAI text-embedding-3-small adapter
is the documented fallback (1536 dims) but is not exercised in Phase 4a.

Per the global "no silent fallbacks" rule: ``get_embedder()`` raises if no
viable provider is configured. Don't add a noop or deterministic-mock
fallback here — let it fail loud.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from plutus_constants import get_hermes_home


def _load_env() -> None:
    """Best-effort load of ~/.plutus-agent/.env so VOYAGE_API_KEY resolves.

    No-op when the file is absent or env var is already set.
    """
    env_path = get_hermes_home() / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


class EmbedderError(RuntimeError):
    """Raised on configuration or upstream-API failures from an embedder."""


class EmbedderInterface(ABC):
    """Embedder protocol.

    Subclasses MUST set ``model_name`` and ``dimension`` and implement
    ``embed_documents`` and ``embed_query``. The split mirrors voyage's
    ``input_type`` distinction — symmetric models can return identical
    results from both methods.
    """

    model_name: str = ""
    dimension: int = 0

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed corpus texts (theses, reflections) for storage in vec0."""

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query text for ``find_similar_*`` retrieval."""


class VoyageFinanceEmbedder(EmbedderInterface):
    """voyage-finance-2 (1024 dims) via the Voyage AI Python SDK."""

    model_name = "voyage-finance-2"
    dimension = 1024

    def __init__(self, api_key: Optional[str] = None):
        # Imported lazily so test environments without the package can still
        # import this module (and rely on get_embedder raising explicitly).
        import voyageai

        key = api_key or os.environ.get("VOYAGE_API_KEY", "").strip()
        if not key:
            raise EmbedderError(
                "VOYAGE_API_KEY not set. Add it to ~/.plutus-agent/.env or "
                "the process environment before instantiating "
                "VoyageFinanceEmbedder."
            )
        self._client = voyageai.Client(api_key=key)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        result = self._client.embed(
            texts=texts,
            model=self.model_name,
            input_type="document",
        )
        return [list(v) for v in result.embeddings]

    def embed_query(self, text: str) -> List[float]:
        if not text:
            raise EmbedderError("embed_query requires a non-empty text")
        result = self._client.embed(
            texts=[text],
            model=self.model_name,
            input_type="query",
        )
        return list(result.embeddings[0])


class OpenAIEmbedder(EmbedderInterface):
    """text-embedding-3-small (1536 dims) — documented fallback only.

    Present so a future swap is a config change, not a code change. NOT
    exercised in Phase 4a. The vec0 virtual tables are pinned to FLOAT[1024]
    today, so swapping to this provider also requires re-creating the vec0
    tables at FLOAT[1536] (re-embed script territory).
    """

    model_name = "text-embedding-3-small"
    dimension = 1536

    def __init__(self, api_key: Optional[str] = None):
        from openai import OpenAI

        key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise EmbedderError(
                "OPENAI_API_KEY not set. Required for OpenAIEmbedder."
            )
        self._client = OpenAI(api_key=key)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(
            model=self.model_name,
            input=texts,
        )
        return [list(item.embedding) for item in resp.data]

    def embed_query(self, text: str) -> List[float]:
        if not text:
            raise EmbedderError("embed_query requires a non-empty text")
        resp = self._client.embeddings.create(
            model=self.model_name,
            input=[text],
        )
        return list(resp.data[0].embedding)


_embedder_singleton: Optional[EmbedderInterface] = None


def get_embedder() -> EmbedderInterface:
    """Return the process-wide embedder, instantiated on first use.

    Default: VoyageFinanceEmbedder (voyage-finance-2). Raises EmbedderError
    if VOYAGE_API_KEY is not set — explicit failure rather than silent
    fallback to a different model with a different dimension.

    Future: read embedder-provider config from ``~/.plutus-agent/config.yaml``
    and route to the appropriate adapter.
    """
    global _embedder_singleton
    if _embedder_singleton is None:
        _load_env()
        _embedder_singleton = VoyageFinanceEmbedder()
    return _embedder_singleton


def reset_embedder_singleton() -> None:
    """Test-only: clear the singleton so the next ``get_embedder`` rebuilds."""
    global _embedder_singleton
    _embedder_singleton = None
