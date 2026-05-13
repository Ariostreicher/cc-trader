"""Scanner — runs all detectors across a list of tickers."""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from ..market.registry import MarketDataService
from .detectors import detect_all
from .types import Setup

logger = logging.getLogger(__name__)


async def scan_symbol(symbol: str, timeframe: str = "1d", days: int = 365) -> list[Setup]:
    df = await MarketDataService.bars(symbol, timeframe=timeframe, days=days)  # type: ignore[arg-type]
    if df.empty:
        return []
    return detect_all(symbol, df, timeframe=timeframe)


async def scan_symbols(
    symbols: Iterable[str],
    timeframe: str = "1d",
    days: int = 365,
    concurrency: int = 8,
) -> dict[str, list[Setup]]:
    """Concurrent scan across many symbols. Returns {symbol: [setups]}."""
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, list[Setup]] = {}

    async def _one(sym: str) -> None:
        async with sem:
            try:
                setups = await scan_symbol(sym, timeframe=timeframe, days=days)
                out[sym] = setups
            except Exception as exc:
                logger.warning("scan failed for %s: %s", sym, exc)
                out[sym] = []

    await asyncio.gather(*[_one(s) for s in symbols])
    return out
