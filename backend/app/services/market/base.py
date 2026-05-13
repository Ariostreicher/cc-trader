"""Provider-agnostic market-data interface.

A *Provider* fetches OHLCV bars and quotes for either stocks or crypto. The
public service layer picks a provider per asset class so the rest of the
codebase doesn't know whether a symbol came from Polygon, Alpaca, Binance,
Coinbase, or yfinance.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd


Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float
    timestamp: datetime
    change: float | None = None
    change_pct: float | None = None
    volume: float | None = None


class MarketDataProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Returns a DataFrame indexed by tz-aware UTC timestamps with columns
        open, high, low, close, volume."""

    @abc.abstractmethod
    async def quote(self, symbol: str) -> Quote: ...

    @abc.abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Symbol search — returns [{symbol, name, exchange, asset_class}]."""


def empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
