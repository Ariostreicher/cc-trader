"""Admin-only endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.session import get_db
from ....models.audit import AdminLog, AIAnalysisHistory, BillingRecord
from ....models.equity import EquityReport
from ....models.user import Subscription, SubscriptionTier, User, UserRole
from ...deps import current_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(current_admin)])


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: Optional[str]
    role: UserRole
    is_active: bool
    is_verified: bool
    tier: SubscriptionTier = SubscriptionTier.free
    created_at: datetime


class HealthOut(BaseModel):
    users_total: int
    users_active: int
    paying_users: int
    equity_reports_total: int
    ai_calls_today: int
    ai_cost_usd_today: float


@router.get("/users", response_model=List[AdminUserOut])
async def list_users(db: AsyncSession = Depends(get_db)) -> List[AdminUserOut]:
    res = await db.execute(
        select(User, Subscription)
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .order_by(User.created_at.desc())
    )
    out: list[AdminUserOut] = []
    for user, sub in res.all():
        data = AdminUserOut.model_validate(user)
        data.tier = sub.tier if sub else SubscriptionTier.free
        out.append(data)
    return out


@router.patch("/users/{user_id}/disable", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def disable_user(
    user_id: uuid.UUID,
    admin: User = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    res = await db.execute(select(User).where(User.id == user_id))
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    target.is_active = False
    db.add(AdminLog(actor_id=admin.id, action="disable_user", target_id=user_id))


@router.patch("/users/{user_id}/enable", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def enable_user(
    user_id: uuid.UUID,
    admin: User = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    res = await db.execute(select(User).where(User.id == user_id))
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    target.is_active = True
    db.add(AdminLog(actor_id=admin.id, action="enable_user", target_id=user_id))


@router.get("/health", response_model=HealthOut)
async def system_health(db: AsyncSession = Depends(get_db)) -> HealthOut:
    from datetime import timezone

    today = datetime.now(timezone.utc).date()

    users_total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    users_active = (
        await db.execute(select(func.count()).select_from(User).where(User.is_active.is_(True)))
    ).scalar_one()
    paying_users = (
        await db.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.tier != SubscriptionTier.free)
        )
    ).scalar_one()
    equity_reports_total = (
        await db.execute(select(func.count()).select_from(EquityReport))
    ).scalar_one()
    ai_calls_today = (
        await db.execute(
            select(func.count())
            .select_from(AIAnalysisHistory)
            .where(func.date(AIAnalysisHistory.created_at) == today)
        )
    ).scalar_one()
    ai_cost_usd_today = (
        await db.execute(
            select(func.coalesce(func.sum(AIAnalysisHistory.cost_usd), 0))
            .where(func.date(AIAnalysisHistory.created_at) == today)
        )
    ).scalar_one()

    return HealthOut(
        users_total=int(users_total),
        users_active=int(users_active),
        paying_users=int(paying_users),
        equity_reports_total=int(equity_reports_total),
        ai_calls_today=int(ai_calls_today),
        ai_cost_usd_today=float(ai_cost_usd_today or 0.0),
    )


@router.get("/billing", response_model=List[dict])
async def list_billing(db: AsyncSession = Depends(get_db)) -> List[dict]:
    res = await db.execute(
        select(BillingRecord).order_by(BillingRecord.created_at.desc()).limit(200)
    )
    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "event": r.event_type,
            "amount_cents": r.amount_cents,
            "currency": r.currency,
            "created_at": r.created_at.isoformat(),
        }
        for r in res.scalars()
    ]
