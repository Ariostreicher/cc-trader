"""Batch embedding via any OpenAI-compatible API.

Provider selection mirrors the chat LLM:
- OPENAI_BASE_URL blank      → real OpenAI
- OPENAI_BASE_URL set        → Groq / Ollama / Together / OpenRouter / etc.
- OPENAI_API_KEY blank       → deterministic hash-vector fallback so dev
                               and unit-test environments still function.

Groq doesn't host embedding models today; if you're on Groq for chat, set
OPENAI_MODEL_EMBED to a model your *embedding* provider serves (e.g.
``nomic-embed-text`` on Ollama, ``text-embedding-3-small`` on OpenAI).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Sequence

from ...core.config import settings

logger = logging.getLogger(__name__)

# Hash-fallback vector dimension. Chosen so it differs from real OpenAI
# embeddings (3072 for text-embedding-3-large) and is detectable downstream.
HASH_VECTOR_DIM = 384


async def embed(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    if settings.OPENAI_API_KEY:
        try:
            return await _embed_openai(list(texts))
        except Exception as exc:
            # Many free LLM providers (e.g. Groq) refuse embedding calls.
            # Fall back gracefully so document ingestion still completes —
            # RAG retrieval quality is lower but the equity model still
            # runs (the Master Instruction Block is sent verbatim regardless).
            logger.warning(
                "embedding provider rejected request (%s) — falling back to hash vectors",
                exc,
            )
            return [_hash_vector(t) for t in texts]
    logger.warning("OPENAI_API_KEY missing — using deterministic hash embeddings")
    return [_hash_vector(t) for t in texts]


async def _embed_openai(texts: list[str]) -> list[list[float]]:
    from openai import AsyncOpenAI

    # Honor OPENAI_BASE_URL so self-hosted (Ollama) or alternative
    # embedding providers work via the same code path.
    client_kwargs: dict = {"api_key": settings.OPENAI_API_KEY}
    if settings.OPENAI_BASE_URL:
        client_kwargs["base_url"] = settings.OPENAI_BASE_URL
    client = AsyncOpenAI(**client_kwargs)

    out: list[list[float]] = []
    # OpenAI accepts batches; we cap at 96 to stay well under the per-request
    # token cap.
    batch_size = 96
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = await client.embeddings.create(model=settings.OPENAI_MODEL_EMBED, input=batch)
        out.extend([d.embedding for d in resp.data])
    return out


def _hash_vector(text: str) -> list[float]:
    """Cheap, deterministic 'embedding' for offline tests.
    Same text → same vector; cosine distance still works."""
    digest = hashlib.sha512(text.encode("utf-8")).digest()
    # Repeat to fill the dimension; map bytes (0-255) into [-1, 1].
    repeats = (HASH_VECTOR_DIM // len(digest)) + 1
    raw = (digest * repeats)[:HASH_VECTOR_DIM]
    return [(b - 128) / 128.0 for b in raw]
