"""Alert CRUD + manual test endpoint."""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.session import get_db
from ....models.alert import Alert
from ....models.user import User
from ....schemas.alert import AlertIn, AlertOut
from ....services.alerts.evaluator import evaluate
from ....services.market.registry import MarketDataService
from ...deps import current_user

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=List[AlertOut])
async def list_alerts(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> List[AlertOut]:
    res = await db.execute(
        select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc())
    )
    return [AlertOut.model_validate(a) for a in res.scalars()]


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
async def create_alert(
    payload: AlertIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AlertOut:
    a = Alert(
        user_id=user.id,
        symbol=payload.symbol.upper(),
        trigger=payload.trigger,
        params=payload.params,
        cooldown_seconds=payload.cooldown_seconds,
        channels=[c.value for c in payload.channels],
        note=payload.note,
        is_enabled=payload.is_enabled,
    )
    db.add(a)
    await db.flush()
    return AlertOut.model_validate(a)


@router.patch("/{alert_id}", response_model=AlertOut)
async def update_alert(
    alert_id: uuid.UUID,
    payload: AlertIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AlertOut:
    res = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id)
    )
    a = res.scalar_one_or_none()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    a.symbol = payload.symbol.upper()
    a.trigger = payload.trigger
    a.params = payload.params
    a.cooldown_seconds = payload.cooldown_seconds
    a.channels = [c.value for c in payload.channels]
    a.note = payload.note
    a.is_enabled = payload.is_enabled
    return AlertOut.model_validate(a)


@router.delete("/{alert_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    res = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id)
    )
    a = res.scalar_one_or_none()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    await db.delete(a)


@router.post("/{alert_id}/test")
async def test_alert(
    alert_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    res = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id)
    )
    a = res.scalar_one_or_none()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    df = await MarketDataService.bars(a.symbol, timeframe="1d", days=365)
    decision = evaluate(a, df)
    return {
        "should_fire": decision.should_fire,
        "reason": decision.reason,
        "payload": decision.payload,
    }
