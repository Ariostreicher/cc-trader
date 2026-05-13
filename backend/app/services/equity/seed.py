"""Seed the public Chart Champions 2026 Research watchlist.

Run via ``python -m app.services.equity.seed`` after ``alembic upgrade head``.
This watchlist mirrors the operator-supplied
``Equity Model 2026 Research TradingView Import List.txt``.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from ...db.session import AsyncSessionLocal
from ...models.watchlist import AssetClass, Watchlist, WatchlistAsset

# (sector, symbol_with_exchange)
SEED_2026 = [
    ("Mega Cap Tech",         "NASDAQ:GOOGL"),
    ("Mega Cap Tech",         "NASDAQ:AVGO"),
    ("Mega Cap Tech",         "NYSE:LLY"),
    ("Mega Cap Tech",         "NYSE:V"),
    ("Mega Cap Tech",         "NYSE:ANET"),
    ("Mega Cap Tech",         "NASDAQ:KLAC"),
    ("Mega Cap Tech",         "NASDAQ:PANW"),
    ("Mega Cap Tech",         "NASDAQ:SNPS"),
    ("Energy",                "NYSE:XOM"),
    ("Energy",                "NYSE:SLB"),
    ("Energy",                "NYSE:DVN"),
    ("Industrials",           "NYSE:CAT"),
    ("Industrials",           "NYSE:GEV"),
    ("Industrials",           "NYSE:BA"),
    ("Industrials",           "NYSE:CRH"),
    ("Industrials",           "NYSE:VRT"),
    ("Industrials",           "NYSE:DAN"),
    ("Industrials",           "NYSE:CMC"),
    ("Industrials",           "NYSE:MHK"),
    ("Industrials",           "NYSE:VMI"),
    ("Financials",            "NYSE:C"),
    ("Financials",            "NYSE:SCHW"),
    ("Financials",            "NYSE:ALL"),
    ("Financials",            "NASDAQ:VLY"),
    ("Financials",            "NYSE:TRU"),
    ("Financials",            "NYSE:TRTX"),
    ("Communication Services","NYSE:DIS"),
    ("Communication Services","NASDAQ:ROKU"),
    ("Consumer Discretionary","NYSE:CVNA"),
    ("Consumer Discretionary","NASDAQ:DKNG"),
    ("Consumer Discretionary","NYSE:WMB"),
    ("Consumer Discretionary","NYSE:RL"),
    ("Consumer Discretionary","NASDAQ:CELH"),
    ("Consumer Staples",      "NYSE:CVS"),
    ("Consumer Staples",      "NASDAQ:SBUX"),
    ("Consumer Staples",      "NYSE:MKC"),
    ("Healthcare",            "NYSE:TMO"),
    ("Healthcare",            "NASDAQ:RVMD"),
    ("Healthcare",            "NYSE:GL"),
    ("Healthcare",            "NASDAQ:FOLD"),
    ("Healthcare",            "NASDAQ:XENE"),
    ("Real Estate",           "NYSE:DLR"),
    ("Real Estate",           "NYSE:CBRE"),
    ("Materials",             "NYSE:ETR"),
    ("Transport",             "NYSE:CP"),
    ("Transport",             "NASDAQ:UAL"),
    ("Transport",             "NYSE:VIK"),
    ("Automotive",            "NYSE:AZO"),
    ("Tech Software",         "NYSE:CRM"),
    ("Tech Software",         "NYSE:GWRE"),
]

WATCHLIST_NAME = "Chart Champions 2026 Research"


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Watchlist).where(
                Watchlist.user_id.is_(None), Watchlist.name == WATCHLIST_NAME
            )
        )
        wl = res.scalar_one_or_none()
        if wl is None:
            wl = Watchlist(
                user_id=None,
                name=WATCHLIST_NAME,
                description=(
                    "2026 research deployment list provided by Chart Champions. "
                    "Not a recommendation list."
                ),
                is_public=True,
                pinned=True,
            )
            db.add(wl)
            await db.flush()

        existing = {a.symbol for a in (await db.execute(
            select(WatchlistAsset).where(WatchlistAsset.watchlist_id == wl.id)
        )).scalars()}

        position = len(existing)
        for sector, qualified in SEED_2026:
            exchange, symbol = qualified.split(":", 1)
            if symbol in existing:
                continue
            db.add(
                WatchlistAsset(
                    watchlist_id=wl.id,
                    symbol=symbol,
                    exchange=exchange,
                    sector=sector,
                    asset_class=AssetClass.stock,
                    position=position,
                )
            )
            position += 1

        await db.commit()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(seed())
