"""Backtest schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.backtest import BacktestStatus


class BacktestIn(BaseModel):
    strategy_name: str = Field(min_length=1, max_length=255)
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: str = Field(default="1d", pattern="^(1m|5m|15m|30m|1h|4h|1d|1w)$")
    start: datetime
    end: datetime
    parameters: dict = Field(default_factory=dict)


class BacktestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_name: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    parameters: dict
    status: BacktestStatus
    n_trades: Optional[int] = None
    win_rate: Optional[float] = None
    sharpe: Optional[float] = None
    max_drawdown: Optional[float] = None
    total_return: Optional[float] = None
    equity_curve: Optional[List[dict]] = None
    trade_log: Optional[List[dict]] = None
    error: Optional[str] = None
    created_at: datetime
