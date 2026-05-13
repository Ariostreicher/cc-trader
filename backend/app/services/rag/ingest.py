"""Coordinator: extract → chunk → embed → persist (Postgres + ChromaDB).

Used both from the synchronous upload endpoint (small docs) and the
background worker (large docs).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.document import Document, DocumentChunk, DocumentStatus
from . import vectorstore
from .chunk import chunk_pages
from .embed import embed
from .extract import extract

logger = logging.getLogger(__name__)


class IngestionService:
    @staticmethod
    async def ingest(db: AsyncSession, *, document_id: uuid.UUID) -> None:
        res = await db.execute(select(Document).where(Document.id == document_id))
        doc: Document | None = res.scalar_one_or_none()
        if doc is None:
            raise ValueError(f"document {document_id} not found")

        try:
            await IngestionService._run(db, doc)
        except Exception as exc:
            logger.exception("ingestion failed for %s: %s", doc.id, exc)
            doc.status = DocumentStatus.failed
            doc.error = str(exc)[:2000]
            await db.flush()
            raise

    @staticmethod
    async def _run(db: AsyncSession, doc: Document) -> None:
        doc.status = DocumentStatus.extracting
        await db.flush()

        pages = extract(doc.storage_path, doc.content_type)
        page_pairs = [(p.page_number, p.text) for p in pages]
        full_text_len = sum(len(t) for _, t in page_pairs)

        doc.page_count = len(page_pairs)
        doc.extracted_text_len = full_text_len
        doc.status = DocumentStatus.chunking
        await db.flush()

        chunks = chunk_pages(page_pairs)
        if not chunks:
            doc.status = DocumentStatus.ready
            doc.error = "no extractable text"
            await db.flush()
            return

        doc.status = DocumentStatus.embedding
        await db.flush()

        embeddings = await embed([c.text for c in chunks])

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []
        records: list[DocumentChunk] = []

        for c, vec in zip(chunks, embeddings, strict=True):
            chroma_id = f"{doc.id}:{c.chunk_index}"
            ids.append(chroma_id)
            documents.append(c.text)
            metadatas.append(
                {
                    "user_id": str(doc.user_id),
                    "document_id": str(doc.id),
                    "filename": doc.filename,
                    "page": c.page_number,
                    "chunk_index": c.chunk_index,
                }
            )
            records.append(
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=c.chunk_index,
                    page_number=c.page_number,
                    text=c.text,
                    token_count=c.token_count,
                    chroma_id=chroma_id,
                )
            )

        # Drop any existing chunks (re-ingest case).
        await vectorstore.delete(where={"document_id": str(doc.id)})

        await vectorstore.add(
            ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas
        )

        db.add_all(records)
        doc.status = DocumentStatus.ready
        doc.error = None
        await db.flush()
        logger.info("ingested %s: %d pages, %d chunks", doc.filename, doc.page_count, len(chunks))


def safe_storage_path(upload_dir: str, user_id: uuid.UUID, filename: str) -> Path:
    """Returns a per-user storage path; filenames are forced to a UUID prefix
    so user-supplied names cannot collide or escape the directory."""
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[-128:]
    return Path(upload_dir) / str(user_id) / f"{uuid.uuid4()}_{safe_name}"
