"""Fundamentals snapshot, used as a *factual input* for the equity model.

Source priority:
1. yfinance (free, no key needed) — primary
2. Future: Polygon, Alpha Vantage, Tiingo — when keys present
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class FundamentalsService:
    @staticmethod
    async def snapshot(ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(_yfinance_snapshot, ticker)


def _yfinance_snapshot(ticker: str) -> dict[str, Any]:
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed; returning empty snapshot")
        return {}

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as exc:
        logger.warning("yfinance failed for %s: %s", ticker, exc)
        return {}

    def _pick(*keys: str) -> Any:
        for k in keys:
            v = info.get(k)
            if v not in (None, "", float("nan")):
                return v
        return None

    return {
        "name": _pick("longName", "shortName"),
        "sector": _pick("sector"),
        "industry": _pick("industry"),
        "country": _pick("country"),
        "price": _pick("currentPrice", "regularMarketPrice"),
        "market_cap": _pick("marketCap"),
        "pe_ratio_ttm": _pick("trailingPE"),
        "forward_pe": _pick("forwardPE"),
        "eps_ttm": _pick("trailingEps"),
        "revenue_ttm": _pick("totalRevenue"),
        "gross_margin": _pick("grossMargins"),
        "operating_margin": _pick("operatingMargins"),
        "profit_margin": _pick("profitMargins"),
        "free_cash_flow": _pick("freeCashflow"),
        "operating_cash_flow": _pick("operatingCashflow"),
        "total_cash": _pick("totalCash"),
        "total_debt": _pick("totalDebt"),
        "debt_to_equity": _pick("debtToEquity"),
        "return_on_equity": _pick("returnOnEquity"),
        "return_on_assets": _pick("returnOnAssets"),
        "beta": _pick("beta"),
        "dividend_yield": _pick("dividendYield"),
        "52w_high": _pick("fiftyTwoWeekHigh"),
        "52w_low": _pick("fiftyTwoWeekLow"),
        "shares_outstanding": _pick("sharesOutstanding"),
        "analyst_target_mean": _pick("targetMeanPrice"),
        "analyst_target_median": _pick("targetMedianPrice"),
        "recommendation": _pick("recommendationKey"),
    }
