"""Paper trading endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ....db.session import get_db
from ....models.portfolio import PaperTrade, Portfolio
from ....models.user import User
from ....schemas.portfolio import PaperOrderIn, PaperTradeOut, PortfolioFull
from ....services.paper_trading import close_trade, get_or_create_portfolio, open_trade
from ...deps import current_user

router = APIRouter(prefix="/paper", tags=["paper-trading"])


@router.get("/portfolio", response_model=PortfolioFull)
async def get_portfolio(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> PortfolioFull:
    portfolio = await get_or_create_portfolio(db, user_id=user.id)
    res = await db.execute(
        select(Portfolio)
        .where(Portfolio.id == portfolio.id)
        .options(selectinload(Portfolio.trades))
    )
    p = res.scalar_one()
    return PortfolioFull.model_validate(p)


@router.post(
    "/orders", response_model=PaperTradeOut, status_code=status.HTTP_201_CREATED
)
async def place_order(
    payload: PaperOrderIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> PaperTradeOut:
    trade = await open_trade(
        db,
        user_id=user.id,
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        stop_loss=payload.stop_loss,
        take_profit=payload.take_profit,
        journal=payload.journal,
    )
    return PaperTradeOut.model_validate(trade)


@router.post("/orders/{trade_id}/close", response_model=PaperTradeOut)
async def close_order(
    trade_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> PaperTradeOut:
    trade = await close_trade(db, user_id=user.id, trade_id=trade_id)
    return PaperTradeOut.model_validate(trade)
