"""Paper trading service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.portfolio import PaperTrade, Portfolio, TradeSide, TradeStatus
from ..market.registry import MarketDataService


async def get_or_create_portfolio(db: AsyncSession, *, user_id: uuid.UUID) -> Portfolio:
    res = await db.execute(select(Portfolio).where(Portfolio.user_id == user_id))
    p = res.scalars().first()
    if p:
        return p
    p = Portfolio(user_id=user_id)
    db.add(p)
    await db.flush()
    return p


async def open_trade(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    symbol: str,
    side: TradeSide,
    quantity: float,
    stop_loss: float | None,
    take_profit: float | None,
    journal: str | None,
) -> PaperTrade:
    portfolio = await get_or_create_portfolio(db, user_id=user_id)
    quote = await MarketDataService.quote(symbol)
    if quote.price <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "could not price symbol")

    notional = Decimal(str(quote.price)) * Decimal(str(quantity))
    if Decimal(str(portfolio.cash)) < notional and side == TradeSide.long:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "insufficient paper cash")
    if side == TradeSide.long:
        portfolio.cash = float(Decimal(str(portfolio.cash)) - notional)
    # For shorts in paper mode we hold the proceeds as cash and net later.
    else:
        portfolio.cash = float(Decimal(str(portfolio.cash)) + notional)

    trade = PaperTrade(
        portfolio_id=portfolio.id,
        symbol=symbol.upper(),
        side=side,
        quantity=quantity,
        entry_price=quote.price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        status=TradeStatus.open,
        opened_at=datetime.now(timezone.utc),
        journal=journal,
    )
    db.add(trade)
    await db.flush()
    return trade


async def close_trade(db: AsyncSession, *, user_id: uuid.UUID, trade_id: uuid.UUID) -> PaperTrade:
    res = await db.execute(
        select(PaperTrade)
        .join(Portfolio, Portfolio.id == PaperTrade.portfolio_id)
        .where(PaperTrade.id == trade_id, Portfolio.user_id == user_id)
    )
    trade = res.scalar_one_or_none()
    if not trade:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trade not found")
    if trade.status != TradeStatus.open:
        raise HTTPException(status.HTTP_409_CONFLICT, "trade not open")

    portfolio_res = await db.execute(select(Portfolio).where(Portfolio.id == trade.portfolio_id))
    portfolio = portfolio_res.scalar_one()

    quote = await MarketDataService.quote(trade.symbol)
    exit_price = Decimal(str(quote.price))
    entry = Decimal(str(trade.entry_price))
    qty = Decimal(str(trade.quantity))

    if trade.side == TradeSide.long:
        pnl = (exit_price - entry) * qty
        portfolio.cash = float(Decimal(str(portfolio.cash)) + exit_price * qty)
    else:
        pnl = (entry - exit_price) * qty
        portfolio.cash = float(Decimal(str(portfolio.cash)) - exit_price * qty)

    portfolio.realized_pnl = float(Decimal(str(portfolio.realized_pnl)) + pnl)
    trade.exit_price = float(exit_price)
    trade.pnl = float(pnl)
    trade.status = TradeStatus.closed
    trade.closed_at = datetime.now(timezone.utc)
    await db.flush()
    return trade
