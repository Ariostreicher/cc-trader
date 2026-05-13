"""Binance public-data adapter for crypto. Read-only — no auth required."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pandas as pd

from .base import MarketDataProvider, Quote, Timeframe, empty_ohlcv

_TF_MAP: dict[Timeframe, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}


class BinanceProvider(MarketDataProvider):
    name = "binance"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=20, base_url="https://api.binance.com")

    async def bars(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime):
        interval = _TF_MAP[timeframe]
        params = {
            "symbol": symbol.replace("/", "").upper(),
            "interval": interval,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": 1000,
        }
        r = await self._client.get("/api/v3/klines", params=params)
        if r.status_code != 200:
            return empty_ohlcv()
        rows = r.json() or []
        if not rows:
            return empty_ohlcv()
        df = pd.DataFrame(rows, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c])
        return df[["open", "high", "low", "close", "volume"]]

    async def quote(self, symbol: str) -> Quote:
        params = {"symbol": symbol.replace("/", "").upper()}
        r = await self._client.get("/api/v3/ticker/24hr", params=params)
        if r.status_code != 200:
            return Quote(symbol=symbol, price=0.0, timestamp=datetime.now(timezone.utc))
        data = r.json()
        return Quote(
            symbol=symbol,
            price=float(data.get("lastPrice", 0.0)),
            timestamp=datetime.now(timezone.utc),
            change=float(data.get("priceChange", 0.0)) or None,
            change_pct=float(data.get("priceChangePercent", 0.0)) or None,
            volume=float(data.get("volume", 0.0)) or None,
        )

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        # Binance has no fuzzy search; we substring-match exchangeInfo.
        r = await self._client.get("/api/v3/exchangeInfo")
        if r.status_code != 200:
            return []
        symbols = r.json().get("symbols") or []
        q = query.upper().replace("/", "")
        out: list[dict] = []
        for s in symbols:
            if s.get("status") != "TRADING":
                continue
            sym = s.get("symbol") or ""
            base = s.get("baseAsset") or ""
            quote = s.get("quoteAsset") or ""
            if q in sym or q in base or q in quote:
                out.append(
                    {
                        "symbol": sym,
                        "name": f"{base}/{quote}",
                        "exchange": "BINANCE",
                        "asset_class": "crypto",
                    }
                )
                if len(out) >= limit:
                    break
        return out
