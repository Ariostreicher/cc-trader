"""Thin async wrapper over a single ChromaDB collection.

Documents are partitioned across users via the metadata.user_id filter so a
single collection serves the whole tenant base safely.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ...core.config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "cc_trader_corpus"


class _ChromaClient:
    def __init__(self) -> None:
        self._client = None
        self._collection = None

    def _ensure(self):
        if self._client is not None:
            return
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("chromadb is required") from e

        # HttpClient is preferred for the containerised setup; a fallback to
        # an in-process Persistent client makes local dev simpler.
        try:
            self._client = chromadb.HttpClient(
                host=settings.CHROMA_HOST,
                port=settings.CHROMA_PORT,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        except Exception as exc:
            logger.warning("Falling back to PersistentClient: %s", exc)
            self._client = chromadb.PersistentClient(path="/data/chroma")
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )

    @property
    def collection(self):
        self._ensure()
        return self._collection


_client = _ChromaClient()


def _add_sync(
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
) -> None:
    _client.collection.add(
        ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas
    )


def _query_sync(
    query_embedding: list[float],
    *,
    n_results: int,
    where: Optional[dict],
) -> dict:
    return _client.collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where or None,
    )


def _delete_sync(*, where: dict) -> None:
    _client.collection.delete(where=where)


async def add(
    *,
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
) -> None:
    await asyncio.to_thread(_add_sync, ids, documents, embeddings, metadatas)


async def query(
    *,
    query_embedding: list[float],
    n_results: int = 8,
    where: Optional[dict] = None,
) -> dict:
    return await asyncio.to_thread(_query_sync, query_embedding, n_results=n_results, where=where)


async def delete(*, where: dict) -> None:
    await asyncio.to_thread(_delete_sync, where=where)
