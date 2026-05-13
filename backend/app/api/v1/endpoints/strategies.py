"""Strategy listing — what was extracted from uploaded methodology."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.session import get_db
from ....models.document import Document
from ....models.strategy import ExtractedStrategy
from ....models.user import User
from ...deps import current_user

router = APIRouter(prefix="/strategies", tags=["strategies"])


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    name: str
    summary: Optional[str] = None
    rules_json: dict
    citations: Optional[list] = None
    is_deterministic: bool
    created_at: datetime


@router.get("", response_model=List[StrategyOut])
async def list_strategies(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> List[StrategyOut]:
    res = await db.execute(
        select(ExtractedStrategy)
        .join(Document, Document.id == ExtractedStrategy.document_id)
        .where(Document.user_id == user.id)
        .order_by(ExtractedStrategy.created_at.desc())
    )
    return [StrategyOut.model_validate(s) for s in res.scalars()]


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy(
    strategy_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> StrategyOut:
    res = await db.execute(
        select(ExtractedStrategy)
        .join(Document, Document.id == ExtractedStrategy.document_id)
        .where(ExtractedStrategy.id == strategy_id, Document.user_id == user.id)
    )
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy not found")
    return StrategyOut.model_validate(s)
