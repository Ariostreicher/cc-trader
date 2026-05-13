"""Live Setups endpoints — the heart of Phase 2."""

from __future__ import annotations

import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ....db.session import get_db
from ....models.user import User
from ....models.watchlist import Watchlist
from ....schemas.setup import ChartLevel, ChartPayload, ScanOut, SetupOut
from ....services.indicators.ta import (
    cc_region_levels,
    ema,
    fibonacci_levels,
    support_resistance,
    swing_pivots,
)
from ....services.market.registry import MarketDataService
from ....services.setups.scanner import scan_symbol, scan_symbols
from ...deps import current_user

router = APIRouter(prefix="/setups", tags=["setups"])


@router.get("/symbol/{symbol}", response_model=List[SetupOut])
async def scan_one(
    symbol: str,
    timeframe: str = Query(default="1d"),
    days: int = Query(default=365, ge=30, le=3650),
    _: User = Depends(current_user),
) -> List[SetupOut]:
    """Run all detectors against a single symbol."""
    setups = await scan_symbol(symbol.upper(), timeframe=timeframe, days=days)
    return [SetupOut.model_validate(s.to_dict()) for s in setups]


@router.get("/scan", response_model=ScanOut)
async def scan_many(
    symbols: Optional[str] = Query(
        default=None, description="Comma-separated symbols; if omitted, uses your watchlists."
    ),
    timeframe: str = Query(default="1d"),
    days: int = Query(default=365, ge=30, le=3650),
    min_conviction: float = Query(default=0.0, ge=0.0, le=1.0),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ScanOut:
    """Scan all tickers in the user's watchlists (or an explicit list).

    Returns every setup detected, sorted by conviction descending.
    """
    if symbols:
        tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        res = await db.execute(
            select(Watchlist)
            .where((Watchlist.user_id == user.id) | (Watchlist.is_public.is_(True)))
            .options(selectinload(Watchlist.assets))
        )
        seen: set[str] = set()
        tickers = []
        for wl in res.scalars():
            for a in wl.assets:
                if a.symbol not in seen:
                    seen.add(a.symbol)
                    tickers.append(a.symbol)

    if not tickers:
        return ScanOut(timeframe=timeframe, setups=[], scanned=0, skipped=0, duration_ms=0)

    started = time.perf_counter()
    results = await scan_symbols(tickers, timeframe=timeframe, days=days)
    duration_ms = int((time.perf_counter() - started) * 1000)

    flat: list[SetupOut] = []
    skipped = 0
    for sym, setups in results.items():
        if not setups:
            skipped += 1
            continue
        for s in setups:
            if s.conviction < min_conviction:
                continue
            flat.append(SetupOut.model_validate(s.to_dict()))

    flat.sort(key=lambda s: (s.conviction, s.risk_reward), reverse=True)

    return ScanOut(
        timeframe=timeframe,
        setups=flat,
        scanned=len(tickers),
        skipped=skipped,
        duration_ms=duration_ms,
    )


@router.get("/chart/{symbol}", response_model=ChartPayload)
async def chart(
    symbol: str,
    timeframe: str = Query(default="1d"),
    days: int = Query(default=180, ge=30, le=3650),
    _: User = Depends(current_user),
) -> ChartPayload:
    """OHLCV bars + EMAs + S/R + fib levels + any active setups, ready to draw."""
    sym = symbol.upper()
    df = await MarketDataService.bars(sym, timeframe=timeframe, days=days)  # type: ignore[arg-type]
    if df.empty:
        raise HTTPException(404, "no data")

    bars = [
        {
            "t": idx.isoformat(),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": float(row["volume"]),
        }
        for idx, row in df.iterrows()
    ]

    # EMAs (current value)
    e55 = float(ema(df["close"], 55).iloc[-1]) if len(df) > 55 else None
    e100 = float(ema(df["close"], 100).iloc[-1]) if len(df) > 100 else None
    e200 = float(ema(df["close"], 200).iloc[-1]) if len(df) > 200 else None

    # S/R levels
    sr = support_resistance(df.tail(200))

    # Latest swing-based fib levels
    pivots = swing_pivots(df.tail(150), n=5)
    fib_levels: list[ChartLevel] = []
    if len(pivots) >= 2:
        a, b = pivots[-2], pivots[-1]
        if a.kind == "low" and b.kind == "high":
            fib = fibonacci_levels(b.price, a.price)
            for k, v in fib.items():
                if k in {"0.382", "0.5", "0.618", "0.66", "0.786", "1.272", "1.618"}:
                    fib_levels.append(ChartLevel(label=f"Fib {k}", value=v, kind="fib"))
        elif a.kind == "high" and b.kind == "low":
            fib = fibonacci_levels(a.price, b.price)
            for k, v in fib.items():
                if k in {"0.382", "0.5", "0.618", "0.66", "0.786", "1.272", "1.618"}:
                    fib_levels.append(ChartLevel(label=f"Fib {k}", value=v, kind="fib"))

    levels: list[ChartLevel] = []
    if e55:
        levels.append(ChartLevel(label="EMA 55", value=e55, kind="ema"))
    if e100:
        levels.append(ChartLevel(label="EMA 100", value=e100, kind="ema"))
    if e200:
        levels.append(ChartLevel(label="EMA 200", value=e200, kind="ema"))
    for s in sr["support"][-5:]:
        levels.append(ChartLevel(label="Support", value=s, kind="support"))
    for r in sr["resistance"][-5:]:
        levels.append(ChartLevel(label="Resistance", value=r, kind="resistance"))
    levels.extend(fib_levels)

    setups = await scan_symbol(sym, timeframe=timeframe, days=days)
    return ChartPayload(
        symbol=sym,
        timeframe=timeframe,
        bars=bars,
        levels=levels,
        setups=[SetupOut.model_validate(s.to_dict()) for s in setups],
    )
