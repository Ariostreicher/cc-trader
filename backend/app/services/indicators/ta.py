"""Vectorized technical indicators.

Every function takes either a single Series ``close`` or the full OHLCV
DataFrame and returns a Series (or DataFrame) aligned on the same index.

References to the Chart Champions cheatsheets are cited inline where the
indicator implements a specific rule from the methodology.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def sma(close: pd.Series, length: int = 20) -> pd.Series:
    return close.rolling(window=length, min_periods=length).mean()


def ema(close: pd.Series, length: int = 20) -> pd.Series:
    return close.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.DataFrame:
    m = sma(close, length)
    s = close.rolling(window=length, min_periods=length).std(ddof=0)
    return pd.DataFrame({"mid": m, "upper": m + mult * s, "lower": m - mult * s})


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    return pv.cumsum() / df["volume"].cumsum().replace(0, np.nan)


# ----------------------------------------------------------------------
# Chart Champions specific rules
# ----------------------------------------------------------------------


def fibonacci_levels(swing_high: float, swing_low: float) -> dict[str, float]:
    """Standard Fibonacci retracement levels.
    The Chart Champions 'CC Region' is 0.618–0.66 retracement
    (see First 18.pdf p.1 and p.63).
    """
    rng = swing_high - swing_low
    return {
        "0.0": swing_low,
        "0.236": swing_low + 0.236 * rng,
        "0.382": swing_low + 0.382 * rng,
        "0.5": swing_low + 0.5 * rng,
        "0.618": swing_low + 0.618 * rng,
        "0.66": swing_low + 0.66 * rng,  # CC region upper
        "0.786": swing_low + 0.786 * rng,
        "1.0": swing_high,
        "1.272": swing_low + 1.272 * rng,
        "1.618": swing_low + 1.618 * rng,
    }


def cc_region_levels(swing_high: float, swing_low: float) -> tuple[float, float]:
    """The CC retracement zone: ``[0.618, 0.66]`` of the swing range."""
    fib = fibonacci_levels(swing_high, swing_low)
    return fib["0.618"], fib["0.66"]


@dataclass(slots=True)
class Pivot:
    index: pd.Timestamp
    price: float
    kind: str  # "high" | "low"


def swing_pivots(df: pd.DataFrame, n: int = 5) -> list[Pivot]:
    """Detect swing highs and lows with ``n`` bars on each side.

    Default ``n=5`` matches the visual convention used in the Chart
    Champions market-structure cheatsheets.
    """
    highs = df["high"].values
    lows = df["low"].values
    times = df.index
    pivots: list[Pivot] = []
    for i in range(n, len(df) - n):
        h_window = highs[i - n : i + n + 1]
        l_window = lows[i - n : i + n + 1]
        if highs[i] == h_window.max() and (h_window.argmax() == n):
            pivots.append(Pivot(index=times[i], price=float(highs[i]), kind="high"))
        if lows[i] == l_window.min() and (l_window.argmin() == n):
            pivots.append(Pivot(index=times[i], price=float(lows[i]), kind="low"))
    return pivots


def support_resistance(
    df: pd.DataFrame, *, n: int = 5, cluster_tolerance_pct: float = 0.5
) -> dict[str, list[float]]:
    """Cluster swing pivots into S/R levels.
    Two pivots are merged if they are within ``cluster_tolerance_pct``
    of each other. Returned values are mean prices per cluster.
    """
    pivots = swing_pivots(df, n=n)
    highs = sorted([p.price for p in pivots if p.kind == "high"])
    lows = sorted([p.price for p in pivots if p.kind == "low"])

    def _cluster(values: list[float]) -> list[float]:
        if not values:
            return []
        out: list[list[float]] = [[values[0]]]
        for v in values[1:]:
            ref = sum(out[-1]) / len(out[-1])
            if ref > 0 and abs(v - ref) / ref * 100 <= cluster_tolerance_pct:
                out[-1].append(v)
            else:
                out.append([v])
        return [round(sum(c) / len(c), 6) for c in out]

    return {"resistance": _cluster(highs), "support": _cluster(lows)}
