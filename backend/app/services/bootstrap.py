"""Boot-time helpers — pre-load Chart Champions methodology so the operator
never has to upload anything manually.

Runs only when DEV_NO_AUTH=true (single-operator mode). The PDFs live in
``backend/methodology/`` and are copied into the image at build time.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select

from ..core.config import settings
from ..db.session import AsyncSessionLocal
from ..models.document import Document, DocumentStatus
from ..models.user import Subscription, SubscriptionTier, User, UserRole

logger = logging.getLogger(__name__)

# Folder inside the container where the operator's PDFs are baked.
METHODOLOGY_DIR = Path("/app/methodology")


async def _get_or_create_demo_user_session(db) -> User:
    """Eager version of the demo-user creator used at boot."""
    email = settings.DEV_USER_EMAIL.lower()
    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        hashed_password="__no_auth_mode__",
        full_name="Demo Operator",
        role=UserRole.admin,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(Subscription(user_id=user.id, tier=SubscriptionTier.pro))
    await db.flush()
    return user


async def autoload_methodology() -> None:
    """Copy each PDF in /app/methodology into the demo user's uploads,
    create Document rows in 'pending' state, and trigger ingestion in the
    background. Idempotent — files already registered are skipped.
    """
    if not settings.DEV_NO_AUTH:
        return
    if not METHODOLOGY_DIR.exists():
        logger.info("no methodology directory at %s — skipping autoload", METHODOLOGY_DIR)
        return

    files = sorted(
        [p for p in METHODOLOGY_DIR.iterdir() if p.is_file() and p.suffix.lower() in {".pdf", ".txt"}]
    )
    if not files:
        return

    upload_root = Path(settings.UPLOAD_DIR)

    async with AsyncSessionLocal() as db:
        try:
            user = await _get_or_create_demo_user_session(db)
            user_uploads = upload_root / str(user.id)
            user_uploads.mkdir(parents=True, exist_ok=True)

            existing_res = await db.execute(
                select(Document).where(Document.user_id == user.id)
            )
            existing_filenames = {d.filename for d in existing_res.scalars()}

            to_ingest: list[uuid.UUID] = []
            for src in files:
                if src.name in existing_filenames:
                    continue
                dest = user_uploads / f"{uuid.uuid4()}_{src.name.replace(' ', '_')}"
                shutil.copy2(src, dest)
                size = dest.stat().st_size
                doc = Document(
                    user_id=user.id,
                    filename=src.name,
                    content_type="application/pdf" if src.suffix.lower() == ".pdf" else "text/plain",
                    size_bytes=size,
                    storage_path=str(dest),
                    status=DocumentStatus.pending,
                )
                db.add(doc)
                await db.flush()
                to_ingest.append(doc.id)
                logger.info("queued methodology file for ingestion: %s", src.name)

            await db.commit()
        except Exception as exc:
            logger.exception("methodology autoload failed: %s", exc)
            await db.rollback()
            return

    # Kick off ingestion as background tasks — don't block startup.
    if to_ingest:
        asyncio.create_task(_ingest_batch(to_ingest))


async def _ingest_batch(document_ids: list[uuid.UUID]) -> None:
    from .rag import IngestionService

    for did in document_ids:
        try:
            async with AsyncSessionLocal() as db:
                await IngestionService.ingest(db, document_id=did)
                await db.commit()
        except Exception as exc:
            logger.warning("background ingest of %s failed: %s", did, exc)
