"""Backtest endpoints."""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.session import get_db
from ....models.backtest import Backtest, BacktestStatus
from ....models.user import User
from ....schemas.backtest import BacktestIn, BacktestOut
from ....services.backtesting.engine import run_backtest
from ....services.backtesting.strategies import STRATEGIES
from ....services.market.registry import MarketDataService
from ...deps import current_user

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestOut, status_code=status.HTTP_201_CREATED)
async def run(
    payload: BacktestIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> BacktestOut:
    if payload.strategy_name not in STRATEGIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown strategy '{payload.strategy_name}'")
    record = Backtest(
        user_id=user.id,
        strategy_name=payload.strategy_name,
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe,
        start=payload.start,
        end=payload.end,
        parameters=payload.parameters,
        status=BacktestStatus.running,
    )
    db.add(record)
    await db.flush()

    try:
        df = await MarketDataService.bars(
            payload.symbol, timeframe=payload.timeframe, start=payload.start, end=payload.end  # type: ignore[arg-type]
        )
        result = run_backtest(df, STRATEGIES[payload.strategy_name])
        record.status = BacktestStatus.succeeded
        record.n_trades = result.n_trades
        record.win_rate = result.win_rate
        record.sharpe = result.sharpe
        record.max_drawdown = result.max_drawdown
        record.total_return = result.total_return
        record.equity_curve = [
            {"t": idx.isoformat(), "equity": float(v)} for idx, v in result.equity_curve.items()
        ]
        record.trade_log = result.trades
    except Exception as exc:
        record.status = BacktestStatus.failed
        record.error = str(exc)[:2000]

    return BacktestOut.model_validate(record)


@router.get("", response_model=List[BacktestOut])
async def list_backtests(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> List[BacktestOut]:
    res = await db.execute(
        select(Backtest).where(Backtest.user_id == user.id).order_by(Backtest.created_at.desc())
    )
    return [BacktestOut.model_validate(b) for b in res.scalars()]


@router.get("/{backtest_id}", response_model=BacktestOut)
async def get_backtest(
    backtest_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> BacktestOut:
    res = await db.execute(
        select(Backtest).where(Backtest.id == backtest_id, Backtest.user_id == user.id)
    )
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backtest not found")
    return BacktestOut.model_validate(b)
