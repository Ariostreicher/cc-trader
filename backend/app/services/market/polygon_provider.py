"""Polygon.io adapter for stocks. Activated when POLYGON_API_KEY is set."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import httpx
import pandas as pd

from ...core.config import settings
from .base import MarketDataProvider, Quote, Timeframe, empty_ohlcv

_AGG_MAP: dict[Timeframe, tuple[int, str]] = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "1h": (1, "hour"),
    "4h": (4, "hour"),
    "1d": (1, "day"),
    "1w": (1, "week"),
}


class PolygonProvider(MarketDataProvider):
    name = "polygon"

    def __init__(self) -> None:
        self._key = settings.POLYGON_API_KEY
        self._client = httpx.AsyncClient(timeout=20)

    async def bars(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime):
        if not self._key:
            return empty_ohlcv()
        mult, span = _AGG_MAP[timeframe]
        url = (
            f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/{mult}/{span}"
            f"/{start.date().isoformat()}/{end.date().isoformat()}"
        )
        r = await self._client.get(url, params={"adjusted": "true", "limit": 50000, "apiKey": self._key})
        if r.status_code != 200:
            return empty_ohlcv()
        rows = r.json().get("results") or []
        if not rows:
            return empty_ohlcv()
        df = pd.DataFrame(rows).rename(
            columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "t": "ts"}
        )
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
        return df

    async def quote(self, symbol: str) -> Quote:
        if not self._key:
            return Quote(symbol=symbol, price=0.0, timestamp=datetime.utcnow())
        url = f"https://api.polygon.io/v2/last/trade/{symbol}"
        r = await self._client.get(url, params={"apiKey": self._key})
        data = r.json().get("results") or {}
        price = float(data.get("p") or 0.0)
        return Quote(symbol=symbol, price=price, timestamp=datetime.utcnow())

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        if not self._key:
            return []
        url = "https://api.polygon.io/v3/reference/tickers"
        r = await self._client.get(
            url, params={"search": query, "active": "true", "limit": limit, "apiKey": self._key}
        )
        results = r.json().get("results") or []
        out = []
        for row in results:
            out.append(
                {
                    "symbol": row.get("ticker"),
                    "name": row.get("name"),
                    "exchange": row.get("primary_exchange"),
                    "asset_class": _polygon_class(row.get("market"), row.get("type")),
                }
            )
        return out


def _polygon_class(market: str | None, t: str | None) -> Literal["stock", "etf", "crypto", "index"]:
    if market == "crypto":
        return "crypto"
    if market == "indices":
        return "index"
    if (t or "").lower() == "etf":
        return "etf"
    return "stock"
