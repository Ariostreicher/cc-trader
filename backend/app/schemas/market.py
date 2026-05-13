"""Schemas for market data + indicator endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class QuoteOut(BaseModel):
    symbol: str
    price: float
    timestamp: datetime
    change: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[float] = None


class Bar(BaseModel):
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


class BarsOut(BaseModel):
    symbol: str
    timeframe: str
    bars: List[Bar]


class IndicatorsOut(BaseModel):
    symbol: str
    timeframe: str
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    ema_55: Optional[float] = None
    ema_100: Optional[float] = None
    ema_200: Optional[float] = None
    vwap: Optional[float] = None
    atr_14: Optional[float] = None
    bollinger_mid: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_lower: Optional[float] = None
    support: List[float] = []
    resistance: List[float] = []


class SearchResult(BaseModel):
    symbol: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    asset_class: Optional[str] = None
