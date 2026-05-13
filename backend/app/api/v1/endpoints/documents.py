"""Document upload + status + delete."""

from __future__ import annotations

import os
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.config import settings
from ....db.session import AsyncSessionLocal, get_db
from ....models.document import Document, DocumentStatus
from ....models.user import User
from ....schemas.document import DocumentOut
from ....services.rag import IngestionService
from ....services.rag.ingest import safe_storage_path
from ....services.rag.vectorstore import delete as vector_delete
from ...deps import current_user

router = APIRouter(prefix="/documents", tags=["documents"])


async def _ingest_in_background(document_id) -> None:
    async with AsyncSessionLocal() as db:
        try:
            await IngestionService.ingest(db, document_id=document_id)
            await db.commit()
        except Exception:
            await db.rollback()


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    size_limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    contents = await file.read()
    if len(contents) > size_limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")

    storage_path = safe_storage_path(settings.UPLOAD_DIR, user.id, file.filename or "upload")
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(contents)

    doc = Document(
        user_id=user.id,
        filename=file.filename or storage_path.name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(contents),
        storage_path=str(storage_path),
        status=DocumentStatus.pending,
    )
    db.add(doc)
    await db.flush()
    await db.commit()  # ensure the row exists for the background task

    background.add_task(_ingest_in_background, doc.id)
    return DocumentOut.model_validate(doc)


@router.get("", response_model=List[DocumentOut])
async def list_documents(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> List[DocumentOut]:
    res = await db.execute(
        select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())
    )
    return [DocumentOut.model_validate(d) for d in res.scalars()]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    res = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
    )
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return DocumentOut.model_validate(doc)


@router.delete("/{document_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    res = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
    )
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    try:
        os.unlink(doc.storage_path)
    except FileNotFoundError:
        pass
    await vector_delete(where={"document_id": str(doc.id)})
    await db.delete(doc)
