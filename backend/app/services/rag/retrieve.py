"""Retrieval: similarity search filtered by user_id (and optionally document)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from . import vectorstore
from .embed import embed


@dataclass(slots=True)
class RetrievedChunk:
    text: str
    score: float
    page: int | None
    document_id: str
    filename: str | None


class RAGService:
    @staticmethod
    async def retrieve(
        *,
        user_id: uuid.UUID,
        query: str,
        k: int = 8,
        document_id: Optional[uuid.UUID] = None,
    ) -> list[RetrievedChunk]:
        query_embedding = (await embed([query]))[0]
        where: dict = {"user_id": str(user_id)}
        if document_id is not None:
            where = {"$and": [{"user_id": str(user_id)}, {"document_id": str(document_id)}]}
        result = await vectorstore.query(
            query_embedding=query_embedding, n_results=k, where=where
        )
        out: list[RetrievedChunk] = []
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for doc_text, meta, dist in zip(docs, metas, distances, strict=False):
            # cosine distance → similarity ≈ 1 - distance
            out.append(
                RetrievedChunk(
                    text=doc_text,
                    score=max(0.0, 1.0 - float(dist)) if dist is not None else 0.0,
                    page=(meta or {}).get("page"),
                    document_id=(meta or {}).get("document_id", ""),
                    filename=(meta or {}).get("filename"),
                )
            )
        return out

    @staticmethod
    def format_context(chunks: list[RetrievedChunk]) -> str:
        """Plain-text concatenation used to inject retrieved context into LLM prompts."""
        parts = []
        for c in chunks:
            cite = f"[{c.filename or 'doc'} p{c.page or '?'}]"
            parts.append(f"{cite}\n{c.text}")
        return "\n\n---\n\n".join(parts)
