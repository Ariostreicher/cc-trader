"""yfinance fallback provider — keyless, works for both stocks and crypto."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from .base import MarketDataProvider, Quote, Timeframe, empty_ohlcv

_TF_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "1h",   # yfinance has no 4h; downstream resampling handles it
    "1d": "1d",
    "1w": "1wk",
}


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(how="any")


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    async def bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> pd.DataFrame:
        return await asyncio.to_thread(self._bars_sync, symbol, timeframe, start, end)

    def _bars_sync(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime):
        try:
            import yfinance as yf
        except ImportError:
            return empty_ohlcv()
        interval = _TF_MAP.get(timeframe, "1d")
        df = yf.download(
            symbol,
            start=start,
            end=end,
            interval=interval,
            progress=False,
            auto_adjust=False,
        )
        if df.empty:
            return empty_ohlcv()
        df = df.rename(columns=str.lower)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[["open", "high", "low", "close", "volume"]]
        if timeframe == "4h":
            df = _resample_to_4h(df)
        return df

    async def quote(self, symbol: str) -> Quote:
        return await asyncio.to_thread(self._quote_sync, symbol)

    def _quote_sync(self, symbol: str) -> Quote:
        try:
            import yfinance as yf
        except ImportError:
            return Quote(symbol=symbol, price=0.0, timestamp=datetime.now(timezone.utc))
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:
            info = {}
        price = info.get("regularMarketPrice") or info.get("currentPrice") or 0.0
        prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
        change = (price - prev) if (price and prev) else None
        change_pct = (change / prev * 100) if (change is not None and prev) else None
        return Quote(
            symbol=symbol,
            price=float(price or 0.0),
            timestamp=datetime.now(timezone.utc),
            change=change,
            change_pct=change_pct,
            volume=info.get("regularMarketVolume"),
        )

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        # yfinance has no dedicated search; we return a single candidate via
        # the Ticker lookup. Real search lives in PolygonProvider.
        try:
            import yfinance as yf
            info = yf.Ticker(query).info or {}
        except Exception:
            return []
        if not info.get("symbol"):
            return []
        return [
            {
                "symbol": info["symbol"],
                "name": info.get("longName") or info.get("shortName"),
                "exchange": info.get("exchange"),
                "asset_class": _infer_class(info),
            }
        ]


def _infer_class(info: dict) -> Literal["stock", "etf", "crypto", "index"]:
    qt = (info.get("quoteType") or "").lower()
    if qt == "etf":
        return "etf"
    if qt == "cryptocurrency":
        return "crypto"
    if qt == "index":
        return "index"
    return "stock"
