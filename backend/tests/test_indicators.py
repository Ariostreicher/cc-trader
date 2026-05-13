"""Sanity tests for indicator math (not full coverage — just shape + invariants)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.indicators import (
    bollinger,
    cc_region_levels,
    ema,
    fibonacci_levels,
    macd,
    rsi,
    sma,
    support_resistance,
    swing_pivots,
)


def _df(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + rng.standard_normal(n).cumsum()
    high = close + np.abs(rng.standard_normal(n))
    low = close - np.abs(rng.standard_normal(n))
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": rng.uniform(1e6, 5e6, n)},
        index=pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC"),
    )


def test_sma_ema_lengths_align():
    df = _df()
    assert len(sma(df["close"], 20)) == len(df)
    assert len(ema(df["close"], 20)) == len(df)


def test_rsi_bounded_0_100():
    df = _df()
    r = rsi(df["close"]).dropna()
    assert (r.between(0, 100)).all()


def test_macd_components():
    df = _df()
    m = macd(df["close"])
    assert {"macd", "signal", "hist"}.issubset(m.columns)


def test_bollinger_upper_above_mid():
    df = _df()
    b = bollinger(df["close"]).dropna()
    assert (b["upper"] >= b["mid"]).all()


def test_fibonacci_and_cc_region():
    fib = fibonacci_levels(110, 100)
    assert fib["0.618"] == 106.18
    lo, hi = cc_region_levels(110, 100)
    assert lo == 106.18
    assert hi == 106.6


def test_swing_pivots_and_sr_cluster():
    df = _df()
    pivots = swing_pivots(df, n=5)
    assert len(pivots) > 0
    sr = support_resistance(df)
    assert "support" in sr and "resistance" in sr
