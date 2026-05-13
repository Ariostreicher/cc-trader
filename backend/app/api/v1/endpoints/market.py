"""Market data + indicator endpoints."""

from __future__ import annotations

import math
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from ....models.user import User
from ....schemas.market import Bar, BarsOut, IndicatorsOut, QuoteOut
from ....services.indicators import (
    atr,
    bollinger,
    ema,
    macd,
    rsi,
    support_resistance,
    vwap,
)
from ....services.market.registry import MarketDataService
from ...deps import current_user

router = APIRouter(prefix="/market", tags=["market"])


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


@router.get("/quote/{symbol}", response_model=QuoteOut)
async def get_quote(symbol: str, _: User = Depends(current_user)) -> QuoteOut:
    q = await MarketDataService.quote(symbol)
    return QuoteOut(
        symbol=q.symbol,
        price=q.price,
        timestamp=q.timestamp,
        change=q.change,
        change_pct=q.change_pct,
        volume=q.volume,
    )


@router.get("/bars/{symbol}", response_model=BarsOut)
async def get_bars(
    symbol: str,
    timeframe: str = Query(default="1d"),
    days: int = Query(default=365, ge=1, le=3650),
    _: User = Depends(current_user),
) -> BarsOut:
    if timeframe not in {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}:
        raise HTTPException(400, "invalid timeframe")
    df = await MarketDataService.bars(symbol, timeframe=timeframe, days=days)  # type: ignore[arg-type]
    bars = [
        Bar(t=idx, o=row["open"], h=row["high"], l=row["low"], c=row["close"], v=row["volume"])
        for idx, row in df.iterrows()
    ]
    return BarsOut(symbol=symbol, timeframe=timeframe, bars=bars)


@router.get("/indicators/{symbol}", response_model=IndicatorsOut)
async def get_indicators(
    symbol: str,
    timeframe: str = Query(default="1d"),
    days: int = Query(default=365, ge=30, le=3650),
    _: User = Depends(current_user),
) -> IndicatorsOut:
    if timeframe not in {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}:
        raise HTTPException(400, "invalid timeframe")
    df = await MarketDataService.bars(symbol, timeframe=timeframe, days=days)  # type: ignore[arg-type]
    if df.empty:
        raise HTTPException(404, "no data")

    close = df["close"]
    rsi_v = rsi(close)
    macd_df = macd(close)
    bb = bollinger(close)
    sr = support_resistance(df)

    out = IndicatorsOut(
        symbol=symbol,
        timeframe=timeframe,
        rsi=_f(rsi_v.iloc[-1]),
        macd=_f(macd_df["macd"].iloc[-1]),
        macd_signal=_f(macd_df["signal"].iloc[-1]),
        macd_hist=_f(macd_df["hist"].iloc[-1]),
        ema_55=_f(ema(close, 55).iloc[-1]),
        ema_100=_f(ema(close, 100).iloc[-1]),
        ema_200=_f(ema(close, 200).iloc[-1]),
        vwap=_f(vwap(df).iloc[-1]),
        atr_14=_f(atr(df).iloc[-1]),
        bollinger_mid=_f(bb["mid"].iloc[-1]),
        bollinger_upper=_f(bb["upper"].iloc[-1]),
        bollinger_lower=_f(bb["lower"].iloc[-1]),
        support=sr["support"][-5:],
        resistance=sr["resistance"][-5:],
    )
    return out


@router.get("/quotes", response_model=List[QuoteOut])
async def batch_quotes(
    symbols: str = Query(..., description="Comma-separated"),
    _: User = Depends(current_user),
) -> List[QuoteOut]:
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if len(syms) > 100:
        raise HTTPException(400, "too many symbols")
    quotes = await MarketDataService.quotes(syms)
    return [
        QuoteOut(
            symbol=s,
            price=q.price,
            timestamp=q.timestamp,
            change=q.change,
            change_pct=q.change_pct,
            volume=q.volume,
        )
        for s, q in quotes.items()
    ]
