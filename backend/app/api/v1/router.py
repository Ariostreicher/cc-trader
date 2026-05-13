"""Top-level v1 router — assembles all endpoint modules."""

from __future__ import annotations

from fastapi import APIRouter

from .endpoints import (
    admin,
    alerts,
    auth,
    backtest,
    billing,
    documents,
    equity,
    market,
    paper,
    search,
    setups,
    strategies,
    watchlists,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(strategies.router)
api_router.include_router(equity.router)
api_router.include_router(market.router)
api_router.include_router(search.router)
api_router.include_router(watchlists.router)
api_router.include_router(alerts.router)
api_router.include_router(setups.router)
api_router.include_router(paper.router)
api_router.include_router(backtest.router)
api_router.include_router(billing.router)
api_router.include_router(admin.router)
