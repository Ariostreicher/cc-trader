"""Provider selection + a single MarketDataService facade."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterable

import pandas as pd

from ...core.config import settings
from .base import MarketDataProvider, Quote, Timeframe
from .binance_provider import BinanceProvider
from .polygon_provider import PolygonProvider
from .yfinance_provider import YFinanceProvider


@lru_cache(maxsize=1)
def _providers() -> dict[str, MarketDataProvider]:
    out: dict[str, MarketDataProvider] = {"yfinance": YFinanceProvider()}
    if settings.POLYGON_API_KEY:
        out["polygon"] = PolygonProvider()
    out["binance"] = BinanceProvider()
    return out


def _looks_like_crypto(symbol: str) -> bool:
    s = symbol.upper()
    return (
        s.endswith(("USDT", "USDC", "BUSD", "BTC", "ETH"))
        or "/" in s
        or s in {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "BNB"}
    )


def _provider_for(symbol: str) -> MarketDataProvider:
    if _looks_like_crypto(symbol):
        return _providers().get("binance") or _providers()["yfinance"]
    return _providers().get("polygon") or _providers()["yfinance"]


class MarketDataService:
    @staticmethod
    async def bars(
        symbol: str,
        timeframe: Timeframe = "1d",
        *,
        days: int = 365,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=days))
        return await _provider_for(symbol).bars(symbol, timeframe, start, end)

    @staticmethod
    async def quote(symbol: str) -> Quote:
        return await _provider_for(symbol).quote(symbol)

    @staticmethod
    async def quotes(symbols: Iterable[str]) -> dict[str, Quote]:
        # Naive fan-out; in production this should be batched per provider.
        import asyncio

        symbols = list(symbols)
        results = await asyncio.gather(*[MarketDataService.quote(s) for s in symbols])
        return dict(zip(symbols, results))

    @staticmethod
    async def search(query: str, limit: int = 10) -> list[dict]:
        # Use the strongest provider available for each asset class and merge.
        polygon = _providers().get("polygon")
        binance = _providers()["binance"]
        yfin = _providers()["yfinance"]

        seen: set[str] = set()
        out: list[dict] = []
        for provider in (polygon, binance, yfin):
            if provider is None:
                continue
            try:
                items = await provider.search(query, limit=limit)
            except Exception:
                items = []
            for it in items:
                sym = it.get("symbol")
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                out.append(it)
                if len(out) >= limit:
                    return out
        return out
