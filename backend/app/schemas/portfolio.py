"""Paper-trading schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.portfolio import TradeSide, TradeStatus


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    starting_cash: float
    cash: float
    realized_pnl: float
    created_at: datetime


class PaperOrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: TradeSide
    quantity: float = Field(gt=0)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    journal: Optional[str] = None


class PaperTradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    side: TradeSide
    quantity: float
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: TradeStatus
    opened_at: datetime
    closed_at: Optional[datetime] = None
    pnl: Optional[float] = None
    journal: Optional[str] = None


class PortfolioFull(PortfolioOut):
    trades: List[PaperTradeOut] = []
