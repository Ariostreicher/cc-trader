#!/usr/bin/env python3
"""
CC Trader — standalone Chart Champions setup scanner.

Runs the same detectors as the full app but WITHOUT Docker, WITHOUT a database,
WITHOUT a frontend. Pulls live OHLCV via yfinance, runs the CC rules, and
opens an HTML report in your browser.

Usage:
    pip3 install yfinance pandas numpy
    python3 scan_setups.py                # scan default CC 2026 watchlist
    python3 scan_setups.py BTC-USD ETH-USD AAPL TSLA   # ad-hoc tickers
"""

from __future__ import annotations

import sys
import os
import json
import webbrowser
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Optional

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except ImportError:
    print("Missing dependencies. Run:  pip3 install yfinance pandas numpy")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Default tickers — Chart Champions 2026 Research Watchlist
# ---------------------------------------------------------------------------
CC_2026 = [
    # Mega Cap Tech
    "GOOGL", "AVGO", "LLY", "V", "ANET", "KLAC", "PANW", "SNPS",
    # Energy
    "XOM", "SLB", "DVN",
    # Industrials
    "CAT", "GEV", "BA", "CRH", "VRT", "DAN", "CMC", "MHK", "VMI",
    # Financials
    "C", "SCHW", "ALL", "VLY", "TRU", "TRTX",
    # Communication Services
    "DIS", "ROKU",
    # Consumer Discretionary
    "CVNA", "DKNG", "WMB", "RL", "CELH",
    # Consumer Staples
    "CVS", "SBUX", "MKC",
    # Healthcare
    "TMO", "RVMD", "GL", "FOLD", "XENE",
    # Real Estate / Materials / Transport / Auto / Software
    "DLR", "CBRE", "ETR", "CP", "UAL", "VIK", "AZO", "CRM", "GWRE",
    # Crypto majors (yfinance format)
    "BTC-USD", "ETH-USD", "SOL-USD",
]


# ---------------------------------------------------------------------------
# Indicators — verbatim copies from app/services/indicators/ta.py
# ---------------------------------------------------------------------------
def ema(close: pd.Series, length: int) -> pd.Series:
    return close.ewm(span=length, adjust=False, min_periods=length).mean()


def sma(close: pd.Series, length: int) -> pd.Series:
    return close.rolling(window=length, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0):
    m = sma(close, length)
    s = close.rolling(window=length, min_periods=length).std(ddof=0)
    return pd.DataFrame({"mid": m, "upper": m + mult * s, "lower": m - mult * s})


def keltner_channel(df: pd.DataFrame, length: int = 20, mult: float = 1.5) -> pd.DataFrame:
    """Keltner Channel — EMA of typical price ± mult × ATR. Used by BB Squeeze
    detector (BB inside KC = volatility compression about to expand).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    mid = ema(typical, length)
    a = atr(df, length)
    return pd.DataFrame({"mid": mid, "upper": mid + mult * a, "lower": mid - mult * a})


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume — cumulative volume signed by price direction. Rising
    OBV confirms uptrend; flat/declining OBV during a price rally = divergence."""
    if df is None or df.empty or "volume" not in df.columns:
        return pd.Series([], dtype=float)
    delta = df["close"].diff()
    direction = np.where(delta > 0, 1, np.where(delta < 0, -1, 0))
    signed = direction * df["volume"].fillna(0)
    return pd.Series(signed, index=df.index).cumsum()


def fibonacci_levels(swing_high: float, swing_low: float) -> dict[str, float]:
    rng = swing_high - swing_low
    return {
        "0.382": swing_low + 0.382 * rng,
        "0.5": swing_low + 0.5 * rng,
        "0.618": swing_low + 0.618 * rng,
        "0.66": swing_low + 0.66 * rng,
        "1.272": swing_low + 1.272 * rng,
        "1.618": swing_low + 1.618 * rng,
    }


def cc_region(swing_high: float, swing_low: float) -> tuple[float, float]:
    """Chart Champions retracement zone — First 18.pdf p.1, p.63."""
    rng = swing_high - swing_low
    return (swing_low + 0.618 * rng, swing_low + 0.66 * rng)


@dataclass
class Pivot:
    idx: pd.Timestamp
    price: float
    kind: str  # "high" or "low"


def swing_pivots(df: pd.DataFrame, n: int = 5) -> List[Pivot]:
    highs = df["high"].values
    lows = df["low"].values
    times = df.index
    out: list[Pivot] = []
    for i in range(n, len(df) - n):
        h_w = highs[i - n : i + n + 1]
        l_w = lows[i - n : i + n + 1]
        if highs[i] == h_w.max() and h_w.argmax() == n:
            out.append(Pivot(times[i], float(highs[i]), "high"))
        if lows[i] == l_w.min() and l_w.argmin() == n:
            out.append(Pivot(times[i], float(lows[i]), "low"))
    return out


def support_resistance(df: pd.DataFrame, n: int = 5, tol_pct: float = 0.5):
    """Classify swing levels as support/resistance RELATIVE TO CURRENT PRICE.

    In a downtrend, what was a 'swing low' months ago is now ABOVE current
    price — that's resistance on the way back up, not support. The correct
    classification is positional, not directional:
        level < current_price  →  support
        level > current_price  →  resistance
    """
    if df is None or df.empty:
        return {"resistance": [], "support": []}
    current_price = float(df["close"].iloc[-1])
    pivots = swing_pivots(df, n)
    # ALL swing levels are potential S/R — let position vs current price decide.
    all_levels = sorted({p.price for p in pivots})

    def cluster(vs):
        if not vs:
            return []
        out = [[vs[0]]]
        for v in vs[1:]:
            ref = sum(out[-1]) / len(out[-1])
            if ref > 0 and abs(v - ref) / ref * 100 <= tol_pct:
                out[-1].append(v)
            else:
                out.append([v])
        return [round(sum(c) / len(c), 4) for c in out]

    supports = cluster([v for v in all_levels if v < current_price])
    resistances = cluster([v for v in all_levels if v > current_price])
    return {"support": supports, "resistance": resistances}


# ---------------------------------------------------------------------------
# Setup output type
# ---------------------------------------------------------------------------
@dataclass
class ContextFlag:
    label: str          # "HTF", "Volume", "Market", "Sector", "Catalyst"
    status: str         # "ok" | "warn" | "bad"
    detail: str         # human-readable explanation


@dataclass
class Snapshot:
    """Chart-only view for a ticker that didn't fire a setup.
    Used so ad-hoc searches always return SOMETHING useful — the chart, current
    price, and CC indicator values for the operator to read manually."""
    symbol: str
    current_price: float
    ema_55: Optional[float] = None
    ema_100: Optional[float] = None
    ema_200: Optional[float] = None
    rsi_14: Optional[float] = None
    support_levels: List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    context_flags: List[ContextFlag] = field(default_factory=list)
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_pct: Optional[float] = None    # (ask - bid) / mid × 100
    avg_volume: Optional[float] = None    # 20-day avg vol for liquidity gauge
    # Wave 1: comprehensive level overlays
    fib: Optional[dict] = None            # output of compute_fib_levels
    pivots: Optional[dict] = None         # daily pivot points
    vwap_anchored: Optional[float] = None # anchored VWAP from the active swing
    round_numbers: List[float] = field(default_factory=list)
    # Wave 5: multi-timeframe levels
    pivots_weekly: Optional[dict] = None
    pivots_monthly: Optional[dict] = None
    recent_weekly: List[dict] = field(default_factory=list)   # last N weekly H/L
    recent_monthly: List[dict] = field(default_factory=list)  # last N monthly H/L
    vp_weekly: Optional[dict] = None
    vp_monthly: Optional[dict] = None
    vp_quarterly: Optional[dict] = None
    naked_pocs: List[dict] = field(default_factory=list)
    # Wave 7: Structured Equity Analysis Model (fundamental backdrop)
    equity_analysis: Optional[dict] = None
    # Wave 12: Camarilla pivots (alternative intraday reference)
    camarilla: Optional[dict] = None


@dataclass
class Setup:
    symbol: str
    name: str
    direction: str  # "long" / "short"
    entry: float
    stop_loss: float
    targets: List[float]
    current_price: float
    conviction: float
    reasoning: str
    citation: str  # e.g. "First 18.pdf p.67"
    ai_analysis: str = ""  # senior-trader voice via Groq
    context_flags: List[ContextFlag] = field(default_factory=list)

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        if risk == 0 or not self.targets:
            return 0.0
        return abs(self.targets[0] - self.entry) / risk

    @property
    def move_pct(self) -> float:
        if not self.targets:
            return 0.0
        if self.direction == "long":
            return (self.targets[0] - self.entry) / self.entry * 100
        return (self.entry - self.targets[0]) / self.entry * 100


# ---------------------------------------------------------------------------
# Wave 1 level helpers — every chart should show the full Fibonacci ladder,
# anchored VWAP, classic Pivot Points, and round numbers, not just EMAs and
# swing pivots. CC methodology references all of these as primary levels.
# ---------------------------------------------------------------------------
def compute_fib_levels(df: pd.DataFrame, lookback_bars: int = 250) -> dict:
    """Return Fibonacci retracements + extensions anchored to the highest high
    and lowest low in the most recent `lookback_bars` (default ~12 months daily).
    Direction is auto-detected:
      • If the high came AFTER the low → uptrend swing, retracements sit
        BELOW the high (support on pullback from $H to $L).
      • If the low came AFTER the high → downtrend swing, retracements sit
        ABOVE the low (resistance on bounce from $L back toward $H).

    Returns:
      {
        'high': float, 'low': float, 'direction': 'up'|'down',
        'retracements': {'0.236': px, '0.382': px, ... '0.786': px, '1.0': px},
        'extensions':   {'1.272': px, '1.414': px, '1.618': px},
      }
    """
    if df is None or df.empty or len(df) < 10:
        return {}
    window = df.tail(lookback_bars)
    hi_idx = window["high"].idxmax()
    lo_idx = window["low"].idxmin()
    hi = float(window.loc[hi_idx, "high"])
    lo = float(window.loc[lo_idx, "low"])
    if hi <= lo:
        return {}
    direction = "up" if hi_idx > lo_idx else "down"
    rng = hi - lo
    levels = {}
    for pct in (0.236, 0.382, 0.500, 0.618, 0.660, 0.786):
        if direction == "up":
            levels[f"{pct:.3f}"] = hi - pct * rng
        else:
            levels[f"{pct:.3f}"] = lo + pct * rng
    levels["1.000"] = lo if direction == "up" else hi
    extensions = {}
    for ext in (1.272, 1.414, 1.618):
        if direction == "up":
            extensions[f"{ext:.3f}"] = hi + (ext - 1.0) * rng
        else:
            extensions[f"{ext:.3f}"] = lo - (ext - 1.0) * rng
    return {
        "high": hi, "low": lo,
        "direction": direction,
        "retracements": levels,
        "extensions": extensions,
    }


def compute_pivot_points(df: pd.DataFrame) -> dict:
    """Classic floor-trader Pivot Points from the previous completed bar.
    Used by intraday + daily traders alike — they act as magnets that the
    price respects more often than chance.

    Returns: {pp, r1, r2, r3, s1, s2, s3, prev_high, prev_low, prev_close}
    """
    if df is None or df.empty or len(df) < 2:
        return {}
    prev = df.iloc[-2]
    ph, pl, pc = float(prev["high"]), float(prev["low"]), float(prev["close"])
    pp = (ph + pl + pc) / 3.0
    rng = ph - pl
    return {
        "pp": pp,
        "r1": 2 * pp - pl,        "r2": pp + rng,           "r3": ph + 2 * (pp - pl),
        "s1": 2 * pp - ph,        "s2": pp - rng,           "s3": pl - 2 * (ph - pp),
        "prev_high": ph, "prev_low": pl, "prev_close": pc,
    }


def compute_anchored_vwap(df: pd.DataFrame, anchor_idx: int = None,
                          lookback_bars: int = 250) -> Optional[float]:
    """Anchored VWAP from an anchor bar to the latest bar.

    Default anchor: the most recent extreme (high or low) in the last
    `lookback_bars`. That gives a "VWAP from the major pivot" which is a
    classic CC reference for measuring whether the move from the swing has
    been on conviction or thin liquidity.

    Returns: float VWAP, or None if not computable.
    """
    if df is None or df.empty or len(df) < 5:
        return None
    if anchor_idx is None:
        window = df.tail(lookback_bars)
        hi_pos = window["high"].argmax()
        lo_pos = window["low"].argmin()
        # Anchor to whichever extreme is MORE RECENT (the active swing leg)
        anchor_pos = max(hi_pos, lo_pos)
        # Translate position in window back to position in full df
        anchor_idx = (len(df) - len(window)) + anchor_pos
    seg = df.iloc[anchor_idx:]
    if seg.empty or "volume" not in seg.columns:
        return None
    typical = (seg["high"] + seg["low"] + seg["close"]) / 3.0
    pv = (typical * seg["volume"]).sum()
    v  = seg["volume"].sum()
    if v <= 0:
        return None
    return float(pv / v)


def compute_round_numbers(price: float, count: int = 3) -> List[float]:
    """Return the `count` nearest round-number levels above and below `price`.
    Step size scales with price so we don't spam tiny stocks with $50 steps.
    """
    if price <= 0:
        return []
    if price < 5:        step = 0.50
    elif price < 20:     step = 1.0
    elif price < 50:     step = 5.0
    elif price < 200:    step = 10.0
    elif price < 1000:   step = 25.0
    elif price < 5000:   step = 100.0
    else:                step = 500.0
    base = round(price / step) * step
    levels = []
    for i in range(-count, count + 1):
        lvl = base + i * step
        if lvl > 0 and lvl != price:
            levels.append(round(lvl, 2))
    return sorted(set(levels))


# ---------------------------------------------------------------------------
# Wave 5 — multi-timeframe levels. CC traders overlay levels from multiple
# timeframes on a single chart: last N Daily H/L, last N Weekly H/L, last N
# Monthly H/L, plus Daily/Weekly/Monthly Pivot Points, plus naked POCs (POCs
# from prior periods that haven't been retraced yet). Every level is labeled
# with its timeframe so the operator can see at a glance "DAILY R1 vs WEEKLY POC".
# ---------------------------------------------------------------------------
def resample_period(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample daily OHLCV to a higher timeframe ('W' = weekly, 'M' = monthly).
    Standard aggregation: open=first, high=max, low=min, close=last, volume=sum.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    if not isinstance(df.index, pd.DatetimeIndex):
        return pd.DataFrame()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    try:
        out = df.resample(rule).agg(agg).dropna()
    except Exception:
        return pd.DataFrame()
    return out


def recent_period_extremes(df: pd.DataFrame, count: int = 3) -> dict:
    """Return the most recent `count` completed-period highs and lows as a
    list of dicts. Use with resampled weekly/monthly bars.
      [{period_end, high, low}, ...]
    """
    out: list[dict] = []
    if df is None or df.empty or len(df) < 2:
        return {"periods": out}
    # Exclude the current (in-progress) period — only use COMPLETED ones.
    completed = df.iloc[:-1].tail(count)
    for ts, row in completed.iterrows():
        out.append({
            "period_end": str(ts.date()) if hasattr(ts, "date") else str(ts),
            "high": float(row["high"]),
            "low":  float(row["low"]),
            "close": float(row["close"]) if "close" in row else None,
        })
    return {"periods": out}


def compute_multi_timeframe_pivots(daily_df: pd.DataFrame) -> dict:
    """Compute Daily / Weekly / Monthly classic Pivot Points all in one call.
    Returns a dict with keys 'daily', 'weekly', 'monthly', each containing the
    pivot-point dict (pp / r1 / r2 / s1 / s2 / prev_h / prev_l / prev_c) for
    that timeframe."""
    out = {}
    if daily_df is None or daily_df.empty:
        return out
    out["daily"] = compute_pivot_points(daily_df)
    weekly = resample_period(daily_df, "W")
    if not weekly.empty and len(weekly) >= 2:
        out["weekly"] = compute_pivot_points(weekly)
    monthly = resample_period(daily_df, "ME")
    if not monthly.empty and len(monthly) >= 2:
        out["monthly"] = compute_pivot_points(monthly)
    return out


def compute_multi_timeframe_volume_profile(daily_df: pd.DataFrame) -> dict:
    """Compute Weekly + Monthly Volume Profile from daily bars.
      • Weekly VP uses the last 5 trading days
      • Monthly VP uses the last 21 trading days
    Each returns {poc, vah, val}.
    """
    out = {}
    if daily_df is None or daily_df.empty:
        return out
    if len(daily_df) >= 5:
        out["weekly"] = compute_volume_profile(daily_df, lookback_bars=5, bins=30)
    if len(daily_df) >= 21:
        out["monthly"] = compute_volume_profile(daily_df, lookback_bars=21, bins=40)
    if len(daily_df) >= 60:
        out["quarterly"] = compute_volume_profile(daily_df, lookback_bars=60, bins=50)
    return out


def find_naked_pocs(daily_df: pd.DataFrame, periods: int = 8) -> List[dict]:
    """Compute the POC of each of the last `periods` weekly windows, and flag
    each as 'naked' if the current price hasn't yet returned to within 0.5% of
    that POC since it was created. nPOCs act like magnets — price tends to
    revisit them.

    Returns: [{period_start, period_end, poc, naked: bool}, ...] for the
    most recent 'naked' POCs (the chart-worthy ones).
    """
    out: list[dict] = []
    if daily_df is None or daily_df.empty or len(daily_df) < 10:
        return out
    if not isinstance(daily_df.index, pd.DatetimeIndex):
        return out
    weekly_groups = list(daily_df.groupby(pd.Grouper(freq="W")))
    if len(weekly_groups) < 2:
        return out
    px_now = float(daily_df["close"].iloc[-1])
    # Walk each completed week backwards. The most recent (current) week is excluded.
    for grp_idx, (week_end, week_df) in enumerate(weekly_groups[:-1]):
        if week_df.empty or len(week_df) < 2:
            continue
        vp = compute_volume_profile(week_df, lookback_bars=len(week_df), bins=20)
        if not vp or "poc" not in vp:
            continue
        poc = float(vp["poc"])
        # Check if any bar AFTER this week traded within 0.5% of poc
        later = daily_df[daily_df.index > week_end]
        if later.empty:
            naked = True
        else:
            tol = abs(poc) * 0.005
            naked = not bool(
                ((later["low"] <= poc + tol) & (later["high"] >= poc - tol)).any()
            )
        if naked:
            out.append({
                "period_end": str(week_end.date()),
                "poc": poc,
                "naked": True,
                "distance_pct": (poc - px_now) / px_now * 100.0 if px_now else 0.0,
            })
    # Return the closest 6 naked POCs to current price (most relevant)
    out.sort(key=lambda x: abs(x["distance_pct"]))
    return out[:6]


# ---------------------------------------------------------------------------
# Backtested conviction values — populated by `--backtest` at startup or by
# the persisted JSON file on disk. Without a backtest the constants below act
# as the "prior" — calibrated against typical CC-style win-rates (45-65%).
# After running `python scan_setups.py --backtest`, real numbers replace these.
# ---------------------------------------------------------------------------
BACKTESTED_CONVICTION: dict[str, float] = {
    # Wave 0 — original CC patterns
    "EMA Pullback":      0.62,
    "CC Region":         0.64,
    "S/R Flip":          0.60,
    "Volume Spike":      0.58,
    "Inside Day":        0.55,
    "RSI Reversal":      0.48,
    # Wave 2
    "3rd Touch":         0.66,    # CC explicitly calls this highest probability
    "Trendline Break":   0.60,
    "ORB":               0.58,
    # Wave 3 — Smart Money Concepts
    "BoS":               0.62,
    "ChoCh":             0.54,    # reversals are inherently lower win-rate
    "Liquidity Grab":    0.58,
    "Order Block":       0.60,
    "FVG":               0.56,
    # Bonus
    "Wyckoff":           0.60,
    "Three Drives":      0.52,
    "Channel":           0.56,
    "VolProfile":        0.54,
    # Wave 8 — chart patterns / harmonics / SMC extensions
    "BB Squeeze":        0.60,
    "Gap":               0.56,
    "Climax":            0.54,
    "Double Top":        0.62,
    "Double Bottom":     0.64,
    "Head & Shoulders":  0.66,
    "Inverse H&S":       0.66,
    "Triangle":          0.60,
    "Wedge":             0.58,
    "Flag":              0.62,
    "Cup Handle":        0.60,
    "ABCD":              0.58,
    "Gartley":           0.62,
    "Bat":               0.62,
    "Butterfly":         0.60,
    "Crab":              0.58,
    "Cypher":            0.60,
    "Shark":             0.56,
    "Wolfe":             0.56,
    "Breaker":           0.58,
    "OTE":               0.62,
}

# Load saved backtest results if present (written by run_backtest()).
_BT_FILE = Path(__file__).parent / "backtest_results.json"
if _BT_FILE.exists():
    try:
        _bt_data = json.loads(_BT_FILE.read_text())
        for k, v in _bt_data.get("conviction", {}).items():
            BACKTESTED_CONVICTION[k] = float(v)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers used across detectors — volume confirmation, smarter stops/targets,
# and bar-pattern classification. These were added in the "senior trader audit"
# round to reflect real CC methodology more faithfully than the original
# mechanical EMA/ATR shortcuts.
# ---------------------------------------------------------------------------
def volume_confirmed(df: pd.DataFrame, threshold: float = 0.8) -> bool:
    """Return True if the most recent bar's volume is at least `threshold`×
    the trailing 20-bar average. CC requires volume confirmation on a pullback
    or breakout bar — without it the move is "stealth" and lower-probability."""
    if len(df) < 22:
        return False
    last_vol = float(df["volume"].iloc[-1])
    vol_avg  = float(df["volume"].iloc[-22:-1].mean())
    return vol_avg > 0 and last_vol >= threshold * vol_avg


def nearest_swing_below(df: pd.DataFrame, price: float, lookback: int = 60, n: int = 5) -> Optional[float]:
    """Find the closest swing low BELOW `price` within the last `lookback` bars.
    Used for placing stops on long setups — much better than mechanical
    EMA-buffer because a swing low is where invalidation actually lives."""
    pivots = swing_pivots(df.tail(lookback), n=n)
    lows = [p.price for p in pivots if p.kind == "low" and p.price < price]
    return max(lows) if lows else None


def nearest_swing_above(df: pd.DataFrame, price: float, lookback: int = 60, n: int = 5) -> Optional[float]:
    """Mirror of nearest_swing_below — stop reference for short setups."""
    pivots = swing_pivots(df.tail(lookback), n=n)
    highs = [p.price for p in pivots if p.kind == "high" and p.price > price]
    return min(highs) if highs else None


def smart_targets_long(df: pd.DataFrame, entry: float, stop: float) -> List[float]:
    """Build 2 target levels for a LONG by climbing through real resistance.
    T1 = nearest resistance above entry (if any).
    T2 = second resistance, OR 2× the T1 distance if only one exists.
    Falls back to 2R / 4R when no S/R data is available."""
    sr = support_resistance(df.tail(200))
    risk = abs(entry - stop)
    resistances_above = [r for r in sorted(sr["resistance"]) if r > entry + 0.5 * risk]
    if len(resistances_above) >= 2:
        return [resistances_above[0], resistances_above[1]]
    if len(resistances_above) == 1:
        t1 = resistances_above[0]
        return [t1, entry + 2 * (t1 - entry)]
    # No S/R found — fall back to symmetric 2R / 4R targets
    return [entry + 2 * risk, entry + 4 * risk]


def smart_targets_short(df: pd.DataFrame, entry: float, stop: float) -> List[float]:
    """Mirror of smart_targets_long for SHORT setups."""
    sr = support_resistance(df.tail(200))
    risk = abs(stop - entry)
    supports_below = [s for s in sorted(sr["support"], reverse=True) if s < entry - 0.5 * risk]
    if len(supports_below) >= 2:
        return [supports_below[0], supports_below[1]]
    if len(supports_below) == 1:
        t1 = supports_below[0]
        return [t1, entry - 2 * (entry - t1)]
    return [entry - 2 * risk, entry - 4 * risk]


def is_bull_confirmation(pat: str) -> bool:
    """Return True if this candlestick pattern adds bullish conviction."""
    return pat in (
        "hammer", "pin-bar-bull", "engulfing-bull", "engulfing",
        "morning-star", "three-white-soldiers", "abandoned-baby-bull",
        "kicker-bull", "piercing-line", "tweezer-bottom",
        "harami-bull", "dragonfly-doji", "marubozu-bull",
    )


def is_bear_confirmation(pat: str) -> bool:
    """Return True if this candlestick pattern adds bearish conviction."""
    return pat in (
        "inverted-hammer", "shooting-star", "pin-bar-bear",
        "engulfing-bear", "engulfing",
        "evening-star", "three-black-crows", "abandoned-baby-bear",
        "kicker-bear", "dark-cloud-cover", "tweezer-top",
        "harami-bear", "gravestone-doji", "marubozu-bear",
    )


def bar_pattern(df: pd.DataFrame) -> str:
    """Comprehensive candlestick classifier — every named pattern in the CC
    cheatsheets, all in one function. Returns the most specific match
    (multi-bar patterns checked first, then single-bar variants).

    Multi-bar patterns:
      'morning-star' | 'evening-star' | 'three-white-soldiers' | 'three-black-crows'
      'kicker-bull' | 'kicker-bear' | 'abandoned-baby-bull' | 'abandoned-baby-bear'
      'piercing-line' | 'dark-cloud-cover' | 'tweezer-bottom' | 'tweezer-top'
      'harami-bull' | 'harami-bear' | 'engulfing-bull' | 'engulfing-bear' | 'engulfing'
      'inside' | 'outside'
    Single-bar:
      'hammer' | 'inverted-hammer' | 'hanging-man' | 'shooting-star'
      'pin-bar-bull' | 'pin-bar-bear' | 'dragonfly-doji' | 'gravestone-doji'
      'long-legged-doji' | 'doji' | 'marubozu-bull' | 'marubozu-bear' | 'spinning-top'
      'neutral'
    """
    if df is None or df.empty:
        return "neutral"
    n = len(df)
    if n < 1:
        return "neutral"

    last = df.iloc[-1]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    body = abs(c - o)
    full = max(h - l, 1e-9)
    up_wick = h - max(c, o)
    lo_wick = min(c, o) - l
    body_pct = body / full
    is_green = c > o
    is_red = c < o

    # ============ 3-bar patterns (need n >= 3) ============
    if n >= 3:
        b2, b1, b0 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        b2_body = abs(b2["close"] - b2["open"])
        b1_body = abs(b1["close"] - b1["open"])
        b0_body = body
        b1_range = b1["high"] - b1["low"]
        # Morning Star: big red, tiny middle, big green that closes above b2's midpoint
        if b2["close"] < b2["open"] and b2_body > 0.5 * (b2["high"]-b2["low"]) \
           and b1_range < 0.4 * (b2["high"]-b2["low"]) \
           and c > o and c > (b2["open"] + b2["close"]) / 2.0:
            return "morning-star"
        # Evening Star: big green, tiny middle, big red that closes below b2's midpoint
        if b2["close"] > b2["open"] and b2_body > 0.5 * (b2["high"]-b2["low"]) \
           and b1_range < 0.4 * (b2["high"]-b2["low"]) \
           and c < o and c < (b2["open"] + b2["close"]) / 2.0:
            return "evening-star"
        # Abandoned Baby Bull: gap-down doji at bottom + gap-up green
        if b1_body < 0.1 * b1_range \
           and b1["high"] < b2["low"] and b1["high"] < l \
           and c > o:
            return "abandoned-baby-bull"
        # Abandoned Baby Bear: gap-up doji at top + gap-down red
        if b1_body < 0.1 * b1_range \
           and b1["low"] > b2["high"] and b1["low"] > h \
           and c < o:
            return "abandoned-baby-bear"
        # Three White Soldiers: 3 consecutive green bars, each closing higher
        if (b2["close"] > b2["open"] and b1["close"] > b1["open"] and is_green
            and b1["close"] > b2["close"] and c > b1["close"]
            and b1["open"] > b2["open"] and o > b1["open"]):
            return "three-white-soldiers"
        # Three Black Crows: 3 consecutive red bars, each closing lower
        if (b2["close"] < b2["open"] and b1["close"] < b1["open"] and is_red
            and b1["close"] < b2["close"] and c < b1["close"]
            and b1["open"] < b2["open"] and o < b1["open"]):
            return "three-black-crows"

    # If the CURRENT bar is a doji (very small body), classify it as doji
    # BEFORE looking at 2-bar patterns — otherwise a doji on top of a flat
    # bar mis-classifies as "engulfing".
    if body_pct < 0.1:
        # Doji sub-variants (long-legged / dragonfly / gravestone) handled below
        # — but mark a quick return now to avoid the 2-bar overrides.
        if lo_wick >= 0.6 * full and up_wick < 0.1 * full:
            return "dragonfly-doji"
        if up_wick >= 0.6 * full and lo_wick < 0.1 * full:
            return "gravestone-doji"
        if lo_wick > 0.3 * full and up_wick > 0.3 * full:
            return "long-legged-doji"
        return "doji"

    # ============ 2-bar patterns (need n >= 2) ============
    if n >= 2:
        prev = df.iloc[-2]
        prev_body = abs(prev["close"] - prev["open"])
        prev_range = max(prev["high"] - prev["low"], 1e-9)
        prev_meaningful = (prev_body / prev_range) >= 0.1   # prev has a real body
        prev_body_hi = max(prev["open"], prev["close"])
        prev_body_lo = min(prev["open"], prev["close"])
        cur_body_hi = max(o, c)
        cur_body_lo = min(o, c)
        # Body-comparison patterns only meaningful if prev has a real body
        if prev_meaningful:
            # Kicker Bull
            if prev["close"] < prev["open"] and o > prev["open"] and is_green:
                return "kicker-bull"
            if prev["close"] > prev["open"] and o < prev["open"] and is_red:
                return "kicker-bear"
            if prev["close"] < prev["open"] and o < prev["low"] \
               and is_green and c > (prev["open"] + prev["close"]) / 2.0 and c < prev["open"]:
                return "piercing-line"
            if prev["close"] > prev["open"] and o > prev["high"] \
               and is_red and c < (prev["open"] + prev["close"]) / 2.0 and c > prev["open"]:
                return "dark-cloud-cover"
            if prev["close"] < prev["open"] and is_green \
               and abs(prev["low"] - l) < 0.05 * full:
                return "tweezer-bottom"
            if prev["close"] > prev["open"] and is_red \
               and abs(prev["high"] - h) < 0.05 * full:
                return "tweezer-top"
            if (prev["close"] < prev["open"] and is_green
                and cur_body_hi >= prev_body_hi and cur_body_lo <= prev_body_lo
                and body > prev_body):
                return "engulfing-bull"
            if (prev["close"] > prev["open"] and is_red
                and cur_body_hi >= prev_body_hi and cur_body_lo <= prev_body_lo
                and body > prev_body):
                return "engulfing-bear"
            if (cur_body_hi >= prev_body_hi and cur_body_lo <= prev_body_lo
                and body > prev_body):
                return "engulfing"
            if prev["close"] < prev["open"] and is_green \
               and cur_body_hi <= prev_body_hi and cur_body_lo >= prev_body_lo \
               and body < 0.7 * prev_body:
                return "harami-bull"
            if prev["close"] > prev["open"] and is_red \
               and cur_body_hi <= prev_body_hi and cur_body_lo >= prev_body_lo \
               and body < 0.7 * prev_body:
                return "harami-bear"
        # Inside / Outside use range, not body — safe regardless of prev body
        if h <= prev["high"] and l >= prev["low"]:
            return "inside"
        if h >= prev["high"] and l <= prev["low"]:
            return "outside"

    # ============ Single-bar patterns ============
    # Doji variants — body very small relative to range
    if body_pct < 0.1:
        # Dragonfly Doji — long lower wick, open=high=close
        if lo_wick >= 0.6 * full and up_wick < 0.1 * full:
            return "dragonfly-doji"
        # Gravestone Doji — long upper wick, open=low=close
        if up_wick >= 0.6 * full and lo_wick < 0.1 * full:
            return "gravestone-doji"
        # Long-legged Doji — both wicks substantial
        if lo_wick > 0.3 * full and up_wick > 0.3 * full:
            return "long-legged-doji"
        return "doji"
    # Marubozu — body is ~90%+ of range (very small wicks)
    if body_pct >= 0.88:
        return "marubozu-bull" if is_green else "marubozu-bear"
    # Spinning Top — small body, wicks on both sides similar size
    if body_pct < 0.35 and lo_wick > body and up_wick > body \
       and 0.5 <= (up_wick / max(lo_wick, 1e-9)) <= 2.0:
        return "spinning-top"
    # Hammer — long lower wick, tiny upper wick (bullish reversal at bottom)
    if lo_wick >= 2 * body and up_wick < body:
        # Pin Bar Bull = stricter hammer (lower wick >= 2/3 of full range)
        if lo_wick / full >= 0.6:
            return "pin-bar-bull"
        return "hammer"
    # Hanging Man — same shape as hammer but appears after up move (we can't
    # know the trend context here without more bars, so we return "hanging-man"
    # only when the bar closes below its open AND has the hammer shape).
    # Inverted Hammer / Shooting Star — long upper wick
    if up_wick >= 2 * body and lo_wick < body:
        if up_wick / full >= 0.6:
            return "pin-bar-bear"
        # Shooting Star variant — bearish (closes lower than open)
        if is_red:
            return "shooting-star"
        return "inverted-hammer"
    return "neutral"


# ---------------------------------------------------------------------------
# Wave 2 + Wave 3 helpers — market structure, FVGs, order blocks, volume
# profile, trendlines. These are the foundations for the next round of
# detectors and reflect deeper CC methodology than the EMA/ATR shortcuts.
# ---------------------------------------------------------------------------
def classify_market_structure(df: pd.DataFrame, lookback: int = 150, n: int = 5) -> List[dict]:
    """Walk the swing pivots from oldest to newest and classify each one
    relative to the previous SAME-KIND pivot:
      HH = high higher than previous high
      LH = high lower than previous high
      HL = low higher than previous low
      LL = low lower than previous low
    The sequence of these tags is the market structure. An uptrend is a string
    of HH/HL; a downtrend is LH/LL. A break of structure (BoS) confirms the
    trend; a change of character (ChoCh) flips it.
    Returns a list of dicts:
      [{idx, price, kind: 'high'|'low', label: 'HH'|'LH'|'HL'|'LL'}, ...]
    """
    pivots = swing_pivots(df.tail(lookback), n=n)
    out: list[dict] = []
    last_high = None
    last_low = None
    for p in pivots:
        if p.kind == "high":
            label = "HH" if last_high is not None and p.price > last_high else (
                    "LH" if last_high is not None else "H?")
            last_high = p.price
        else:
            label = "HL" if last_low is not None and p.price > last_low else (
                    "LL" if last_low is not None else "L?")
            last_low = p.price
        out.append({"idx": p.idx, "price": p.price, "kind": p.kind, "label": label})
    return out


def detect_trend_from_structure(structure: List[dict]) -> str:
    """Return 'up', 'down', or 'range' based on the last 4 pivots' labels.
    Uptrend = predominantly HH/HL. Downtrend = LH/LL. Mixed = range.
    """
    if not structure:
        return "range"
    recent = structure[-6:]
    labels = [s["label"] for s in recent if s["label"] in ("HH","HL","LH","LL")]
    if not labels:
        return "range"
    bull = sum(1 for l in labels if l in ("HH","HL"))
    bear = sum(1 for l in labels if l in ("LH","LL"))
    if bull >= bear * 2 and bull >= 2:
        return "up"
    if bear >= bull * 2 and bear >= 2:
        return "down"
    return "range"


def find_fvgs(df: pd.DataFrame, lookback: int = 100) -> List[dict]:
    """Detect Fair Value Gaps in the recent `lookback` bars. A bullish FVG is
    a 3-bar pattern where bar[0].high < bar[2].low (price moved up so fast it
    left an imbalance). Bearish FVG: bar[0].low > bar[2].high.
    Returns:
      [{idx, kind: 'bull'|'bear', top, bot, filled: bool}, ...]
    The `filled` flag is True if a later bar's range crossed back through
    the gap zone (in which case the FVG has been mitigated).
    """
    out: list[dict] = []
    if df is None or df.empty or len(df) < 4:
        return out
    window = df.tail(lookback).reset_index(drop=False)
    n = len(window)
    for i in range(n - 2):
        b0 = window.iloc[i]
        b1 = window.iloc[i + 1]
        b2 = window.iloc[i + 2]
        # Bullish FVG: gap between b0.high and b2.low
        if b0["high"] < b2["low"]:
            top = float(b2["low"])
            bot = float(b0["high"])
            mid = (top + bot) / 2.0
            # Check if any later bar reached back into the gap (mitigation)
            later = window.iloc[i + 3:]
            filled = (not later.empty) and bool((later["low"] <= mid).any())
            out.append({"idx": i + 1, "kind": "bull", "top": top, "bot": bot, "filled": filled})
        # Bearish FVG
        if b0["low"] > b2["high"]:
            top = float(b0["low"])
            bot = float(b2["high"])
            mid = (top + bot) / 2.0
            later = window.iloc[i + 3:]
            filled = (not later.empty) and bool((later["high"] >= mid).any())
            out.append({"idx": i + 1, "kind": "bear", "top": top, "bot": bot, "filled": filled})
    return out


def find_order_blocks(df: pd.DataFrame, lookback: int = 100, impulse_atrs: float = 2.0) -> List[dict]:
    """An institutional order block is the LAST opposite-color candle BEFORE a
    strong impulsive move. Bullish OB = last red candle before a rally of
    ≥ impulse_atrs × ATR over the next ~3 bars. Bearish OB = mirror.
    Returns:
      [{idx, kind: 'bull'|'bear', top, bot, mid, broken: bool}, ...]
    `broken` is True if price has since traded through the OB (mitigated).
    """
    out: list[dict] = []
    if df is None or df.empty or len(df) < 30:
        return out
    a = atr(df, 14)
    window = df.tail(lookback).reset_index(drop=False)
    atr_w = a.tail(lookback).reset_index(drop=True)
    n = len(window)
    for i in range(2, n - 4):
        atrv = float(atr_w.iloc[i]) if i < len(atr_w) and pd.notna(atr_w.iloc[i]) else 0
        if atrv <= 0:
            continue
        b = window.iloc[i]
        # Look at next 3 bars cumulative move
        future = window.iloc[i + 1: i + 5]
        if future.empty:
            continue
        move_up = float(future["high"].max() - b["close"])
        move_dn = float(b["close"] - future["low"].min())
        # Bullish OB: this bar is RED (close<open) and next 3 bars rally ≥ N ATR
        if b["close"] < b["open"] and move_up >= impulse_atrs * atrv:
            top = float(b["high"])
            bot = float(b["low"])
            later = window.iloc[i + 5:]
            broken = (not later.empty) and bool((later["low"] <= bot).any())
            out.append({"idx": i, "kind": "bull", "top": top, "bot": bot,
                        "mid": (top + bot) / 2.0, "broken": broken})
        # Bearish OB: this bar is GREEN (close>open) and next 3 bars drop ≥ N ATR
        if b["close"] > b["open"] and move_dn >= impulse_atrs * atrv:
            top = float(b["high"])
            bot = float(b["low"])
            later = window.iloc[i + 5:]
            broken = (not later.empty) and bool((later["high"] >= top).any())
            out.append({"idx": i, "kind": "bear", "top": top, "bot": bot,
                        "mid": (top + bot) / 2.0, "broken": broken})
    return out


def compute_volume_profile(df: pd.DataFrame, lookback_bars: int = 60, bins: int = 40) -> dict:
    """Compute a simple Volume Profile over the last `lookback_bars`:
      • POC = price bin with the highest traded volume
      • VAH/VAL = Value Area High/Low — the band around POC containing 70% of volume
    Volume is approximated as evenly distributed within each bar's H–L range
    (standard simplification when only OHLCV is available).
    """
    if df is None or df.empty or len(df) < 10 or "volume" not in df.columns:
        return {}
    window = df.tail(lookback_bars).copy()
    lo = float(window["low"].min())
    hi = float(window["high"].max())
    if hi <= lo:
        return {}
    edges = np.linspace(lo, hi, bins + 1)
    vol_per_bin = np.zeros(bins)
    for _, row in window.iterrows():
        # Distribute the bar's volume across the bins it spans
        b_lo, b_hi, v = float(row["low"]), float(row["high"]), float(row["volume"] or 0)
        if v <= 0 or b_hi <= b_lo:
            continue
        i0 = max(0, int((b_lo - lo) / (hi - lo) * bins))
        i1 = min(bins - 1, int((b_hi - lo) / (hi - lo) * bins))
        if i1 <= i0:
            vol_per_bin[i0] += v
            continue
        spread = i1 - i0 + 1
        per = v / spread
        for k in range(i0, i1 + 1):
            vol_per_bin[k] += per
    poc_idx = int(vol_per_bin.argmax())
    poc = (edges[poc_idx] + edges[poc_idx + 1]) / 2.0
    total_v = vol_per_bin.sum()
    if total_v <= 0:
        return {"poc": poc}
    # Expand outward from POC until we capture 70% of total volume
    target = 0.70 * total_v
    captured = vol_per_bin[poc_idx]
    lo_i, hi_i = poc_idx, poc_idx
    while captured < target and (lo_i > 0 or hi_i < bins - 1):
        next_lo = vol_per_bin[lo_i - 1] if lo_i > 0 else -1
        next_hi = vol_per_bin[hi_i + 1] if hi_i < bins - 1 else -1
        if next_hi >= next_lo and hi_i < bins - 1:
            hi_i += 1
            captured += vol_per_bin[hi_i]
        elif lo_i > 0:
            lo_i -= 1
            captured += vol_per_bin[lo_i]
        else:
            break
    val = edges[lo_i]
    vah = edges[hi_i + 1]
    return {"poc": float(poc), "vah": float(vah), "val": float(val),
            "lookback_bars": lookback_bars}


def fit_trendline(df: pd.DataFrame, kind: str = "support", lookback: int = 60, n: int = 5) -> Optional[dict]:
    """Fit a simple trendline through the 2 most recent swing lows (support)
    or swing highs (resistance) in the lookback window.
    Returns {slope, intercept, value_at_last_bar, anchor_a, anchor_b}, or None
    if there aren't enough pivots of the requested kind.
    """
    pivots = swing_pivots(df.tail(lookback), n=n)
    same = [p for p in pivots if p.kind == ("low" if kind == "support" else "high")]
    if len(same) < 2:
        return None
    a, b = same[-2], same[-1]
    # Use integer bar offsets as x (df.index might be datetime). Convert pivot
    # times to absolute positions.
    window = df.tail(lookback)
    try:
        pos_a = window.index.get_loc(a.idx)
        pos_b = window.index.get_loc(b.idx)
    except KeyError:
        return None
    if pos_b == pos_a:
        return None
    slope = (b.price - a.price) / (pos_b - pos_a)
    intercept = a.price - slope * pos_a
    last_pos = len(window) - 1
    value_now = slope * last_pos + intercept
    return {"slope": slope, "intercept": intercept,
            "value_at_last_bar": float(value_now),
            "anchor_a": {"price": a.price, "pos": pos_a},
            "anchor_b": {"price": b.price, "pos": pos_b}}


# ---------------------------------------------------------------------------
# Detectors — Chart Champions rules
# ---------------------------------------------------------------------------
def detect_ema_pullback(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """First 18.pdf p.67 — EMA 55/100/200 alignment + pullback.
    Requires volume confirmation AND a bullish/bearish bar pattern on the
    trigger bar. Stops anchor to nearest swing pivot, targets use real S/R.
    Conviction is set by the backtest engine when available."""
    if len(df) < 220:
        return None
    close = df["close"]
    e55, e100, e200 = ema(close, 55), ema(close, 100), ema(close, 200)
    a = atr(df, 14)
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(a.iloc[-1])
    e55_, e100_, e200_ = float(e55.iloc[-1]), float(e100.iloc[-1]), float(e200.iloc[-1])
    if np.isnan(e200_) or np.isnan(atrv):
        return None
    if not volume_confirmed(df):
        return None
    pat = bar_pattern(df)
    base_conv = BACKTESTED_CONVICTION.get("EMA Pullback", 0.62)

    if e55_ > e100_ > e200_ and px > e55_ and (px - e55_) <= atrv:
        # Stop = nearest swing low below entry (CC: invalidation lives at the
        # last respected low). Use ATR buffer to avoid wick-outs.
        swing_low = nearest_swing_below(df, px, lookback=80)
        ema_stop  = e55_ - 0.3 * atrv
        stop = min(swing_low, ema_stop) if swing_low is not None else ema_stop
        # Confirmation pattern boost: hammer/engulfing add to conviction.
        conv = base_conv + (0.10 if is_bull_confirmation(pat) else 0.0)
        targets = smart_targets_long(df, px, stop)
        return Setup(
            symbol, "EMA 55/100/200 Pullback (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.92, conv),
            reasoning=f"Bull align 55>100>200, price pulling back to EMA55 (${e55_:.2f}). Vol confirmed, bar pattern: {pat}. Stop at swing-low ${stop:.2f}.",
            citation="First 18.pdf p.67",
        )

    if e55_ < e100_ < e200_ and px < e55_ and (e55_ - px) <= atrv:
        swing_high = nearest_swing_above(df, px, lookback=80)
        ema_stop = e55_ + 0.3 * atrv
        stop = max(swing_high, ema_stop) if swing_high is not None else ema_stop
        conv = base_conv + (0.10 if is_bear_confirmation(pat) else 0.0)
        targets = smart_targets_short(df, px, stop)
        return Setup(
            symbol, "EMA 55/100/200 Pullback (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.92, conv - 0.05),
            reasoning=f"Bear align 55<100<200, price reaching EMA55 (${e55_:.2f}). Vol confirmed, bar pattern: {pat}. Stop at swing-high ${stop:.2f}.",
            citation="First 18.pdf p.67",
        )
    return None


def detect_cc_region_pullback(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """First 18.pdf p.1, p.63 — pullback into 0.618–0.66 CC region."""
    pivots = swing_pivots(df.tail(150), n=5)
    if len(pivots) < 2:
        return None
    a, b = pivots[-2], pivots[-1]
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])

    if not volume_confirmed(df):
        return None
    pat = bar_pattern(df)
    base = BACKTESTED_CONVICTION.get("CC Region", 0.64)

    if a.kind == "low" and b.kind == "high" and b.price > a.price:
        lo, hi = cc_region(b.price, a.price)
        if float(last["low"]) <= hi and px > lo:
            stop = lo - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
            return Setup(
                symbol, "CC Region Pullback (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.92, conv),
                reasoning=f"Price wicked into CC region ${lo:.2f}–${hi:.2f} (0.618–0.66 retracement) and closed above. Bar: {pat}.",
                citation="First 18.pdf p.1, p.63",
            )
    if a.kind == "high" and b.kind == "low" and b.price < a.price:
        lo, hi = cc_region(a.price, b.price)
        if float(last["high"]) >= lo and px < hi:
            stop = hi + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
            return Setup(
                symbol, "CC Region Pullback (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.92, conv - 0.05),
                reasoning=f"Bearish CC region rejection at ${lo:.2f}–${hi:.2f}. Bar: {pat}.",
                citation="First 18.pdf p.1 (inverted)",
            )
    return None


def detect_sr_flip(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """First 18.pdf p.61 — broken level retested in the opposite role.
    Requires volume confirmation + a confirming bar pattern."""
    if df is None or df.empty or len(df) < 20:
        return None
    sr = support_resistance(df.tail(200))
    last = df.iloc[-1]
    px = float(last["close"])
    lo = float(last["low"])
    hi = float(last["high"])
    atrv = float(atr(df, 14).iloc[-1])
    if pd.isna(atrv):
        return None
    if not volume_confirmed(df):
        return None
    pat = bar_pattern(df)
    base = BACKTESTED_CONVICTION.get("S/R Flip", 0.60)

    for level in sr["resistance"]:
        if lo <= level <= px and (px - level) <= 0.5 * atrv:
            stop = level - 0.5 * atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
            return Setup(
                symbol, "Resistance Flip to Support (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.92, conv),
                reasoning=f"Former resistance ${level:.2f} broken and retested as support. Bar: {pat}.",
                citation="First 18.pdf p.61",
            )
    for level in sr["support"]:
        if px <= level <= hi and (level - px) <= 0.5 * atrv:
            stop = level + 0.5 * atrv
            targets = smart_targets_short(df, px, stop)
            conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
            return Setup(
                symbol, "Support Flip to Resistance (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.92, conv - 0.05),
                reasoning=f"Former support ${level:.2f} broken and retested as resistance. Bar: {pat}.",
                citation="First 18.pdf p.61 (inverted)",
            )
    return None


def detect_volume_spike(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Second 18.pdf p.18 — new 20-bar high/low on 2x avg volume.
    This already has volume confirmation baked in (it IS the signal).
    Stops use the recent range edge; targets use real S/R."""
    if len(df) < 25:
        return None
    last = df.iloc[-1]
    window = df.iloc[-21:-1]
    vol_avg = float(window["volume"].mean())
    if vol_avg == 0 or float(last["volume"]) < 2.0 * vol_avg:
        return None
    high20 = float(window["high"].max())
    low20 = float(window["low"].min())
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("Volume Spike", 0.58)
    pat = bar_pattern(df)

    if px > high20:
        stop = high20 - 0.5 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(
            symbol, "Volume Spike Breakout (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.92, conv),
            reasoning=f"New 20-bar high on {float(last['volume'])/vol_avg:.1f}× avg volume. Bar: {pat}.",
            citation="Second 18.pdf p.18",
        )
    if px < low20:
        stop = low20 + 0.5 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(
            symbol, "Volume Spike Breakdown (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.92, conv - 0.05),
            reasoning=f"New 20-bar low on {float(last['volume'])/vol_avg:.1f}× volume. Bar: {pat}.",
            citation="Second 18.pdf p.18 (inverted)",
        )
    return None


def detect_inside_day(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """First 18.pdf p.43 — inside day breakout. Requires breakout-day volume."""
    if len(df) < 25:
        return None
    d2, d1, d0 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if not (d1["high"] <= d2["high"] and d1["low"] >= d2["low"]):
        return None
    if not volume_confirmed(df):
        return None
    atrv = float(atr(df, 14).iloc[-1])
    px = float(d0["close"])
    base = BACKTESTED_CONVICTION.get("Inside Day", 0.55)

    if px > d1["high"]:
        stop = float(d1["low"]) - 0.2 * atrv
        targets = smart_targets_long(df, px, stop)
        return Setup(
            symbol, "Inside Day Breakout (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=base,
            reasoning=f"Inside-day breakout above ${d1['high']:.2f}. Vol confirmed.",
            citation="First 18.pdf p.43",
        )
    if px < d1["low"]:
        stop = float(d1["high"]) + 0.2 * atrv
        targets = smart_targets_short(df, px, stop)
        return Setup(
            symbol, "Inside Day Breakdown (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=base - 0.05,
            reasoning=f"Inside-day breakdown below ${d1['low']:.2f}. Vol confirmed.",
            citation="First 18.pdf p.43 (inverted)",
        )
    return None


def detect_rsi_reversal(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Second 18.pdf p.1 — Entry Triggers / RSI extremes. Volume + bar pattern
    confirmation tightened. Stops/targets use real S/R."""
    r = rsi(df["close"], 14)
    if len(r.dropna()) < 3:
        return None
    r_now, r_prev = float(r.iloc[-1]), float(r.iloc[-2])
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if not volume_confirmed(df, threshold=1.0):  # tighter: full avg vol
        return None
    pat = bar_pattern(df)
    base = BACKTESTED_CONVICTION.get("RSI Reversal", 0.48)

    if r_prev < 30 and r_now >= 30:
        swing_low = nearest_swing_below(df, px, lookback=40)
        stop = swing_low - 0.3 * atrv if swing_low is not None else px * 0.97
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
        return Setup(
            symbol, "RSI Oversold Reversal (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.88, conv),
            reasoning=f"RSI exiting oversold ({r_prev:.1f}→{r_now:.1f}). Vol confirmed, bar: {pat}.",
            citation="Second 18.pdf p.1",
        )
    if r_prev > 70 and r_now <= 70:
        swing_high = nearest_swing_above(df, px, lookback=40)
        stop = swing_high + 0.3 * atrv if swing_high is not None else px * 1.03
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
        return Setup(
            symbol, "RSI Overbought Reversal (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.88, conv - 0.05),
            reasoning=f"RSI exiting overbought ({r_prev:.1f}→{r_now:.1f}). Vol confirmed, bar: {pat}.",
            citation="Second 18.pdf p.1 (inverted)",
        )
    return None


# ---------------------------------------------------------------------------
# Wave 2 — 3rd touch, trendline break+retest, ORB
# ---------------------------------------------------------------------------
def detect_third_touch(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """CC: 'The 3rd touch is the highest-probability touch.' We find any level
    that has been touched exactly TWICE in the past 100 bars and the current
    bar is now approaching that level for the 3rd time."""
    if df is None or df.empty or len(df) < 30:
        return None
    if not volume_confirmed(df):
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("3rd Touch", 0.66)
    pat = bar_pattern(df)
    pivots = swing_pivots(df.tail(120), n=4)
    tol = max(atrv * 0.5, px * 0.005)
    # Group pivots by price into clusters
    clusters: list[list] = []
    for p in pivots:
        attached = False
        for cl in clusters:
            avg = sum(x.price for x in cl) / len(cl)
            if abs(p.price - avg) <= tol:
                cl.append(p); attached = True; break
        if not attached:
            clusters.append([p])
    for cl in clusters:
        if len(cl) != 2:
            continue
        level = sum(x.price for x in cl) / 2.0
        kinds = [x.kind for x in cl]
        # Approaching the level for 3rd touch when price is within 0.7 ATR
        if abs(px - level) > 0.7 * atrv:
            continue
        # All highs cluster → resistance test, look for SHORT
        if all(k == "high" for k in kinds) and px <= level:
            stop = level + 0.5 * atrv
            targets = smart_targets_short(df, px, stop)
            conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
            return Setup(symbol, f"3rd Touch @ ${level:.2f} (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.90, conv - 0.05),
                reasoning=f"Price approaching ${level:.2f} for 3rd time — 2 prior rejections at this level. Bar: {pat}.",
                citation="First 18.pdf p.66 — 3rd touch is highest probability")
        # All lows cluster → support test, look for LONG
        if all(k == "low" for k in kinds) and px >= level:
            stop = level - 0.5 * atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
            return Setup(symbol, f"3rd Touch @ ${level:.2f} (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.90, conv),
                reasoning=f"Price approaching ${level:.2f} for 3rd time — 2 prior holds at this level. Bar: {pat}.",
                citation="First 18.pdf p.66 — 3rd touch is highest probability")
    return None


def detect_trendline_break(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Detect a broken trendline followed by a retest. CC: 'A broken trendline
    becomes the opposite-role level on retest.' If the support trendline was
    broken below and price has now retraced UP to the line, we're SHORT. If a
    resistance trendline was broken above and price retraced back DOWN, LONG."""
    if df is None or df.empty or len(df) < 30:
        return None
    if not volume_confirmed(df):
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("Trendline Break", 0.60)
    pat = bar_pattern(df)
    # Support trendline (rising lows) — if broken below + now retest from below
    tl_sup = fit_trendline(df, kind="support", lookback=80)
    if tl_sup is not None:
        line = tl_sup["value_at_last_bar"]
        # Was broken: at least one of the last 5 bars closed below the line
        last5 = df.tail(5)
        broken = bool((last5["close"] < line - 0.3 * atrv).any())
        if broken and abs(px - line) <= 0.5 * atrv and px <= line:
            stop = line + 0.7 * atrv
            targets = smart_targets_short(df, px, stop)
            conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
            return Setup(symbol, "Broken trendline retest (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.88, conv - 0.05),
                reasoning=f"Rising support trendline broken; price retesting from below at ${line:.2f}. Bar: {pat}.",
                citation="First 18.pdf — trendline role reversal")
    # Resistance trendline (falling highs) — if broken above + now retest from above
    tl_res = fit_trendline(df, kind="resistance", lookback=80)
    if tl_res is not None:
        line = tl_res["value_at_last_bar"]
        last5 = df.tail(5)
        broken = bool((last5["close"] > line + 0.3 * atrv).any())
        if broken and abs(px - line) <= 0.5 * atrv and px >= line:
            stop = line - 0.7 * atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
            return Setup(symbol, "Broken trendline retest (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.88, conv),
                reasoning=f"Falling resistance trendline broken; price retesting from above at ${line:.2f}. Bar: {pat}.",
                citation="First 18.pdf — trendline role reversal")
    return None


def detect_orb_breakout(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Opening Range Breakout — adapted for daily bars. The 'opening range' is
    the prior 5 trading days; today's close breaking that range with volume
    is the signal. This is the daily-bar version of the classic intraday ORB."""
    if df is None or df.empty or len(df) < 8:
        return None
    if not volume_confirmed(df, threshold=1.2):
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("ORB", 0.58)
    pat = bar_pattern(df)
    # 5-bar range (the "opening range")
    rng = df.iloc[-6:-1]
    rng_hi = float(rng["high"].max())
    rng_lo = float(rng["low"].min())
    if px > rng_hi:
        stop = rng_lo - 0.3 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(symbol, "ORB Breakout (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.88, conv),
            reasoning=f"Break above 5-bar opening range ${rng_lo:.2f}–${rng_hi:.2f} on confirming volume. Bar: {pat}.",
            citation="Second 18.pdf — Opening Range Breakout")
    if px < rng_lo:
        stop = rng_hi + 0.3 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(symbol, "ORB Breakdown (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.88, conv - 0.05),
            reasoning=f"Break below 5-bar opening range ${rng_lo:.2f}–${rng_hi:.2f} on confirming volume. Bar: {pat}.",
            citation="Second 18.pdf — Opening Range Breakdown")
    return None


# ---------------------------------------------------------------------------
# Wave 3 — BoS, ChoCh, liquidity grabs, order blocks, FVGs
# ---------------------------------------------------------------------------
def detect_bos(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Break of Structure — in an uptrend (HH/HL series), price closes above
    the most recent swing HIGH. Confirms trend continuation."""
    if df is None or df.empty or len(df) < 30:
        return None
    if not volume_confirmed(df):
        return None
    structure = classify_market_structure(df, lookback=120, n=4)
    trend = detect_trend_from_structure(structure)
    if trend == "range":
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("BoS", 0.62)
    pat = bar_pattern(df)
    # Find most recent swing high and most recent swing low
    last_high = next((s for s in reversed(structure) if s["kind"] == "high"), None)
    last_low  = next((s for s in reversed(structure) if s["kind"] == "low"),  None)
    if last_high is None or last_low is None:
        return None
    if trend == "up" and px > last_high["price"] and px - last_high["price"] <= 0.5 * atrv:
        stop = last_low["price"] - 0.2 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(symbol, "BoS (continuation long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.90, conv),
            reasoning=f"Uptrend BoS — price closed above prior swing high ${last_high['price']:.2f}, structure intact. Bar: {pat}.",
            citation="Smart Money Concepts — Break of Structure")
    if trend == "down" and px < last_low["price"] and last_low["price"] - px <= 0.5 * atrv:
        stop = last_high["price"] + 0.2 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(symbol, "BoS (continuation short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.90, conv - 0.05),
            reasoning=f"Downtrend BoS — price closed below prior swing low ${last_low['price']:.2f}. Bar: {pat}.",
            citation="Smart Money Concepts — Break of Structure")
    return None


def detect_choch(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Change of Character — established uptrend breaks below an HL (lower
    low for the first time), or downtrend breaks above an LH. Early reversal
    signal — more risk than BoS but higher reward."""
    if df is None or df.empty or len(df) < 40:
        return None
    if not volume_confirmed(df):
        return None
    structure = classify_market_structure(df, lookback=150, n=4)
    trend = detect_trend_from_structure(structure[:-1] if len(structure) > 4 else structure)
    if trend == "range" or len(structure) < 4:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("ChoCh", 0.54)
    pat = bar_pattern(df)
    # In an uptrend, the most recent HL is the structural support to watch.
    if trend == "up":
        last_HL = next((s for s in reversed(structure) if s["label"] == "HL"), None)
        if last_HL and px < last_HL["price"] and last_HL["price"] - px <= 0.7 * atrv:
            last_high_after = next((s for s in reversed(structure) if s["kind"] == "high"), None)
            stop = (last_high_after["price"] + 0.3 * atrv) if last_high_after else px + atrv
            targets = smart_targets_short(df, px, stop)
            conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
            return Setup(symbol, "ChoCh (reversal short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.85, conv - 0.05),
                reasoning=f"Uptrend ChoCh — price broke below structural HL ${last_HL['price']:.2f}. Bar: {pat}.",
                citation="Smart Money Concepts — Change of Character")
    if trend == "down":
        last_LH = next((s for s in reversed(structure) if s["label"] == "LH"), None)
        if last_LH and px > last_LH["price"] and px - last_LH["price"] <= 0.7 * atrv:
            last_low_after = next((s for s in reversed(structure) if s["kind"] == "low"), None)
            stop = (last_low_after["price"] - 0.3 * atrv) if last_low_after else px - atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
            return Setup(symbol, "ChoCh (reversal long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.85, conv),
                reasoning=f"Downtrend ChoCh — price broke above structural LH ${last_LH['price']:.2f}. Bar: {pat}.",
                citation="Smart Money Concepts — Change of Character")
    return None


def detect_liquidity_grab(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Stop hunt / liquidity grab. The current bar wicks BEYOND a prior swing
    extreme (above last high or below last low) but CLOSES back inside the
    range. Classic reversal signal — institutions take out retail stops."""
    if df is None or df.empty or len(df) < 30:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    hi = float(last["high"])
    lo = float(last["low"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("Liquidity Grab", 0.58)
    pat = bar_pattern(df)
    pivots = swing_pivots(df.iloc[:-1].tail(50), n=4)
    recent_high = max((p.price for p in pivots if p.kind == "high"), default=None)
    recent_low  = min((p.price for p in pivots if p.kind == "low"),  default=None)
    # Bullish grab — wicked BELOW prior low but closed back above it
    if recent_low is not None and lo < recent_low and px > recent_low:
        stop = lo - 0.2 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
        return Setup(symbol, "Liquidity grab below low (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.85, conv),
            reasoning=f"Wicked below prior low ${recent_low:.2f} but closed back inside. Stops were swept. Bar: {pat}.",
            citation="Smart Money Concepts — liquidity sweep / stop hunt")
    # Bearish grab — wicked ABOVE prior high but closed back below it
    if recent_high is not None and hi > recent_high and px < recent_high:
        stop = hi + 0.2 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
        return Setup(symbol, "Liquidity grab above high (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.85, conv - 0.05),
            reasoning=f"Wicked above prior high ${recent_high:.2f} but closed back inside. Stops were swept. Bar: {pat}.",
            citation="Smart Money Concepts — liquidity sweep / stop hunt")
    return None


def detect_order_block_retest(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Price returns to an unbroken institutional order block. Bullish OB is
    the last red candle before a strong rally; bearish OB the last green
    candle before a strong drop. Retest = high-probability reaction zone."""
    if df is None or df.empty or len(df) < 30:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    lo = float(last["low"])
    hi = float(last["high"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("Order Block", 0.60)
    pat = bar_pattern(df)
    obs = find_order_blocks(df, lookback=80)
    # Use only UNBROKEN order blocks closest to current price
    bull_obs = sorted([o for o in obs if o["kind"] == "bull" and not o["broken"]],
                      key=lambda o: o["top"], reverse=True)
    bear_obs = sorted([o for o in obs if o["kind"] == "bear" and not o["broken"]],
                      key=lambda o: o["bot"])
    for ob in bull_obs:
        if ob["bot"] <= lo <= ob["top"] and px > ob["mid"]:
            stop = ob["bot"] - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
            return Setup(symbol, f"Bull Order Block retest @ ${ob['mid']:.2f}", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.88, conv),
                reasoning=f"Price tagged unbroken bullish order block ${ob['bot']:.2f}–${ob['top']:.2f} and held. Bar: {pat}.",
                citation="Smart Money Concepts — Order Block retest")
    for ob in bear_obs:
        if ob["bot"] <= hi <= ob["top"] and px < ob["mid"]:
            stop = ob["top"] + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
            return Setup(symbol, f"Bear Order Block retest @ ${ob['mid']:.2f}", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.88, conv - 0.05),
                reasoning=f"Price tagged unbroken bearish order block ${ob['bot']:.2f}–${ob['top']:.2f} and rejected. Bar: {pat}.",
                citation="Smart Money Concepts — Order Block retest")
    return None


def detect_fvg_fill(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Price returns to fill an UNFILLED Fair Value Gap. Bullish FVG = price
    pulled back into the gap zone (above the gap remains intact = bullish bias
    continues). Bearish FVG = price rallied into the gap zone (resistance)."""
    if df is None or df.empty or len(df) < 30:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    lo = float(last["low"])
    hi = float(last["high"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("FVG", 0.56)
    pat = bar_pattern(df)
    fvgs = find_fvgs(df, lookback=80)
    # Bullish FVG: gap zone is SUPPORT — price pulled down INTO it from above
    bull_fvgs = [f for f in fvgs if f["kind"] == "bull" and not f["filled"]]
    bear_fvgs = [f for f in fvgs if f["kind"] == "bear" and not f["filled"]]
    for fvg in bull_fvgs:
        mid = (fvg["top"] + fvg["bot"]) / 2.0
        if fvg["bot"] <= lo <= fvg["top"] and px > mid:
            stop = fvg["bot"] - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
            return Setup(symbol, f"Bullish FVG fill @ ${mid:.2f}", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.85, conv),
                reasoning=f"Price tagged bullish FVG ${fvg['bot']:.2f}–${fvg['top']:.2f} and held. Bar: {pat}.",
                citation="Smart Money Concepts — Fair Value Gap fill")
    for fvg in bear_fvgs:
        mid = (fvg["top"] + fvg["bot"]) / 2.0
        if fvg["bot"] <= hi <= fvg["top"] and px < mid:
            stop = fvg["top"] + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
            return Setup(symbol, f"Bearish FVG fill @ ${mid:.2f}", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=min(0.85, conv - 0.05),
                reasoning=f"Price tagged bearish FVG ${fvg['bot']:.2f}–${fvg['top']:.2f} and rejected. Bar: {pat}.",
                citation="Smart Money Concepts — Fair Value Gap fill")
    return None


# ---------------------------------------------------------------------------
# Bonus — Wyckoff Spring/Upthrust, Three Drives, Channel, Volume Profile test
# ---------------------------------------------------------------------------
def detect_wyckoff_spring(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Wyckoff Spring (bullish) or Upthrust (bearish). A false break of a
    well-established support/resistance — price wicks beyond it but reverses
    sharply within the same bar. Distinguishes a true break from a shakeout."""
    if df is None or df.empty or len(df) < 30:
        return None
    if not volume_confirmed(df, threshold=1.2):
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    lo_today = float(last["low"])
    hi_today = float(last["high"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("Wyckoff", 0.60)
    # Trading range = last 30 bars excluding today
    rng = df.iloc[-31:-1]
    rng_hi = float(rng["high"].max())
    rng_lo = float(rng["low"].min())
    body = abs(last["close"] - last["open"])
    full_range = hi_today - lo_today
    # Spring: wick below range_lo by ≥ 0.3 ATR, but close back inside, body in upper half
    if lo_today < rng_lo - 0.3 * atrv and px > rng_lo and px > (lo_today + 0.6 * full_range):
        stop = lo_today - 0.2 * atrv
        targets = smart_targets_long(df, px, stop)
        return Setup(symbol, "Wyckoff Spring (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=base + (0.10 if body > 0.3 * atrv else 0.0),
            reasoning=f"False break below range ${rng_lo:.2f}; reversed sharply within bar. Spring confirmed.",
            citation="Wyckoff — Spring (false break of support)")
    # Upthrust: wick above range_hi but close back inside, body in lower half
    if hi_today > rng_hi + 0.3 * atrv and px < rng_hi and px < (hi_today - 0.6 * full_range):
        stop = hi_today + 0.2 * atrv
        targets = smart_targets_short(df, px, stop)
        return Setup(symbol, "Wyckoff Upthrust (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=base - 0.05 + (0.10 if body > 0.3 * atrv else 0.0),
            reasoning=f"False break above range ${rng_hi:.2f}; reversed sharply within bar. Upthrust confirmed.",
            citation="Wyckoff — Upthrust (false break of resistance)")
    return None


def detect_three_drives(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Three-drives reversal — three consecutive swings in the same direction
    forming an exhaustion. Each drive should extend further than the prior."""
    if df is None or df.empty or len(df) < 60:
        return None
    pivots = swing_pivots(df.tail(80), n=5)
    if len(pivots) < 6:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("Three Drives", 0.52)
    # Look for 3 ascending highs (bear reversal) or 3 descending lows (bull reversal)
    highs = [p for p in pivots if p.kind == "high"]
    lows  = [p for p in pivots if p.kind == "low"]
    if len(highs) >= 3:
        h1, h2, h3 = highs[-3], highs[-2], highs[-1]
        if h2.price > h1.price and h3.price > h2.price and px < h3.price - 0.3 * atrv:
            stop = h3.price + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            return Setup(symbol, "Three Drives Top (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base,
                reasoning=f"Three ascending highs ${h1.price:.2f}→${h2.price:.2f}→${h3.price:.2f}; reversal candidate.",
                citation="Harmonic — Three Drives Top")
    if len(lows) >= 3:
        l1, l2, l3 = lows[-3], lows[-2], lows[-1]
        if l2.price < l1.price and l3.price < l2.price and px > l3.price + 0.3 * atrv:
            stop = l3.price - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            return Setup(symbol, "Three Drives Bottom (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base,
                reasoning=f"Three descending lows ${l1.price:.2f}→${l2.price:.2f}→${l3.price:.2f}; reversal candidate.",
                citation="Harmonic — Three Drives Bottom")
    return None


def detect_channel_break(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Linear regression channel — fits a least-squares line through the last
    N closes ± 2 standard deviations. Breakout above/below the channel band
    on volume = trend continuation or reversal trade."""
    if df is None or df.empty or len(df) < 30:
        return None
    if not volume_confirmed(df):
        return None
    window = df.tail(40)
    n = len(window)
    x = np.arange(n)
    y = window["close"].values
    # Least squares regression
    slope, intercept = np.polyfit(x, y, 1)
    residuals = y - (slope * x + intercept)
    std = float(np.std(residuals, ddof=1)) if n > 1 else 0
    if std == 0:
        return None
    last_pos = n - 1
    midline = slope * last_pos + intercept
    upper = midline + 2 * std
    lower = midline - 2 * std
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("Channel", 0.56)
    pat = bar_pattern(df)
    if px > upper and slope > 0:
        stop = midline - 0.3 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(symbol, "Channel Breakout (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.85, conv),
            reasoning=f"Break above rising channel upper band ${upper:.2f}. Bar: {pat}.",
            citation="Linear regression channel — upper breakout")
    if px < lower and slope < 0:
        stop = midline + 0.3 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if (is_bull_confirmation(pat) or is_bear_confirmation(pat)) else 0.0)
        return Setup(symbol, "Channel Breakdown (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.85, conv - 0.05),
            reasoning=f"Break below falling channel lower band ${lower:.2f}. Bar: {pat}.",
            citation="Linear regression channel — lower breakdown")
    return None


def detect_volume_profile_test(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Price tests the Point of Control (POC) or the edges of the Value Area.
    POC is the most-traded price — a strong magnet/reaction zone. VAH/VAL are
    the high/low boundaries of the area containing 70% of recent volume."""
    if df is None or df.empty or len(df) < 30:
        return None
    if not volume_confirmed(df):
        return None
    vp = compute_volume_profile(df, lookback_bars=60)
    if not vp or "poc" not in vp:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    lo = float(last["low"])
    hi = float(last["high"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("VolProfile", 0.54)
    pat = bar_pattern(df)
    poc, vah, val = vp.get("poc"), vp.get("vah"), vp.get("val")
    # Long: price tested VAL from above and held
    if val is not None and lo <= val and px > val and (px - val) <= 0.5 * atrv:
        stop = val - 0.4 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
        return Setup(symbol, f"Value Area Low test @ ${val:.2f} (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.82, conv),
            reasoning=f"Price reached VAL ${val:.2f} (low boundary of 70% value area) and held. POC ${poc:.2f}.",
            citation="Volume Profile — VAL test")
    # Short: price tested VAH from below and rejected
    if vah is not None and hi >= vah and px < vah and (vah - px) <= 0.5 * atrv:
        stop = vah + 0.4 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
        return Setup(symbol, f"Value Area High test @ ${vah:.2f} (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.82, conv - 0.05),
            reasoning=f"Price reached VAH ${vah:.2f} (high boundary of 70% value area) and rejected. POC ${poc:.2f}.",
            citation="Volume Profile — VAH test")
    return None


# ---------------------------------------------------------------------------
# Wave 8 — comprehensive coverage of everything still missing from the
# 499 pages: BB Squeeze, Gap, Climax, Measured Move, Camarilla pivots,
# 8 classic chart patterns, 7 harmonics + Wolfe, SMC extensions.
# ---------------------------------------------------------------------------
def detect_bb_squeeze(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """BB Squeeze — Bollinger Bands inside Keltner Channels = volatility
    compression about to release. Fires on the breakout bar (BB exits KC)."""
    if df is None or df.empty or len(df) < 25:
        return None
    bb = bollinger(df["close"], length=20, mult=2.0)
    kc = keltner_channel(df, length=20, mult=1.5)
    if bb.empty or kc.empty:
        return None
    if pd.isna(bb["upper"].iloc[-2]) or pd.isna(kc["upper"].iloc[-2]):
        return None
    # Squeeze condition on PREVIOUS bar: BB upper < KC upper AND BB lower > KC lower
    was_squeezed = (float(bb["upper"].iloc[-2]) < float(kc["upper"].iloc[-2]) and
                    float(bb["lower"].iloc[-2]) > float(kc["lower"].iloc[-2]))
    # Release on CURRENT bar: BB now outside KC
    now_released = (float(bb["upper"].iloc[-1]) >= float(kc["upper"].iloc[-1]) or
                    float(bb["lower"].iloc[-1]) <= float(kc["lower"].iloc[-1]))
    if not (was_squeezed and now_released):
        return None
    if not volume_confirmed(df):
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    base = BACKTESTED_CONVICTION.get("BB Squeeze", 0.60)
    pat = bar_pattern(df)
    # Direction = which side BB broke out of KC
    if float(bb["upper"].iloc[-1]) >= float(kc["upper"].iloc[-1]) and px > float(kc["upper"].iloc[-2]):
        stop = float(kc["lower"].iloc[-1]) - 0.3 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base + (0.10 if is_bull_confirmation(pat) else 0.0)
        return Setup(symbol, "BB Squeeze Release (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.88, conv),
            reasoning=f"BB was inside KC → volatility compressed; now releasing upward on volume. Bar: {pat}.",
            citation="Bollinger Band Squeeze — TTM (LBR) play")
    if float(bb["lower"].iloc[-1]) <= float(kc["lower"].iloc[-1]) and px < float(kc["lower"].iloc[-2]):
        stop = float(kc["upper"].iloc[-1]) + 0.3 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base + (0.10 if is_bear_confirmation(pat) else 0.0)
        return Setup(symbol, "BB Squeeze Release (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.88, conv - 0.05),
            reasoning=f"BB was inside KC → volatility compressed; now releasing downward on volume. Bar: {pat}.",
            citation="Bollinger Band Squeeze — TTM (LBR) play")
    return None


def detect_gap_play(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Gap classification — breakaway gap (after consolidation, starts a trend)
    fires as a continuation trade. Exhaustion gap (after a long trend) is a
    reversal. Gap fill = retracing into a recent gap as a trade entry."""
    if df is None or df.empty or len(df) < 30:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    p_h, p_l, p_c = float(prev["high"]), float(prev["low"]), float(prev["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if not volume_confirmed(df, threshold=1.2):
        return None
    base = BACKTESTED_CONVICTION.get("Gap", 0.56)
    pat = bar_pattern(df)
    gap_up = o > p_h
    gap_dn = o < p_l
    if not (gap_up or gap_dn):
        return None
    # Determine if prior is "consolidation" (last 10 bars tight range)
    last10 = df.iloc[-12:-2]
    rng = float(last10["high"].max() - last10["low"].min())
    consolidating = rng < 3 * atrv
    if gap_up and c > o and consolidating:
        stop = p_h - 0.3 * atrv
        targets = smart_targets_long(df, c, stop)
        return Setup(symbol, "Breakaway gap up (long)", "long",
            entry=c, stop_loss=stop, targets=targets,
            current_price=c, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
            reasoning=f"Gap-up open from prior high ${p_h:.2f}, closed strong after consolidation. Bar: {pat}.",
            citation="Breakaway gap — continuation entry")
    if gap_dn and c < o and consolidating:
        stop = p_l + 0.3 * atrv
        targets = smart_targets_short(df, c, stop)
        return Setup(symbol, "Breakaway gap down (short)", "short",
            entry=c, stop_loss=stop, targets=targets,
            current_price=c, conviction=base - 0.05 + (0.10 if is_bear_confirmation(pat) else 0.0),
            reasoning=f"Gap-down open from prior low ${p_l:.2f}, closed weak after consolidation. Bar: {pat}.",
            citation="Breakaway gap — breakdown entry")
    return None


def detect_climax_bar(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Buying/Selling Climax — exceptionally wide-range bar on very high volume
    that often marks exhaustion. Wide = 2× ATR. Volume = 2.5× avg. Reversal
    closes back into the bar or against the move direction."""
    if df is None or df.empty or len(df) < 30:
        return None
    last = df.iloc[-1]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    full = h - l
    atrv = float(atr(df, 14).iloc[-1])
    if atrv <= 0 or full < 2.0 * atrv:
        return None
    vol_avg = float(df["volume"].iloc[-22:-1].mean())
    if vol_avg <= 0 or float(last["volume"]) < 2.5 * vol_avg:
        return None
    base = BACKTESTED_CONVICTION.get("Climax", 0.54)
    # Buying climax — strong up bar that closes BELOW upper half (exhaustion)
    if c > o and (c - l) / full < 0.5:
        stop = h + 0.2 * atrv
        targets = smart_targets_short(df, c, stop)
        return Setup(symbol, "Buying Climax exhaustion (short)", "short",
            entry=c, stop_loss=stop, targets=targets,
            current_price=c, conviction=base,
            reasoning=f"Wide-range up bar ({full/atrv:.1f}× ATR) on huge volume but closed in lower half. Exhaustion signal.",
            citation="Wyckoff — Buying Climax")
    # Selling climax — strong down bar that closes ABOVE lower half
    if c < o and (h - c) / full < 0.5:
        stop = l - 0.2 * atrv
        targets = smart_targets_long(df, c, stop)
        return Setup(symbol, "Selling Climax exhaustion (long)", "long",
            entry=c, stop_loss=stop, targets=targets,
            current_price=c, conviction=base,
            reasoning=f"Wide-range down bar ({full/atrv:.1f}× ATR) on huge volume but closed in upper half. Exhaustion signal.",
            citation="Wyckoff — Selling Climax")
    return None


# Camarilla pivot levels — alternative method used by intraday traders
def compute_camarilla_pivots(df: pd.DataFrame) -> dict:
    """Camarilla Pivots — derived from previous day H/L/C using fixed multipliers.
    H3/L3 are mean-reversion levels; H4/L4 are breakout levels.
    Returns {h1..h4, l1..l4, prev_close}."""
    if df is None or df.empty or len(df) < 2:
        return {}
    prev = df.iloc[-2]
    h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
    rng = h - l
    return {
        "h4": c + rng * 1.1 / 2,    "h3": c + rng * 1.1 / 4,
        "h2": c + rng * 1.1 / 6,    "h1": c + rng * 1.1 / 12,
        "l1": c - rng * 1.1 / 12,   "l2": c - rng * 1.1 / 6,
        "l3": c - rng * 1.1 / 4,    "l4": c - rng * 1.1 / 2,
        "prev_close": c,
    }


# ============================================================================
# Classic chart patterns
# ============================================================================
def detect_double_top(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Two peaks at the same level with a valley between, then breakdown
    below the valley = Double Top (bear reversal)."""
    if df is None or df.empty or len(df) < 30:
        return None
    pivots = swing_pivots(df.tail(80), n=4)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if len(highs) < 2 or len(lows) < 1:
        return None
    h1, h2 = highs[-2], highs[-1]
    if abs(h1.price - h2.price) / max(h1.price, 1) > 0.03:
        return None
    valley = max([l for l in lows if h1.idx < l.idx < h2.idx], key=lambda p: -p.price, default=None)
    if valley is None:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if px > valley.price:
        return None
    if not volume_confirmed(df):
        return None
    stop = max(h1.price, h2.price) + 0.2 * atrv
    height = max(h1.price, h2.price) - valley.price
    targets = [valley.price - height, valley.price - 2 * height]
    return Setup(symbol, "Double Top breakdown (short)", "short",
        entry=px, stop_loss=stop, targets=targets,
        current_price=px, conviction=BACKTESTED_CONVICTION.get("Double Top", 0.62),
        reasoning=f"Two peaks at ~${h1.price:.2f}/${h2.price:.2f}, broke neckline ${valley.price:.2f}.",
        citation="Classic Double Top reversal")


def detect_double_bottom(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Mirror of double top — two lows at same level + breakout above peak."""
    if df is None or df.empty or len(df) < 30:
        return None
    pivots = swing_pivots(df.tail(80), n=4)
    lows = [p for p in pivots if p.kind == "low"]
    highs = [p for p in pivots if p.kind == "high"]
    if len(lows) < 2 or len(highs) < 1:
        return None
    l1, l2 = lows[-2], lows[-1]
    if abs(l1.price - l2.price) / max(l1.price, 1) > 0.03:
        return None
    peak = min([h for h in highs if l1.idx < h.idx < l2.idx], key=lambda p: -p.price, default=None)
    if peak is None:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if px < peak.price:
        return None
    if not volume_confirmed(df):
        return None
    stop = min(l1.price, l2.price) - 0.2 * atrv
    height = peak.price - min(l1.price, l2.price)
    targets = [peak.price + height, peak.price + 2 * height]
    return Setup(symbol, "Double Bottom breakout (long)", "long",
        entry=px, stop_loss=stop, targets=targets,
        current_price=px, conviction=BACKTESTED_CONVICTION.get("Double Bottom", 0.64),
        reasoning=f"Two lows at ~${l1.price:.2f}/${l2.price:.2f}, broke neckline ${peak.price:.2f}.",
        citation="Classic Double Bottom reversal")


def detect_head_and_shoulders(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """H&S: left shoulder lower than head, right shoulder ~equal to left, then
    break of neckline. Inverse H&S mirror for bullish reversal."""
    if df is None or df.empty or len(df) < 40:
        return None
    pivots = swing_pivots(df.tail(100), n=4)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if not volume_confirmed(df):
        return None
    # Classic H&S top — need 3 highs where middle is highest
    if len(highs) >= 3:
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        if head.price > ls.price and head.price > rs.price and \
           abs(ls.price - rs.price) / max(ls.price, 1) < 0.04:
            # Neckline from the two lows between shoulders
            between = [l for l in lows if ls.idx < l.idx < rs.idx]
            if len(between) >= 2:
                neck = (between[0].price + between[-1].price) / 2.0
                if px < neck - 0.1 * atrv:
                    height = head.price - neck
                    stop = rs.price + 0.2 * atrv
                    targets = [neck - height, neck - 1.5 * height]
                    return Setup(symbol, "Head & Shoulders breakdown (short)", "short",
                        entry=px, stop_loss=stop, targets=targets,
                        current_price=px, conviction=BACKTESTED_CONVICTION.get("Head & Shoulders", 0.66),
                        reasoning=f"L-shoulder ${ls.price:.2f}, head ${head.price:.2f}, R-shoulder ${rs.price:.2f}; broke neckline ${neck:.2f}.",
                        citation="Classic Head & Shoulders top")
    # Inverse H&S — 3 lows where middle is lowest
    if len(lows) >= 3:
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        if head.price < ls.price and head.price < rs.price and \
           abs(ls.price - rs.price) / max(ls.price, 1) < 0.04:
            between = [h for h in highs if ls.idx < h.idx < rs.idx]
            if len(between) >= 2:
                neck = (between[0].price + between[-1].price) / 2.0
                if px > neck + 0.1 * atrv:
                    height = neck - head.price
                    stop = rs.price - 0.2 * atrv
                    targets = [neck + height, neck + 1.5 * height]
                    return Setup(symbol, "Inverse Head & Shoulders breakout (long)", "long",
                        entry=px, stop_loss=stop, targets=targets,
                        current_price=px, conviction=BACKTESTED_CONVICTION.get("Inverse H&S", 0.66),
                        reasoning=f"L-shoulder ${ls.price:.2f}, head ${head.price:.2f}, R-shoulder ${rs.price:.2f}; broke neckline ${neck:.2f}.",
                        citation="Classic Inverse Head & Shoulders bottom")
    return None


def detect_triangle(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Triangle (asc/desc/sym) — two trendlines converging. Asc = flat top +
    rising bottom (bullish). Desc = falling top + flat bottom (bearish).
    Sym = both converging. Break of the apex with volume confirms direction."""
    if df is None or df.empty or len(df) < 30:
        return None
    tl_sup = fit_trendline(df, kind="support", lookback=50)
    tl_res = fit_trendline(df, kind="resistance", lookback=50)
    if tl_sup is None or tl_res is None:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    sup_v = tl_sup["value_at_last_bar"]
    res_v = tl_res["value_at_last_bar"]
    if res_v <= sup_v:
        return None
    if not volume_confirmed(df, threshold=1.2):
        return None
    pat = bar_pattern(df)
    base = BACKTESTED_CONVICTION.get("Triangle", 0.60)
    # Ascending triangle: support rising (positive slope), resistance flat
    if tl_sup["slope"] > 0 and abs(tl_res["slope"]) < tl_sup["slope"] * 0.3:
        if px > res_v + 0.1 * atrv:
            stop = sup_v - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            return Setup(symbol, "Ascending Triangle breakout (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
                reasoning=f"Asc triangle: rising support + flat resistance ${res_v:.2f}; broke out. Bar: {pat}.",
                citation="Classic Ascending Triangle")
    # Descending triangle: resistance falling, support flat
    if tl_res["slope"] < 0 and abs(tl_sup["slope"]) < abs(tl_res["slope"]) * 0.3:
        if px < sup_v - 0.1 * atrv:
            stop = res_v + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            return Setup(symbol, "Descending Triangle breakdown (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bear_confirmation(pat) else 0.0),
                reasoning=f"Desc triangle: falling resistance + flat support ${sup_v:.2f}; broke down. Bar: {pat}.",
                citation="Classic Descending Triangle")
    # Symmetrical triangle: both lines converging
    if tl_sup["slope"] > 0 and tl_res["slope"] < 0:
        if px > res_v + 0.1 * atrv:
            stop = sup_v - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            return Setup(symbol, "Symmetric Triangle breakout (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
                reasoning=f"Sym triangle converging; broke out above resistance line ${res_v:.2f}.",
                citation="Classic Symmetric Triangle")
        if px < sup_v - 0.1 * atrv:
            stop = res_v + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            return Setup(symbol, "Symmetric Triangle breakdown (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base - 0.05 + (0.10 if is_bear_confirmation(pat) else 0.0),
                reasoning=f"Sym triangle converging; broke down below support line ${sup_v:.2f}.",
                citation="Classic Symmetric Triangle")
    return None


def detect_wedge(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Wedge — two trendlines sloping in the SAME direction (both up or both
    down) but converging. Rising wedge in uptrend = bearish reversal. Falling
    wedge in downtrend = bullish reversal."""
    if df is None or df.empty or len(df) < 30:
        return None
    tl_sup = fit_trendline(df, kind="support", lookback=50)
    tl_res = fit_trendline(df, kind="resistance", lookback=50)
    if tl_sup is None or tl_res is None:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    sup_v = tl_sup["value_at_last_bar"]
    res_v = tl_res["value_at_last_bar"]
    if res_v <= sup_v:
        return None
    base = BACKTESTED_CONVICTION.get("Wedge", 0.58)
    pat = bar_pattern(df)
    if not volume_confirmed(df):
        return None
    # Rising wedge: BOTH lines slope up but support slopes faster (bearish setup)
    if tl_sup["slope"] > 0 and tl_res["slope"] > 0 and tl_sup["slope"] > tl_res["slope"]:
        if px < sup_v - 0.2 * atrv:
            stop = res_v + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            return Setup(symbol, "Rising Wedge breakdown (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bear_confirmation(pat) else 0.0),
                reasoning=f"Rising wedge: both lines up but converging; broke down at ${sup_v:.2f}. Bar: {pat}.",
                citation="Classic Rising Wedge reversal")
    # Falling wedge: BOTH lines slope down, resistance slopes faster (bullish)
    if tl_sup["slope"] < 0 and tl_res["slope"] < 0 and abs(tl_res["slope"]) > abs(tl_sup["slope"]):
        if px > res_v + 0.2 * atrv:
            stop = sup_v - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            return Setup(symbol, "Falling Wedge breakout (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
                reasoning=f"Falling wedge: both lines down but converging; broke up at ${res_v:.2f}. Bar: {pat}.",
                citation="Classic Falling Wedge reversal")
    return None


def detect_flag(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Bull/Bear Flag — a sharp impulsive move ('flagpole') followed by a
    tight pullback against the trend ('flag'), then breakout in the trend
    direction. CC favors flags after EMA pullbacks."""
    if df is None or df.empty or len(df) < 30:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if not volume_confirmed(df, threshold=1.2):
        return None
    base = BACKTESTED_CONVICTION.get("Flag", 0.62)
    pat = bar_pattern(df)
    # Define flagpole = the prior strong impulsive 5 bars
    pole = df.iloc[-15:-5]
    flag = df.iloc[-5:-1]
    pole_change = float(pole["close"].iloc[-1] - pole["close"].iloc[0])
    flag_range = float(flag["high"].max() - flag["low"].min())
    # Bull flag — pole goes UP strongly, flag is tight pullback, breakout up
    if pole_change > 3 * atrv and flag_range < 2 * atrv:
        flag_high = float(flag["high"].max())
        if px > flag_high + 0.1 * atrv:
            stop = float(flag["low"].min()) - 0.2 * atrv
            targets = [px + abs(pole_change) * 0.6, px + abs(pole_change)]
            return Setup(symbol, "Bull Flag breakout (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
                reasoning=f"Strong up flagpole ${pole_change:.2f}, tight flag, broke above ${flag_high:.2f}. Measured-move target.",
                citation="Classic Bull Flag continuation")
    # Bear flag — mirror
    if pole_change < -3 * atrv and flag_range < 2 * atrv:
        flag_low = float(flag["low"].min())
        if px < flag_low - 0.1 * atrv:
            stop = float(flag["high"].max()) + 0.2 * atrv
            targets = [px - abs(pole_change) * 0.6, px - abs(pole_change)]
            return Setup(symbol, "Bear Flag breakdown (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base - 0.05 + (0.10 if is_bear_confirmation(pat) else 0.0),
                reasoning=f"Strong down flagpole {pole_change:.2f}, tight flag, broke below ${flag_low:.2f}.",
                citation="Classic Bear Flag continuation")
    return None


def detect_cup_handle(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Cup and Handle — long rounded bottom (cup) followed by a brief
    pullback (handle), then breakout above the cup rim. Bullish continuation."""
    if df is None or df.empty or len(df) < 60:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if not volume_confirmed(df, threshold=1.3):
        return None
    base = BACKTESTED_CONVICTION.get("Cup Handle", 0.60)
    pat = bar_pattern(df)
    # Cup = 40 bars ago, current price close to that level after dip
    cup_start = float(df["close"].iloc[-50])
    cup_low = float(df["low"].iloc[-50:-10].min())
    cup_end = float(df["close"].iloc[-10])
    handle = df.iloc[-10:-1]
    handle_low = float(handle["low"].min())
    cup_rim = max(cup_start, cup_end)
    cup_depth = cup_rim - cup_low
    handle_depth = cup_rim - handle_low
    # Cup criteria: rounded (depth significant), rim equal both sides
    if cup_depth < 3 * atrv:
        return None
    if abs(cup_start - cup_end) / max(cup_rim, 1) > 0.05:
        return None
    # Handle should be shallow vs cup (< 50%)
    if handle_depth > 0.5 * cup_depth:
        return None
    # Breakout: current closes above cup rim
    if px > cup_rim + 0.1 * atrv:
        stop = handle_low - 0.2 * atrv
        targets = [cup_rim + cup_depth, cup_rim + 1.5 * cup_depth]
        return Setup(symbol, "Cup and Handle breakout (long)", "long",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
            reasoning=f"Rounded cup ${cup_low:.2f}–${cup_rim:.2f}, shallow handle ${handle_low:.2f}, broke rim. Bar: {pat}.",
            citation="Classic Cup and Handle (O'Neil)")
    return None


# ============================================================================
# Harmonic patterns — shared XABCD pivot helper + per-pattern Fib ratio matcher
# ============================================================================
def find_xabcd_pivots(df: pd.DataFrame, lookback: int = 150, n: int = 4):
    """Get the last 5 alternating swing pivots as (X, A, B, C, D)."""
    pivots = swing_pivots(df.tail(lookback), n=n)
    if len(pivots) < 5:
        return None
    last5 = pivots[-5:]
    kinds = [p.kind for p in last5]
    if not (kinds == ["low","high","low","high","low"]
            or kinds == ["high","low","high","low","high"]):
        return None
    return {"X": last5[0], "A": last5[1], "B": last5[2], "C": last5[3], "D": last5[4]}


def _check_xabcd(pts, ab_range, bc_range, cd_range, ad_range, ad_kind="xa"):
    """Verify the harmonic ratios are within tolerance.
    ab_range = AB/XA range, bc_range = BC/AB range,
    cd_range = CD/BC range, ad_range = AD/XA (or AD/XC if ad_kind='xc')."""
    X, A, B, C, D = (pts["X"].price, pts["A"].price, pts["B"].price,
                     pts["C"].price, pts["D"].price)
    xa = abs(A - X); ab = abs(B - A); bc = abs(C - B); cd = abs(D - C)
    if xa <= 0 or ab <= 0 or bc <= 0:
        return False
    ab_xa = ab / xa
    bc_ab = bc / ab
    cd_bc = cd / bc
    if ad_kind == "xa":
        ad_ratio = abs(D - A) / xa
    else:  # xc
        xc = abs(C - X)
        if xc <= 0:
            return False
        ad_ratio = abs(D - C) / xc
    return (ab_range[0] <= ab_xa <= ab_range[1]
            and bc_range[0] <= bc_ab <= bc_range[1]
            and cd_range[0] <= cd_bc <= cd_range[1]
            and ad_range[0] <= ad_ratio <= ad_range[1])


def _harmonic_setup(symbol, df, pts, name, citation, base_conv):
    """Build a Setup from the D point of a confirmed harmonic. Direction is
    determined by X kind: X=low → bullish, X=high → bearish."""
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    D = pts["D"]
    pat = bar_pattern(df)
    # Trade only if current price is near the D point (within 0.7 ATR)
    if abs(px - D.price) > 0.7 * atrv:
        return None
    direction = "long" if pts["X"].kind == "low" else "short"
    if direction == "long":
        stop = D.price - 0.5 * atrv
        targets = smart_targets_long(df, px, stop)
        conv = base_conv + (0.10 if is_bull_confirmation(pat) else 0.0)
    else:
        stop = D.price + 0.5 * atrv
        targets = smart_targets_short(df, px, stop)
        conv = base_conv - 0.05 + (0.10 if is_bear_confirmation(pat) else 0.0)
    return Setup(symbol, f"{name} ({direction})", direction,
        entry=px, stop_loss=stop, targets=targets,
        current_price=px, conviction=min(0.88, conv),
        reasoning=f"{name} pattern complete at D=${D.price:.2f}. Bar: {pat}.",
        citation=citation)


def detect_abcd(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """ABCD — simplest harmonic. AB=CD (equal legs), BC retracement 0.382-0.886."""
    if df is None or df.empty or len(df) < 50:
        return None
    pts = find_xabcd_pivots(df)
    if pts is None:
        return None
    # ABCD ratios — use only A/B/C/D (X ignored), AB ≈ CD
    A, B, C, D = pts["A"].price, pts["B"].price, pts["C"].price, pts["D"].price
    ab = abs(B - A); cd = abs(D - C)
    if ab <= 0 or abs(ab - cd) / ab > 0.15:    # AB and CD within 15%
        return None
    bc = abs(C - B)
    if not (0.38 <= bc / ab <= 0.886):
        return None
    return _harmonic_setup(symbol, df, pts, "ABCD harmonic",
        "Harmonic — ABCD equal-legs",
        BACKTESTED_CONVICTION.get("ABCD", 0.58))


def detect_gartley(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Gartley — AB=0.618 XA, BC=0.382-0.886 AB, CD=1.272-1.618 BC, D=0.786 XA."""
    if df is None or df.empty or len(df) < 50:
        return None
    pts = find_xabcd_pivots(df)
    if not pts: return None
    if not _check_xabcd(pts, (0.55, 0.68), (0.38, 0.886), (1.13, 1.62), (0.72, 0.84)):
        return None
    return _harmonic_setup(symbol, df, pts, "Gartley harmonic",
        "Harmonic — Gartley (Second 18 p.93)",
        BACKTESTED_CONVICTION.get("Gartley", 0.62))


def detect_bat(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Bat — AB=0.382-0.5 XA, BC=0.382-0.886 AB, CD=1.618-2.618 BC, D=0.886 XA."""
    if df is None or df.empty or len(df) < 50:
        return None
    pts = find_xabcd_pivots(df)
    if not pts: return None
    if not _check_xabcd(pts, (0.35, 0.55), (0.38, 0.886), (1.50, 2.70), (0.84, 0.93)):
        return None
    return _harmonic_setup(symbol, df, pts, "Bat harmonic",
        "Harmonic — Bat (Second 18 p.92)",
        BACKTESTED_CONVICTION.get("Bat", 0.62))


def detect_butterfly(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Butterfly — AB=0.786 XA, BC=0.382-0.886, CD=1.618-2.24, D=1.27-1.41 XA."""
    if df is None or df.empty or len(df) < 50:
        return None
    pts = find_xabcd_pivots(df)
    if not pts: return None
    if not _check_xabcd(pts, (0.74, 0.83), (0.38, 0.886), (1.55, 2.30), (1.22, 1.45)):
        return None
    return _harmonic_setup(symbol, df, pts, "Butterfly harmonic",
        "Harmonic — Butterfly (Second 18 p.90)",
        BACKTESTED_CONVICTION.get("Butterfly", 0.60))


def detect_crab(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Crab — AB=0.382-0.618 XA, BC=0.382-0.886, CD=2.24-3.618, D=1.618 XA."""
    if df is None or df.empty or len(df) < 50:
        return None
    pts = find_xabcd_pivots(df)
    if not pts: return None
    if not _check_xabcd(pts, (0.35, 0.65), (0.38, 0.886), (2.10, 3.70), (1.55, 1.68)):
        return None
    return _harmonic_setup(symbol, df, pts, "Crab harmonic",
        "Harmonic — Crab (Second 18 p.91)",
        BACKTESTED_CONVICTION.get("Crab", 0.58))


def detect_cypher(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Cypher — AB=0.382-0.618 XA, BC=1.13-1.414 AB (extends past A!),
    CD=0.786 XC, D=0.786 XC."""
    if df is None or df.empty or len(df) < 50:
        return None
    pts = find_xabcd_pivots(df)
    if not pts: return None
    if not _check_xabcd(pts, (0.35, 0.65), (1.10, 1.45), (0.70, 0.86), (0.70, 0.86),
                       ad_kind="xc"):
        return None
    return _harmonic_setup(symbol, df, pts, "Cypher harmonic",
        "Harmonic — Cypher (Second 18 p.94)",
        BACKTESTED_CONVICTION.get("Cypher", 0.60))


def detect_shark(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Shark — AB any (0.5-1.0), BC 1.13-1.618 (extends past A), CD 1.618-2.24,
    D inside the 0.886-1.13 zone of XC."""
    if df is None or df.empty or len(df) < 50:
        return None
    pts = find_xabcd_pivots(df)
    if not pts: return None
    if not _check_xabcd(pts, (0.45, 1.05), (1.10, 1.65), (1.55, 2.30), (0.85, 1.20),
                       ad_kind="xc"):
        return None
    return _harmonic_setup(symbol, df, pts, "Shark harmonic",
        "Harmonic — Shark (Second 18 p.95)",
        BACKTESTED_CONVICTION.get("Shark", 0.56))


def detect_wolfe_wave(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Wolfe Wave — 5-point pattern where points 1-3-5 form one trendline and
    points 2-4 form another. Entry at point 5; target = 1-4 line."""
    if df is None or df.empty or len(df) < 60:
        return None
    pivots = swing_pivots(df.tail(120), n=4)
    if len(pivots) < 5:
        return None
    last5 = pivots[-5:]
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    # We need P1=low P2=high P3=low P4=high P5=low (bullish) or mirror
    kinds = [p.kind for p in last5]
    bull = (kinds == ["low","high","low","high","low"])
    bear = (kinds == ["high","low","high","low","high"])
    if not (bull or bear):
        return None
    p1, p2, p3, p4, p5 = last5
    if bull:
        # Bull: 1-3-5 should be lower lows (extending support)
        if not (p3.price < p1.price and p5.price < p3.price):
            return None
        # 2-4 should be lower highs
        if not (p4.price < p2.price):
            return None
        # Entry at P5, target = 1-4 line projection
        target = max(p1.price, p4.price)
        stop = p5.price - 0.4 * atrv
        if abs(px - p5.price) > 0.7 * atrv:
            return None
        return Setup(symbol, "Wolfe Wave bottom (long)", "long",
            entry=px, stop_loss=stop, targets=[target, target + (target - px)],
            current_price=px,
            conviction=BACKTESTED_CONVICTION.get("Wolfe", 0.56),
            reasoning=f"5-point Wolfe Wave bottom: lower lows 1-3-5, lower highs 2-4; entry at P5 ${p5.price:.2f}.",
            citation="Wolfe Wave — 5-point reversal")
    else:
        if not (p3.price > p1.price and p5.price > p3.price):
            return None
        if not (p4.price > p2.price):
            return None
        target = min(p1.price, p4.price)
        stop = p5.price + 0.4 * atrv
        if abs(px - p5.price) > 0.7 * atrv:
            return None
        return Setup(symbol, "Wolfe Wave top (short)", "short",
            entry=px, stop_loss=stop, targets=[target, target - (px - target)],
            current_price=px,
            conviction=BACKTESTED_CONVICTION.get("Wolfe", 0.56) - 0.05,
            reasoning=f"5-point Wolfe Wave top: higher highs 1-3-5, higher lows 2-4; entry at P5 ${p5.price:.2f}.",
            citation="Wolfe Wave — 5-point reversal")


# ============================================================================
# SMC extensions — Breaker, Mitigation, Premium/Discount/Equilibrium, OTE
# ============================================================================
def detect_breaker_block(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Breaker block — an order block that was broken by the OPPOSITE direction
    impulse. When price returns to a broken bullish OB, the OB becomes
    resistance (now bearish). Mirror for bearish broken OB becoming support."""
    if df is None or df.empty or len(df) < 40:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    hi = float(last["high"])
    lo = float(last["low"])
    atrv = float(atr(df, 14).iloc[-1])
    obs = find_order_blocks(df, lookback=80)
    pat = bar_pattern(df)
    base = BACKTESTED_CONVICTION.get("Breaker", 0.58)
    # Bullish OB that's been BROKEN → now acts as resistance, SHORT setup
    bull_broken = [o for o in obs if o["kind"] == "bull" and o["broken"]]
    for ob in bull_broken:
        if ob["bot"] <= hi <= ob["top"] and px < ob["mid"]:
            stop = ob["top"] + 0.3 * atrv
            targets = smart_targets_short(df, px, stop)
            return Setup(symbol, f"Breaker block @ ${ob['mid']:.2f} (short)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base - 0.05 + (0.10 if is_bear_confirmation(pat) else 0.0),
                reasoning=f"Previously bullish OB ${ob['bot']:.2f}-${ob['top']:.2f} was broken; now acts as resistance.",
                citation="SMC — Breaker block (broken OB role-reversal)")
    # Bearish OB broken → support, LONG setup
    bear_broken = [o for o in obs if o["kind"] == "bear" and o["broken"]]
    for ob in bear_broken:
        if ob["bot"] <= lo <= ob["top"] and px > ob["mid"]:
            stop = ob["bot"] - 0.3 * atrv
            targets = smart_targets_long(df, px, stop)
            return Setup(symbol, f"Breaker block @ ${ob['mid']:.2f} (long)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
                reasoning=f"Previously bearish OB ${ob['bot']:.2f}-${ob['top']:.2f} was broken; now acts as support.",
                citation="SMC — Breaker block (broken OB role-reversal)")
    return None


def detect_premium_discount_ote(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """ICT Premium/Discount/Equilibrium + Optimal Trade Entry. Take the most
    recent leg (swing low → swing high), divide into thirds.
      Discount zone = lower third → look LONG
      Equilibrium = middle third → wait
      Premium zone = upper third → look SHORT
    OTE = the 0.618-0.79 fib retracement zone (a refinement of discount/premium)."""
    if df is None or df.empty or len(df) < 40:
        return None
    pivots = swing_pivots(df.tail(80), n=4)
    if len(pivots) < 2:
        return None
    last2 = pivots[-2:]
    if last2[0].kind == last2[1].kind:
        return None
    leg_low = min(last2[0].price, last2[1].price)
    leg_high = max(last2[0].price, last2[1].price)
    leg_range = leg_high - leg_low
    if leg_range <= 0:
        return None
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    if not volume_confirmed(df):
        return None
    pat = bar_pattern(df)
    base = BACKTESTED_CONVICTION.get("OTE", 0.62)
    # OTE zone = 0.618 to 0.79 retracement from the move direction
    ote_lo = leg_low + 0.59 * leg_range
    ote_hi = leg_low + 0.79 * leg_range
    # If the most recent leg was UP (low → high), and price has pulled BACK
    # into discount/OTE zone, look LONG
    bull_leg = last2[0].kind == "low" and last2[1].kind == "high"
    bear_leg = last2[0].kind == "high" and last2[1].kind == "low"
    if bull_leg:
        # We want a LONG entry if price has come down into the discount/OTE zone
        if ote_lo <= px <= ote_hi:
            stop = leg_low - 0.3 * atrv
            targets = [leg_high, leg_high + leg_range * 0.5]
            return Setup(symbol, f"OTE long @ ${px:.2f} (discount zone)", "long",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base + (0.10 if is_bull_confirmation(pat) else 0.0),
                reasoning=f"Price in OTE/discount zone (0.618-0.79 retrace of leg ${leg_low:.2f}→${leg_high:.2f}). Bar: {pat}.",
                citation="ICT — Optimal Trade Entry (premium/discount)")
    if bear_leg:
        # Bear leg high → low; for SHORT we want price retraced UP into OTE
        ote_lo = leg_high - 0.79 * leg_range
        ote_hi = leg_high - 0.59 * leg_range
        if ote_lo <= px <= ote_hi:
            stop = leg_high + 0.3 * atrv
            targets = [leg_low, leg_low - leg_range * 0.5]
            return Setup(symbol, f"OTE short @ ${px:.2f} (premium zone)", "short",
                entry=px, stop_loss=stop, targets=targets,
                current_price=px, conviction=base - 0.05 + (0.10 if is_bear_confirmation(pat) else 0.0),
                reasoning=f"Price in OTE/premium zone (0.618-0.79 retrace of leg ${leg_high:.2f}→${leg_low:.2f}).",
                citation="ICT — Optimal Trade Entry (premium/discount)")
    return None


DETECTORS = [
    # Wave 0 — original CC patterns
    detect_ema_pullback,
    detect_cc_region_pullback,
    detect_sr_flip,
    detect_volume_spike,
    detect_inside_day,
    detect_rsi_reversal,
    # Wave 2 — 3rd touch + trendline + ORB
    detect_third_touch,
    detect_trendline_break,
    detect_orb_breakout,
    # Wave 3 — Smart Money Concepts
    detect_bos,
    detect_choch,
    detect_liquidity_grab,
    detect_order_block_retest,
    detect_fvg_fill,
    # Bonus — Wyckoff, Three Drives, Channel, Volume Profile
    detect_wyckoff_spring,
    detect_three_drives,
    detect_channel_break,
    detect_volume_profile_test,
    # Wave 8 — comprehensive coverage
    detect_bb_squeeze,
    detect_gap_play,
    detect_climax_bar,
    detect_double_top,
    detect_double_bottom,
    detect_head_and_shoulders,
    detect_triangle,
    detect_wedge,
    detect_flag,
    detect_cup_handle,
    detect_abcd,
    detect_gartley,
    detect_bat,
    detect_butterfly,
    detect_crab,
    detect_cypher,
    detect_shark,
    detect_wolfe_wave,
    detect_breaker_block,
    detect_premium_discount_ote,
]


# ---------------------------------------------------------------------------
# Forming / "Watching" detectors — same CC patterns, but BEFORE they fire.
# When a setup is 1–5 trading days away from triggering, we want to surface
# it so the operator can prepare. Each returns a list[WatchItem] with the
# nearest level, the distance %, and what to wait for.
# ---------------------------------------------------------------------------
@dataclass
class WatchItem:
    symbol: str
    signal: str               # "EMA pullback forming", "3rd touch pending", etc.
    direction: str            # "long" / "short"
    level: float              # the price level to watch
    current_price: float
    distance_pct: float       # +ve = level above price, -ve = below
    waiting_for: str          # "close near $X.XX from above" — what triggers it
    citation: str
    bars_estimate: int = 0    # rough estimate of bars until trigger (heuristic)


def _distance_pct(level: float, current: float) -> float:
    if current <= 0:
        return 0.0
    return (level - current) / current * 100.0


def find_watches(symbol: str, df: Optional[pd.DataFrame]) -> List[WatchItem]:
    """For each scanned ticker, find CC setups that are CLOSE to firing.
    Returns a list of WatchItems — these are not triggers, they are alerts
    for setups forming in the next few bars."""
    if df is None or df.empty or len(df) < 60:
        return []
    out: list[WatchItem] = []
    close = df["close"]
    px = float(close.iloc[-1])
    a = atr(df, 14)
    if pd.isna(a.iloc[-1]):
        return []
    atrv = float(a.iloc[-1])

    # 1. EMA pullback forming — price is 1-3 ATRs from EMA55 in trending mkt
    if len(close) >= 220:
        e55, e100, e200 = ema(close, 55).iloc[-1], ema(close, 100).iloc[-1], ema(close, 200).iloc[-1]
        if not any(pd.isna(v) for v in (e55, e100, e200)):
            e55, e100, e200 = float(e55), float(e100), float(e200)
            # bull pullback forming
            if e55 > e100 > e200 and px > e55 and (px - e55) <= 3 * atrv and (px - e55) > 1 * atrv:
                gap_atrs = (px - e55) / atrv
                out.append(WatchItem(
                    symbol=symbol, signal="EMA 55 pullback forming (long)",
                    direction="long", level=e55, current_price=px,
                    distance_pct=_distance_pct(e55, px),
                    waiting_for=f"price to pull back to EMA55 ${e55:.2f} (currently {gap_atrs:.1f} ATR above)",
                    citation="First 18.pdf p.67",
                    bars_estimate=max(2, int(gap_atrs * 2)),
                ))
            # bear pullback forming
            if e55 < e100 < e200 and px < e55 and (e55 - px) <= 3 * atrv and (e55 - px) > 1 * atrv:
                gap_atrs = (e55 - px) / atrv
                out.append(WatchItem(
                    symbol=symbol, signal="EMA 55 pullback forming (short)",
                    direction="short", level=e55, current_price=px,
                    distance_pct=_distance_pct(e55, px),
                    waiting_for=f"price to rally up to EMA55 ${e55:.2f} (currently {gap_atrs:.1f} ATR below)",
                    citation="First 18.pdf p.67",
                    bars_estimate=max(2, int(gap_atrs * 2)),
                ))

    # 2. 3rd-touch pending — find a level with EXACTLY 2 prior touches within tol
    pivots = swing_pivots(df.tail(150), n=5)
    tol = max(atrv * 0.5, px * 0.005)  # 0.5 ATR or 0.5% — whichever bigger
    # Group pivot levels and count touches
    levels_to_touches: dict[float, int] = {}
    for p in pivots:
        matched = False
        for k in list(levels_to_touches.keys()):
            if abs(p.price - k) <= tol:
                levels_to_touches[k] = levels_to_touches[k] + 1
                matched = True
                break
        if not matched:
            levels_to_touches[p.price] = 1
    for level, n_touches in levels_to_touches.items():
        if n_touches == 2 and abs(px - level) <= 2 * atrv:
            direction = "long" if px > level else "short"
            out.append(WatchItem(
                symbol=symbol, signal=f"3rd touch pending @ ${level:.2f}",
                direction=direction, level=level, current_price=px,
                distance_pct=_distance_pct(level, px),
                waiting_for=f"price to retest ${level:.2f} ({n_touches} touches confirmed)",
                citation="First 18.pdf p.66 — 3rd touch is highest probability",
                bars_estimate=max(1, int(abs(px - level) / atrv * 2)),
            ))

    # 3. Range-tightening / Inside-Day approaching
    if len(df) >= 5:
        last5 = df.tail(5)
        ranges = (last5["high"] - last5["low"]).values
        if len(ranges) >= 3:
            recent_avg = float(np.mean(ranges[-3:]))
            prior_avg = float(np.mean(ranges[:-3])) if len(ranges) > 3 else recent_avg
            if recent_avg < 0.6 * atrv and prior_avg > recent_avg:
                hi5 = float(last5["high"].max())
                lo5 = float(last5["low"].min())
                out.append(WatchItem(
                    symbol=symbol, signal="Range tightening — breakout watch",
                    direction="long" if px > (hi5 + lo5) / 2 else "short",
                    level=hi5 if px > (hi5 + lo5) / 2 else lo5,
                    current_price=px,
                    distance_pct=_distance_pct(hi5 if px > (hi5+lo5)/2 else lo5, px),
                    waiting_for=f"break of 5-day range (${lo5:.2f}–${hi5:.2f})",
                    citation="Second 18.pdf p.4 — Inside Day / range contraction",
                    bars_estimate=3,
                ))

    # 4. S/R retest approaching (within 1 ATR but not yet touching)
    sr = support_resistance(df.tail(200))
    for level in sr["resistance"]:
        gap = level - px
        if 0 < gap <= 1.5 * atrv:
            out.append(WatchItem(
                symbol=symbol, signal=f"Resistance retest pending @ ${level:.2f}",
                direction="short", level=level, current_price=px,
                distance_pct=_distance_pct(level, px),
                waiting_for=f"rejection at ${level:.2f} or break-and-flip",
                citation="First 18.pdf p.61 — S/R role reversal",
                bars_estimate=max(1, int(gap / atrv * 2)),
            ))
    for level in sr["support"]:
        gap = px - level
        if 0 < gap <= 1.5 * atrv:
            out.append(WatchItem(
                symbol=symbol, signal=f"Support retest pending @ ${level:.2f}",
                direction="long", level=level, current_price=px,
                distance_pct=_distance_pct(level, px),
                waiting_for=f"hold at ${level:.2f} or break-down",
                citation="First 18.pdf p.61 — S/R role reversal",
                bars_estimate=max(1, int(gap / atrv * 2)),
            ))

    # De-dup close levels and limit to top 4 most urgent (smallest distance)
    out.sort(key=lambda w: abs(w.distance_pct))
    return out[:4]


# ---------------------------------------------------------------------------
# Context enrichment — 5 confluence checks
#   • HTF (Higher Timeframe — weekly trend)             — CC: First 18 + Second 18 HTF-vs-LTF cheatsheet
#   • Volume                                            — CC: Second 18 p.18 "Ranking by Volume"
#   • Market regime (SPY > 200-day)                     — general TA, not strictly CC
#   • Sector ETF health                                 — general TA, not strictly CC
#   • Earnings within 7 days                            — general TA, not strictly CC
# ---------------------------------------------------------------------------

# Map each CC-2026 ticker to its sector ETF. Tickers not here default to SPY.
SECTOR_ETF = {
    # Mega Cap Tech / Communication
    "GOOGL": "XLC", "META": "XLC", "DIS": "XLC", "ROKU": "XLC",
    "AVGO": "XLK", "KLAC": "XLK", "PANW": "XLK", "SNPS": "XLK",
    "ANET": "XLK", "VRT": "XLK", "CRM": "XLK", "GWRE": "XLK",
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK",
    # Healthcare
    "LLY": "XLV", "TMO": "XLV", "RVMD": "XLV", "GL": "XLV",
    "FOLD": "XLV", "XENE": "XLV",
    # Financials
    "V": "XLF", "C": "XLF", "SCHW": "XLF", "ALL": "XLF",
    "VLY": "XLF", "TRU": "XLF", "TRTX": "XLF",
    # Energy
    "XOM": "XLE", "SLB": "XLE", "DVN": "XLE",
    # Industrials
    "CAT": "XLI", "GEV": "XLI", "BA": "XLI", "CRH": "XLI",
    "DAN": "XLI", "CMC": "XLI", "MHK": "XLI", "VMI": "XLI",
    # Consumer Discretionary
    "CVNA": "XLY", "DKNG": "XLY", "WMB": "XLY", "RL": "XLY",
    "CELH": "XLY", "TSLA": "XLY",
    # Consumer Staples
    "CVS": "XLP", "SBUX": "XLP", "MKC": "XLP",
    # Real Estate
    "DLR": "XLRE", "CBRE": "XLRE",
    # Materials
    "ETR": "XLB",
    # Transport
    "CP": "XLI", "UAL": "XLI", "VIK": "XLI",
    # Auto
    "AZO": "XLY",
    # Crypto — sector = "crypto" pseudo-ETF (use BTC-USD as proxy)
    "BTC-USD": "BTC-USD", "ETH-USD": "BTC-USD", "SOL-USD": "BTC-USD",
}


def _trend_from_df(df: pd.DataFrame) -> str:
    """Return 'up', 'down', or 'side' from EMA50 slope."""
    if df is None or df.empty or len(df) < 60:
        return "side"
    e = ema(df["close"], 50)
    if pd.isna(e.iloc[-1]) or pd.isna(e.iloc[-5]):
        return "side"
    delta = (e.iloc[-1] - e.iloc[-5]) / e.iloc[-5]
    if delta > 0.005:
        return "up"
    if delta < -0.005:
        return "down"
    return "side"


def _earnings_days_away(symbol: str) -> Optional[int]:
    """Returns # of days until next earnings, or None if unknown/none."""
    try:
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return None
        # yfinance returns a dict with 'Earnings Date' as a list
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
        else:
            return None
        if not dates:
            return None
        next_date = dates[0]
        if hasattr(next_date, "date"):
            next_date = next_date.date()
        from datetime import date
        days = (next_date - date.today()).days
        return days if days >= 0 else None
    except Exception:
        return None


def build_context(daily_df: pd.DataFrame, symbol: str, setup_direction: str,
                  spy_trend: str, sector_trend: str,
                  weekly_df: Optional[pd.DataFrame] = None) -> list[ContextFlag]:
    """Compute the 5 context flags for one setup."""
    flags: list[ContextFlag] = []

    # 0) HTF — weekly trend (Second 18 HTF-vs-LTF cheatsheet)
    htf = _trend_from_df(weekly_df) if weekly_df is not None else "unknown"
    if htf == "unknown":
        flags.append(ContextFlag("HTF", "warn", "weekly data unavailable"))
    elif setup_direction == "long":
        if htf == "up":
            flags.append(ContextFlag("HTF", "ok", "weekly uptrend (with bias)"))
        elif htf == "down":
            flags.append(ContextFlag("HTF", "bad", "weekly DOWNTREND — long is counter-trend"))
        else:
            flags.append(ContextFlag("HTF", "warn", "weekly sideways"))
    else:  # short
        if htf == "down":
            flags.append(ContextFlag("HTF", "ok", "weekly downtrend (with bias)"))
        elif htf == "up":
            flags.append(ContextFlag("HTF", "bad", "weekly UPTREND — short is counter-trend"))
        else:
            flags.append(ContextFlag("HTF", "warn", "weekly sideways"))

    # 1) Volume on entry bar
    if daily_df is not None and len(daily_df) >= 21:
        last_vol = float(daily_df["volume"].iloc[-1])
        avg_vol = float(daily_df["volume"].iloc[-21:-1].mean())
        if avg_vol > 0:
            ratio = last_vol / avg_vol
            if ratio >= 1.2:
                flags.append(ContextFlag("Volume", "ok", f"{ratio:.1f}× 20-day avg (confirms)"))
            elif ratio >= 0.5:
                flags.append(ContextFlag("Volume", "warn", f"{ratio:.1f}× avg (neutral)"))
            else:
                flags.append(ContextFlag("Volume", "bad", f"{ratio:.1f}× avg (weak)"))

    # 2) Market regime
    if spy_trend == "up":
        flags.append(ContextFlag("Market", "ok", "SPY above 200-day (risk-on)"))
    elif spy_trend == "down":
        flags.append(ContextFlag("Market", "bad", "SPY below 200-day (risk-off)"))
    else:
        flags.append(ContextFlag("Market", "warn", "SPY sideways"))

    # 3) Sector
    etf = SECTOR_ETF.get(symbol, "SPY")
    if etf == "BTC-USD":
        if sector_trend == "up":
            flags.append(ContextFlag("Crypto regime", "ok", "BTC trend up"))
        elif sector_trend == "down":
            flags.append(ContextFlag("Crypto regime", "bad", "BTC trend down"))
        else:
            flags.append(ContextFlag("Crypto regime", "warn", "BTC sideways"))
    else:
        if sector_trend == "up":
            flags.append(ContextFlag("Sector", "ok", f"{etf} trending up"))
        elif sector_trend == "down":
            flags.append(ContextFlag("Sector", "bad", f"{etf} trending down"))
        else:
            flags.append(ContextFlag("Sector", "warn", f"{etf} sideways"))

    # 4) Earnings catalyst
    days = _earnings_days_away(symbol)
    if days is None:
        flags.append(ContextFlag("Earnings", "ok", "No earnings within 30 days"))
    elif days <= 7:
        flags.append(ContextFlag("Earnings", "bad", f"Earnings in {days} day(s) — high gap risk"))
    elif days <= 14:
        flags.append(ContextFlag("Earnings", "warn", f"Earnings in {days} days — caution"))
    else:
        flags.append(ContextFlag("Earnings", "ok", f"Earnings in {days} days (safe)"))

    return flags


# ---------------------------------------------------------------------------
# AI senior trader voice (Groq) — adds 2-3 sentence analysis per setup
# ---------------------------------------------------------------------------
CC_METHODOLOGY_BRIEF = """
=== CHART CHAMPIONS METHODOLOGY (your only source of truth) ===

You are NOT a general trader. You trade EXCLUSIVELY from the Chart Champions
methodology distilled below. Do not invent rules. Do not blend with generic TA
folklore. Every recommendation cites a specific CC concept.

--- I. CORE PHILOSOPHY ---
• The edge is DISCIPLINE + RISK MANAGEMENT, not prediction.
• Always know your stop BEFORE entering. No exceptions.
• Risk:reward minimum 1.5R, prefer 2R+. Below 1.5R = skip.
• Position size = 1% account risk per trade based on stop distance.
• HTF (higher-timeframe) determines bias. LTF determines entry trigger.
• Multiple confirmations beat single signals every time.

--- II. THE CC REGION (most important Fibonacci concept) ---
The CC Region is the 0.618–0.66 retracement zone of any swing.
This is the high-probability entry zone for trend continuation.
Source: First 18.pdf p.1, p.63.

  • In an uptrend, a pullback to the CC region of the prior up-leg is a buy.
  • In a downtrend, a rally to the CC region of the prior down-leg is a short.
  • Closing OUTSIDE the CC region in the wrong direction = setup invalidated.

Extensions for targets: 1.272 and 1.618 of the same leg.

--- III. THE EMA 55/100/200 STRATEGY ---
Source: First 18.pdf p.67.
  • Long ONLY when EMA55 > EMA100 > EMA200 AND price > EMA55.
  • Short ONLY when EMA55 < EMA100 < EMA200 AND price < EMA55.
  • Entry on pullback to EMA55 (within ~1 ATR of EMA55).
  • Stop below EMA200 OR below recent swing low (whichever is closer to entry).
  • Target 1 = recent swing high. Target 2 = 1.272 fib extension.

--- IV. MARKET STRUCTURE ---
Source: First 18.pdf p.37, Third batch.pdf p.30.
  • Uptrend = sequence of Higher Highs + Higher Lows. 3+ pivots confirm.
  • Downtrend = sequence of Lower Highs + Lower Lows. 3+ pivots confirm.
  • Range = no clear structure. Use horizontal channel tool.
  • Trend change = break of last HL (in uptrend) or last LH (in downtrend).

--- V. SUPPORT / RESISTANCE FLIP ---
Source: First 18.pdf p.61.
A broken resistance retested from above becomes support (entry long).
A broken support retested from below becomes resistance (entry short).
  • Entry on the retest with bullish/bearish rejection.
  • Stop just beyond the flipped level (~0.5 ATR).

--- VI. 3RD TOUCH SETUP ---
Source: Second 18.pdf p.45.
Wait for a level to be touched 3+ times before entering on the 3rd touch.
The third confirmation is where the trade is taken — earlier touches are noise.

--- VII. THREE DRIVES PATTERN ---
Source: First 18.pdf p.1–7.
  • Drive 1 = any size.
  • Point A = CC region retrace (0.618–0.66) of Drive 1.
  • Drive 2 = 1.272–1.618 extension of 1A.
  • Point B = CC region retrace of Drive 2.
  • Drive 3 = 1.272–1.618 of 2B.
  • Time symmetry: drives and corrections take equal time.
Two ways to trade: enter at B targeting 1.272–1.618, or enter on Drive 3 completion looking for reversal.

--- VIII. ORB (OPENING RANGE BREAKOUT) ---
Source: First 18.pdf p.31–35.
Mark the high & low of the first 30m of the session.
Entry on break with volume confirmation (volume > 1.3× session avg).
Stop = opposite side of opening range.

--- IX. INSIDE DAY ---
Source: First 18.pdf p.43.
Today's H/L is within yesterday's H/L. Tomorrow's break of the inside-day range sets direction.
Target = projected range above/below the breakout.

--- X. VOLUME ---
Source: Second 18.pdf p.18.
A new 20-bar high/low on >2× average volume is a high-quality breakout.
Volume below average on a breakout = suspect.

--- XI. WICKOFF (discretionary, secondary confirmation) ---
Source: First 18 p.79+.
Accumulation Phases A–E. Look for Spring (failed breakdown of support followed by reclaim) as a bullish reversal. Look for UTAD (failed breakout) as a bearish reversal. Discretionary — do not trade Wickoff alone.

--- XII. RISK MANAGEMENT (CC-specific) ---
• Hedge to protect, not to profit (Second 18 p.108). A hedge short at resistance protects a long spot position.
• 45° trendline stops (First 18 p.97) — internal trendlines connecting many lows/highs at ~45° are objective stops.
• If price closes against the CC region, the setup is invalidated immediately.

--- XIII. THE STRUCTURED EQUITY ANALYSIS MODEL (fundamental backdrop) ---
For longer-term positioning, score the underlying business across:
Business Quality, Financial Quality, Competitive Positioning, Growth Potential,
Risk Profile, Sentiment & Positioning, Valuation Outlook. (Each 1.0–5.0.)
Composite = average. Above 4.0 = high conviction. Below 3.0 = avoid.

=== END METHODOLOGY ===
"""

SENIOR_TRADER_SYSTEM = CC_METHODOLOGY_BRIEF + """

=== YOUR ROLE ===
You are a senior trader trained exclusively in the methodology above, with
decades of discretionary experience. You evaluate one trade at a time.

=== HARD RULES FOR YOUR OUTPUT ===
1. 3-4 short, decisive sentences. No hedging ("might", "could", "possibly").
2. Reference the EXACT CC rule that backs THIS setup. Cite the source page.
3. State the single biggest risk to this setup honestly.
4. If risk:reward < 1.5R: say "Skip — R:R too thin, wait for a better entry."
5. If risk:reward >= 2.0R AND a 2nd confirmation exists in the setup name
   (volume, EMA alignment, S/R flip, etc.), prefer "Take it."
6. If conviction < 60% AND only one confirmation: "Take it with reduced size."
7. Never invent prices. Use ONLY the entry/stop/targets given.
8. Never promise the trade wins. The edge is discipline + position sizing.
9. End with one explicit action verdict: TAKE IT / REDUCED SIZE / SKIP / AVOID.

=== VOICE ===
Blunt, professional, anti-FOMO. Like a senior CC trader reviewing a junior
trader's idea before clearing them to take the trade.
"""


def ai_enhance_setup(setup: Setup, api_key: str, model: str) -> str:
    """Call Groq to add senior-trader commentary. Returns "" on any failure."""
    import json
    import urllib.request

    flags_text = ""
    if setup.context_flags:
        flags_text = "\nCONTEXT (read these carefully):\n"
        for f in setup.context_flags:
            mark = {"ok": "[OK]", "warn": "[WARN]", "bad": "[BAD]"}[f.status]
            flags_text += f"  {mark} {f.label}: {f.detail}\n"

    user_prompt = f"""SETUP:
- Symbol: {setup.symbol}
- Pattern: {setup.name}
- Direction: {setup.direction.upper()}
- Current Price: ${setup.current_price:.2f}
- Entry: ${setup.entry:.2f}
- Stop: ${setup.stop_loss:.2f}
- Targets: {', '.join(f'${t:.2f}' for t in setup.targets)}
- Risk:Reward to T1: {setup.risk_reward:.2f}R
- Expected move to T1: {setup.move_pct:+.1f}%
- Conviction (detector): {int(setup.conviction*100)}%
- CC rule fired: {setup.reasoning}
- Source: {setup.citation}
{flags_text}
Use the context flags above in your analysis. If HTF is BAD, the setup is counter-trend and should usually be SKIPPED. If Earnings is BAD (within 7 days), warn about gap risk. If Volume is BAD, the setup lacks confirmation. Two or more BAD flags = AVOID.

Write your senior-trader review now."""

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SENIOR_TRADER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 180,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ("(Senior Trader voice is offline — the Groq API key on the server "
                    "is missing, invalid, or revoked. Set OPENAI_API_KEY in the Render "
                    "dashboard → Environment tab with a fresh key from console.groq.com.)")
        if e.code == 429:
            return "(Senior Trader voice paused — Groq daily quota hit. Resets at midnight UTC.)"
        return f"(AI commentary unavailable: HTTP {e.code})"
    except Exception as e:
        return f"(AI commentary unavailable: {type(e).__name__})"


# ---------------------------------------------------------------------------
# Structured Equity Analysis Model — the FUNDAMENTAL half of the CC methodology
# (per "Structured Equity Model Master Instructions.pdf"). This is a 9-step
# procedural review with category scoring. It complements the technical
# detectors above by adding the underlying-business view.
# ---------------------------------------------------------------------------
EQUITY_MODEL_SYSTEM = """You are an equity analyst applying the Structured
Equity Analysis Model — a procedural 9-step framework with strict scoring.

INPUT: a company ticker (e.g. AAPL, GOOGL).

EXECUTE all 9 steps in order. Do NOT skip, merge, reorder, or add steps.

STEP 1 — Snapshot & Business Overview (sector, products, revenue mix)
STEP 2 — Financial Quality, Balance Sheet & Valuation Metrics
STEP 3 — Competitive Positioning & Moat
STEP 4 — Bull Thesis & Growth Drivers
STEP 5 — Bear Thesis & Structural Risks
STEP 6 — Analyst Sentiment & Market Flow
STEP 7 — Scenario-Based Valuation & Return Framework
STEP 8 — Scorecard, Composite Rating & Final Assessment
STEP 9 — Investment Thesis & Invalidation Triggers

SCORING RUBRIC (1.0-5.0, decimals allowed):
  5.0 = Structurally dominant, durable advantages
  4.0 = Strong positioning with manageable risks
  3.0 = Balanced strengths and weaknesses
  2.0 = Structural vulnerabilities present
  1.0 = Structural fragility or capital impairment risk

7 CATEGORIES to score (each 1.0-5.0):
  - Business Quality
  - Financial Quality
  - Competitive Positioning
  - Growth Potential
  - Risk Profile
  - Sentiment & Positioning
  - Valuation Outlook

COMPOSITE RATING = average of the 7 category scores.

CONVICTION BANDS:
  4.5-5.0 → Very High Conviction
  4.0-4.49 → High Conviction
  3.5-3.99 → Moderate Conviction
  3.0-3.49 → Selective / Cautious
  Below 3.0 → Avoid / Monitor

OUTPUT FORMAT — return JSON ONLY (no prose around it):
{
  "ticker": "...",
  "snapshot": "1-2 sentence business summary",
  "bull_thesis": "1-2 sentence bull case",
  "bear_thesis": "1-2 sentence bear case",
  "scores": {
    "business_quality": 0.0,
    "financial_quality": 0.0,
    "competitive_positioning": 0.0,
    "growth_potential": 0.0,
    "risk_profile": 0.0,
    "sentiment_positioning": 0.0,
    "valuation_outlook": 0.0
  },
  "composite": 0.0,
  "conviction_band": "...",
  "stance": "Long / Hold / Avoid",
  "invalidation_triggers": ["...","..."]
}

Be concise and decisive. Do NOT hedge. The score is binding."""


def analyze_equity_model(symbol: str, api_key: str, model: str) -> Optional[dict]:
    """Call Groq with the Structured Equity Analysis Master Instructions
    prompt and return the parsed JSON output. Returns None on failure."""
    import json as _json
    import urllib.request

    if not api_key:
        return None

    body = _json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": EQUITY_MODEL_SYSTEM},
            {"role": "user",   "content": symbol.strip().upper()},
        ],
        "temperature": 0.2,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = _json.loads(resp.read())
        content = data["choices"][0]["message"]["content"].strip()
        result = _json.loads(content)
        return result
    except Exception:
        # Try without response_format (some Groq models reject it)
        try:
            body2 = _json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": EQUITY_MODEL_SYSTEM},
                    {"role": "user",   "content": symbol.strip().upper()},
                ],
                "temperature": 0.2,
                "max_tokens": 700,
            }).encode("utf-8")
            req2 = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=body2,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req2, timeout=25) as resp:
                data = _json.loads(resp.read())
            content = data["choices"][0]["message"]["content"].strip()
            # Find first { and last } to handle preamble
            i, j = content.find("{"), content.rfind("}")
            if i >= 0 and j > i:
                return _json.loads(content[i:j+1])
            return None
        except Exception:
            return None


def conviction_band_for(composite: float) -> str:
    """Map a composite score to the canonical conviction band string."""
    if composite >= 4.5:  return "Very High Conviction"
    if composite >= 4.0:  return "High Conviction"
    if composite >= 3.5:  return "Moderate Conviction"
    if composite >= 3.0:  return "Selective / Cautious"
    return "Avoid / Monitor"


# Cache the equity model results on disk so we don't re-fetch for the same
# ticker on every scan. Refresh per-ticker every ~24 hours.
_EQUITY_CACHE_FILE = Path(__file__).parent / "equity_model_cache.json"


def _load_equity_cache() -> dict:
    if not _EQUITY_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_EQUITY_CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_equity_cache(cache: dict) -> None:
    try:
        _EQUITY_CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str))
    except Exception:
        pass


def get_equity_analysis(symbol: str, api_key: str, model: str,
                        max_age_hours: int = 24) -> Optional[dict]:
    """Public entry. Returns cached analysis if fresh; otherwise calls AI and
    caches. The cache makes scans fast (no AI call per ticker per scan)."""
    sym = symbol.strip().upper()
    cache = _load_equity_cache()
    entry = cache.get(sym)
    now = datetime.utcnow()
    if entry:
        try:
            saved_at = datetime.fromisoformat(entry.get("_saved_at", ""))
            age = (now - saved_at).total_seconds() / 3600.0
            if age <= max_age_hours:
                return entry.get("data")
        except Exception:
            pass
    result = analyze_equity_model(sym, api_key, model)
    if result is None:
        return None
    cache[sym] = {"_saved_at": now.isoformat(), "data": result}
    _save_equity_cache(cache)
    return result


def _load_groq_config() -> tuple[str, str]:
    """Read Groq key + model. Priority:
      1. .env on disk (local dev — wins over shell env to dodge `source .env` staleness)
      2. Process env vars (production hosting — Render / Fly / ngrok)
    """
    api_key = ""
    model = ""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "OPENAI_API_KEY" and v and not api_key:
                api_key = v
            elif k == "OPENAI_MODEL_CHAT" and v and not model:
                model = v
    # In production (no .env), use process env vars from the hosting platform.
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
    if not model:
        model = os.environ.get("OPENAI_MODEL_CHAT", "llama-3.3-70b-versatile")
    return api_key, model


_VALID_TICKER = __import__("re").compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


def scan_one(symbol: str) -> tuple[Optional[pd.DataFrame], list[Setup], Optional[pd.DataFrame]]:
    """Returns (daily_df, setups, weekly_df). weekly_df is used for HTF check."""
    sym = symbol.strip().upper()
    if not _VALID_TICKER.match(sym):
        return None, [], None
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            # Pull 5 years of daily history — captures multi-year highs/lows
            # (e.g., CELH 2024 ATH ~$100 → $20 → recovery). CC traders work
            # off these multi-year levels as primary support/resistance.
            # Weekly is RESAMPLED from daily below — no separate fetch
            # (cuts yfinance load roughly in half, prevents Render OOM/timeout).
            df = yf.download(
                sym, period="5y", interval="1d",
                auto_adjust=True, progress=False, threads=False,
            )
            weekly = None
    except Exception:
        return None, [], None
    if df is None or df.empty:
        return None, [], None

    def _normalize(d):
        if d is None or d.empty:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
        else:
            d.columns = [c.lower() for c in d.columns]
        return d[["open", "high", "low", "close", "volume"]].dropna()

    df = _normalize(df)
    weekly_n = _normalize(weekly)
    # If weekly wasn't fetched separately (Wave 11 hotfix), resample from daily.
    # This produces the same HTF trend signal at zero extra network cost.
    if weekly_n is None and df is not None and not df.empty:
        try:
            weekly_n = resample_period(df, "W")
            if weekly_n is not None and weekly_n.empty:
                weekly_n = None
        except Exception:
            weekly_n = None

    out: list[Setup] = []
    for fn in DETECTORS:
        try:
            s = fn(symbol, df)
            if s is not None:
                out.append(s)
        except Exception:
            continue
    return df, out, weekly_n


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
def _compute_verdict(s: "Setup") -> tuple[str, str, str]:
    """Return (label, css_color, sort_rank). Lower rank = better.

    Rules (CC discipline + confluence):
      • STRONG TAKE — conviction ≥ 0.75, R:R ≥ 2.0, no BAD flags, HTF aligned.
      • TAKE        — conviction ≥ 0.65, R:R ≥ 1.5, ≤ 1 WARN, no BAD.
      • MARGINAL    — R:R ≥ 1.0 and only some WARN flags.
      • AVOID       — any BAD flag OR R:R < 1.0.
    """
    bad = [f for f in s.context_flags if f.status == "bad"]
    warn = [f for f in s.context_flags if f.status == "warn"]
    htf_bad = any(f.label == "HTF" and f.status == "bad" for f in s.context_flags)

    if bad or s.risk_reward < 1.0 or htf_bad:
        return ("AVOID", "#ef4444", 5)
    if s.conviction >= 0.75 and s.risk_reward >= 2.0 and len(warn) == 0:
        return ("STRONG TAKE", "#22c55e", 1)
    if s.conviction >= 0.65 and s.risk_reward >= 1.5 and len(warn) <= 1:
        return ("TAKE", "#86efac", 2)
    # Rank 3 reserved for 👁 WATCH (forming setups), see _watch_verdict()
    return ("MARGINAL", "#f59e0b", 4)


# ---------------------------------------------------------------------------
# Wave 18 — Forward-looking PLAN text.
#
# Every row in the main table gets a one-line "if-then" plan that references
# THE SPECIFIC CC concept the setup hinges on. For fired setups it reads:
#   "🎯 Long now @ $X → $T1 (R:R 2.1R). Stop $S. Cite: First 18.pdf p.67."
# For forming watches it reads:
#   "⏳ Wait for $X (EMA 55 pullback). Then long → ~$T, stop $S. Distance:
#    -2.5% (~3 days). Cite: First 18.pdf p.67."
#
# The "cite" comes straight from the detector's `citation` field, which we
# already populate from CC material (First 18.pdf, Second 18.pdf page refs).
# No silent shortcuts: if no CC concept maps to the level, we say so.
# ---------------------------------------------------------------------------
def _compute_plan_text(s: "Setup") -> str:
    """Wave 20 — Forward-looking conditional plan for a fired setup.
    Phrased as 'IF condition holds, ride to target. ABORT if breaks stop.'
    Never says 'do this RIGHT NOW' — Aaron's spec: every plan is contingent
    on a price-trigger condition, not on this instant."""
    if s is None:
        return ""
    arrow = "🎯"
    verb_long = s.direction == "long"
    verb = "long" if verb_long else "short"
    # Hold-side wording differs by direction.
    holds_phrase = ("holds above" if verb_long else "holds below")
    abort_phrase = ("breaks below" if verb_long else "breaks above")
    t1 = s.targets[0] if s.targets else s.entry
    rr = s.risk_reward
    move = s.move_pct
    return (f'{arrow} <b>IF {holds_phrase} ${s.entry:.2f}</b> → ride <b>{verb}</b> to '
            f'<span style="color:#22c55e">${t1:.2f}</span> ({rr:.1f}R, {move:+.1f}%). '
            f'<b>ABORT</b> if {abort_phrase} '
            f'<span style="color:#ef4444">${s.stop_loss:.2f}</span>. '
            f'<i style="color:#94a3b8">📖 {s.citation}</i>')


def _compute_watch_plan_text(w: "WatchItem") -> str:
    """One-line forward-looking plan for a FORMING watch — describes the
    trigger level + CC concept + expected distance + days estimate.
    Caller decides which `verdict` (👁 WATCH) accompanies this row."""
    if w is None:
        return ""
    verb = "long" if w.direction == "long" else "short"
    # Distance always shown as signed % so the operator knows direction
    dist = w.distance_pct
    bars = max(1, int(w.bars_estimate or 1))
    days_str = f"~{bars} day{'s' if bars != 1 else ''}"
    return (f'⏳ <b>Wait for ${w.level:.2f}</b> ({w.signal}). '
            f'Then <b>{verb}</b> on confirmation, stop just past the level. '
            f'Distance: <span style="color:#fbbf24">{dist:+.1f}%</span> '
            f'({days_str}). '
            f'<i style="color:#94a3b8">📖 {w.citation}</i>')


def _watch_verdict() -> tuple[str, str, int]:
    """Verdict tuple used for forming-watch rows. Ranked between TAKE and
    MARGINAL — a close-to-firing setup is often more actionable than a
    weak fired one."""
    return ("👁 WATCH", "#a78bfa", 3)


def _render_flags(flags: list[ContextFlag]) -> str:
    """Render the 5 confluence chips for a setup card."""
    if not flags:
        return ""
    chips = []
    for f in flags:
        color = {"ok": "#22c55e", "warn": "#f59e0b", "bad": "#ef4444"}[f.status]
        icon = {"ok": "✓", "warn": "⚠", "bad": "✗"}[f.status]
        chips.append(
            f'<div class="flag" style="border-left:3px solid {color}">'
            f'<span class="flag-l">{icon} {f.label}</span>'
            f'<span class="flag-d">{f.detail}</span>'
            f'</div>'
        )
    return f'<div class="flags">{"".join(chips)}</div>'


def _tv_symbol(yahoo_symbol: str) -> str:
    """Map a yfinance ticker to a TradingView-compatible symbol.

    For crypto (-USD), use COINBASE:XXXUSD which TradingView knows.
    For ALL other stocks/ETFs, pass the bare ticker — TradingView's widget
    auto-resolves to the correct exchange (NYSE, NASDAQ, ARCA, etc.).
    """
    s = yahoo_symbol.upper()
    if s.endswith("-USD"):
        return f"COINBASE:{s.replace('-', '')}"
    # Don't hardcode an exchange — TradingView's widget will auto-resolve.
    return s


# Common-name → yfinance-ticker resolver. Maps things people actually type.
TICKER_ALIASES = {
    # Crypto
    "BITCOIN": "BTC-USD", "BTC": "BTC-USD",
    "ETHEREUM": "ETH-USD", "ETHER": "ETH-USD", "ETH": "ETH-USD",
    "SOLANA": "SOL-USD", "SOL": "SOL-USD",
    "DOGECOIN": "DOGE-USD", "DOGE": "DOGE-USD",
    "RIPPLE": "XRP-USD", "XRP": "XRP-USD",
    "CARDANO": "ADA-USD", "ADA": "ADA-USD",
    "BNB": "BNB-USD", "BINANCE": "BNB-USD",
    "POLKADOT": "DOT-USD", "DOT": "DOT-USD",
    "LITECOIN": "LTC-USD", "LTC": "LTC-USD",
    "AVAX": "AVAX-USD", "AVALANCHE": "AVAX-USD",
    "MATIC": "MATIC-USD", "POLYGON": "MATIC-USD",
    "LINK": "LINK-USD", "CHAINLINK": "LINK-USD",
    # Common stock aliases
    "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
    "APPLE": "AAPL",
    "TESLA": "TSLA",
    "MICROSOFT": "MSFT",
    "NVIDIA": "NVDA",
    "AMAZON": "AMZN",
    "META": "META", "FACEBOOK": "META",
    "GOLD": "GLD", "ORO": "GLD",
    "SILVER": "SLV",
    "SPY": "SPY", "S&P": "SPY", "SP500": "SPY",
    "QQQ": "QQQ", "NASDAQ": "QQQ",
    "VIX": "^VIX",
}


def resolve_ticker(query: str) -> str:
    """Map free-form search input to a real yfinance ticker.
    - 'bitcoin' / 'btc' / 'BTC' → 'BTC-USD'
    - 'apple' → 'AAPL'
    - 'GLD' / 'gld' → 'GLD'  (already valid, just uppercased)
    """
    q = query.strip().upper()
    if not q:
        return q
    if q in TICKER_ALIASES:
        return TICKER_ALIASES[q]
    return q


def _snap_chart_body(snap, idx: int, chart_data_by_symbol: dict) -> str:
    """Lightweight Charts container for a snapshot card (no setup). Shows the
    full Wave-1 level overlay: S/R + Fibonacci ladder + Pivot Points + VWAP +
    round numbers. Same set as setup cards minus entry/stop/targets."""
    import json as _json
    sym = snap.symbol
    if sym not in chart_data_by_symbol:
        return '<div class="lwc-fallback">📉 Chart data unavailable — try refreshing.</div>'
    lines: list[dict] = []
    # Swing S/R clusters
    for sup in (snap.support_levels or [])[-3:]:
        lines.append({"price": sup, "color": "#22c55e88", "lineStyle": 2, "lineWidth": 1, "title": f"S ${sup:.2f}"})
    for res in (snap.resistance_levels or [])[-3:]:
        lines.append({"price": res, "color": "#ef444488", "lineStyle": 2, "lineWidth": 1, "title": f"R ${res:.2f}"})
    # Fibonacci ladder
    if snap.fib and snap.fib.get("retracements"):
        for pct, px in snap.fib["retracements"].items():
            is_cc = pct in ("0.618", "0.660")
            lines.append({
                "price": float(px),
                "color": "#fbbf24" if is_cc else "#fbbf2488",
                "lineStyle": 2,
                "lineWidth": 2 if is_cc else 1,
                "title": f"Fib {pct} ${float(px):.2f}",
            })
        for pct, px in (snap.fib.get("extensions") or {}).items():
            lines.append({
                "price": float(px), "color": "#f97316aa",
                "lineStyle": 2, "lineWidth": 1,
                "title": f"Fib ext {pct} ${float(px):.2f}",
            })
    # DAILY Pivot Points
    if snap.pivots:
        p = snap.pivots
        lines.append({"price": p["pp"], "color": "#fde047", "lineStyle": 2, "lineWidth": 1, "title": f"DAILY PP ${p['pp']:.2f}"})
        for key, label in [("r1","R1"),("r2","R2"),("s1","S1"),("s2","S2")]:
            if key in p:
                lines.append({"price": p[key], "color": "#fde04788", "lineStyle": 2, "lineWidth": 1, "title": f"DAILY {label} ${p[key]:.2f}"})
    # WEEKLY Pivot Points
    if getattr(snap, "pivots_weekly", None):
        p = snap.pivots_weekly
        lines.append({"price": p["pp"], "color": "#ec4899", "lineStyle": 2, "lineWidth": 2, "title": f"WEEKLY PP ${p['pp']:.2f}"})
        for key, label in [("r1","R1"),("r2","R2"),("s1","S1"),("s2","S2")]:
            if key in p:
                lines.append({"price": p[key], "color": "#ec489988", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY {label} ${p[key]:.2f}"})
    # MONTHLY Pivot Points
    if getattr(snap, "pivots_monthly", None):
        p = snap.pivots_monthly
        lines.append({"price": p["pp"], "color": "#a855f7", "lineStyle": 2, "lineWidth": 2, "title": f"MONTHLY PP ${p['pp']:.2f}"})
        for key, label in [("r1","R1"),("s1","S1")]:
            if key in p:
                lines.append({"price": p[key], "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY {label} ${p[key]:.2f}"})
    # Recent WEEKLY / MONTHLY highs and lows
    for w in (getattr(snap, "recent_weekly", []) or [])[-3:]:
        lines.append({"price": w["high"], "color": "#ec4899aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY high ${w['high']:.2f}"})
        lines.append({"price": w["low"],  "color": "#ec4899aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY low ${w['low']:.2f}"})
    for m in (getattr(snap, "recent_monthly", []) or [])[-3:]:
        lines.append({"price": m["high"], "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY high ${m['high']:.2f}"})
        lines.append({"price": m["low"],  "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY low ${m['low']:.2f}"})
    # WEEKLY Volume Profile
    vp_w = getattr(snap, "vp_weekly", None)
    if vp_w and "poc" in vp_w:
        lines.append({"price": vp_w["poc"], "color": "#f97316", "lineStyle": 0, "lineWidth": 2, "title": f"WEEKLY POC ${vp_w['poc']:.2f}"})
        if "vah" in vp_w:
            lines.append({"price": vp_w["vah"], "color": "#f97316aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY VAH ${vp_w['vah']:.2f}"})
        if "val" in vp_w:
            lines.append({"price": vp_w["val"], "color": "#f97316aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY VAL ${vp_w['val']:.2f}"})
    # MONTHLY Volume Profile
    vp_m = getattr(snap, "vp_monthly", None)
    if vp_m and "poc" in vp_m:
        lines.append({"price": vp_m["poc"], "color": "#dc2626", "lineStyle": 0, "lineWidth": 2, "title": f"MONTHLY POC ${vp_m['poc']:.2f}"})
        if "vah" in vp_m:
            lines.append({"price": vp_m["vah"], "color": "#dc2626aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY VAH ${vp_m['vah']:.2f}"})
        if "val" in vp_m:
            lines.append({"price": vp_m["val"], "color": "#dc2626aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY VAL ${vp_m['val']:.2f}"})
    # Naked POCs
    for n in (getattr(snap, "naked_pocs", []) or [])[:6]:
        lines.append({"price": float(n["poc"]), "color": "#06b6d4", "lineStyle": 2, "lineWidth": 1, "title": f"nPOC ${float(n['poc']):.2f}"})
    # Camarilla pivots (teal — intraday reference)
    cam = getattr(snap, "camarilla", None)
    if cam:
        for key, label in [("h4","H4"),("h3","H3"),("h2","H2"),("h1","H1"),
                           ("l1","L1"),("l2","L2"),("l3","L3"),("l4","L4")]:
            if key in cam:
                lines.append({"price": float(cam[key]), "color": "#14b8a688",
                              "lineStyle": 2, "lineWidth": 1,
                              "title": f"CAM {label} ${float(cam[key]):.2f}"})
    # VWAP
    if snap.vwap_anchored is not None:
        lines.append({
            "price": float(snap.vwap_anchored),
            "color": "#3b82f6", "lineStyle": 0, "lineWidth": 2,
            "title": f"VWAP ${float(snap.vwap_anchored):.2f}",
        })
    # Round numbers
    for rn in (snap.round_numbers or [])[:6]:
        lines.append({
            "price": float(rn), "color": "#94a3b822",
            "lineStyle": 2, "lineWidth": 1,
            "title": f"${float(rn):.0f}",
        })
    return (
        f'<div class="lwc-chart" id="lwc_snap_{idx}" data-symbol="{sym}" '
        f"data-lines='{_json.dumps(lines)}'></div>"
    )


_AI_OFFLINE_BLOCK = (
    '<div class="ai-voice ai-offline">'
    '<div class="ai-head">🎯 Senior Trader Read</div>'
    "<i>(no commentary — either there's no Groq key on the server, or the key was revoked. "
    "Set <code>OPENAI_API_KEY</code> in your Render dashboard → Environment to enable this.)</i>"
    "</div>"
)


def _render_equity_panel(eq: Optional[dict]) -> str:
    """Render the Structured Equity Analysis Model result as a card panel.
    Shows the 7 category scores, composite + conviction band, stance, and
    invalidation triggers. Returns empty string if eq is None."""
    if not eq:
        return ""
    scores = eq.get("scores") or {}
    composite = eq.get("composite") or (
        sum(scores.values()) / len(scores) if scores else 0.0
    )
    try:
        composite = float(composite)
    except Exception:
        composite = 0.0
    band = eq.get("conviction_band") or conviction_band_for(composite)
    stance = eq.get("stance") or "—"
    band_color = "#22c55e" if composite >= 4.0 else (
                 "#86efac" if composite >= 3.5 else (
                 "#f59e0b" if composite >= 3.0 else "#ef4444"))
    invalidation = eq.get("invalidation_triggers") or []
    bull = eq.get("bull_thesis", "")
    bear = eq.get("bear_thesis", "")
    snap_blurb = eq.get("snapshot", "")

    cat_rows = []
    label_map = {
        "business_quality":      "Business Quality",
        "financial_quality":     "Financial Quality",
        "competitive_positioning":"Competitive Position",
        "growth_potential":      "Growth Potential",
        "risk_profile":          "Risk Profile",
        "sentiment_positioning": "Sentiment & Positioning",
        "valuation_outlook":     "Valuation Outlook",
    }
    for key, label in label_map.items():
        v = scores.get(key)
        if v is None:
            continue
        try:
            v = float(v)
        except Exception:
            continue
        score_col = "#22c55e" if v >= 4.0 else ("#86efac" if v >= 3.5 else (
                    "#f59e0b" if v >= 3.0 else "#ef4444"))
        # 5-dot visual scale
        dots = ""
        for i in range(1, 6):
            filled = i <= round(v)
            dots += (
                f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
                f'background:{score_col if filled else "#1e293b"};margin-right:2px"></span>'
            )
        cat_rows.append(
            f'<div class="eq-row"><span class="eq-label">{label}</span>'
            f'<span class="eq-score">{dots}<span class="eq-val" style="color:{score_col}">'
            f'{v:.1f}</span></span></div>'
        )

    inval_html = ""
    if invalidation:
        items = "".join(f"<li>{x}</li>" for x in invalidation[:4])
        inval_html = f'<div class="eq-inval"><b>Invalidation triggers:</b><ul>{items}</ul></div>'

    return f'''
    <div class="equity-panel">
      <div class="eq-head">
        <span class="eq-title">📊 Structured Equity Analysis</span>
        <span class="eq-band" style="background:{band_color};color:#000">
          {composite:.2f} · {band}
        </span>
      </div>
      <div class="eq-snap">{snap_blurb}</div>
      <div class="eq-grid">{"".join(cat_rows)}</div>
      <div class="eq-stance"><b>Stance:</b> <span style="color:{band_color}">{stance}</span></div>
      <div class="eq-thesis">
        <div><b style="color:#22c55e">Bull:</b> {bull}</div>
        <div><b style="color:#ef4444">Bear:</b> {bear}</div>
      </div>
      {inval_html}
    </div>
    '''


def _ai_voice_block(ai_text: str) -> str:
    if not ai_text:
        return _AI_OFFLINE_BLOCK
    return (
        '<div class="ai-voice">'
        '<div class="ai-head">🎯 Senior Trader Read</div>'
        f"{ai_text}"
        "</div>"
    )


def _render_key_levels_panel(snap: "Snapshot") -> str:
    """A consistent 'Key Levels' panel: price + EMAs + S/R + Fib + pivots
    + Camarilla + VWAP + multi-TF, with EVERY label and value color-coded to
    match the corresponding line on the chart. Used on every setup card AND
    every snapshot card."""
    if snap is None:
        return ""
    px = snap.current_price
    def _row(label: str, value: Optional[float], color: str = "#e2e8f0") -> str:
        """Both label and value share the given color (so the eye finds the
        right level instantly — they match the line color on the chart)."""
        if value is None:
            return f'<div><span class="lbl" style="color:{color}">{label}</span><span class="val">—</span></div>'
        dist_pct = ((value - px) / px * 100.0) if px else 0.0
        arrow = "↑" if dist_pct > 0 else ("↓" if dist_pct < 0 else "•")
        sign = "+" if dist_pct > 0 else ""
        return (
            f'<div><span class="lbl" style="color:{color}">{label}</span>'
            f'<span class="val" style="color:{color}">${value:.2f} '
            f'<span class="lvl-dist">({arrow} {sign}{dist_pct:.1f}%)</span></span></div>'
        )
    rows = []
    rows.append(f'<div><span class="lbl" style="color:#fbbf24"><b>Current</b></span><span class="val" style="color:#fbbf24"><b>${px:.2f}</b></span></div>')
    # Bid / ask / spread
    if getattr(snap, "bid", None) is not None and getattr(snap, "ask", None) is not None:
        spread_str = f" ({snap.spread_pct:.2f}%)" if snap.spread_pct else ""
        spread_color = "#22c55e" if snap.spread_pct and snap.spread_pct < 0.10 else (
                       "#f59e0b" if snap.spread_pct and snap.spread_pct < 0.50 else "#ef4444")
        rows.append(
            f'<div><span class="lbl" style="color:{spread_color}">Bid/Ask</span>'
            f'<span class="val" style="color:{spread_color}">${snap.bid:.2f} / ${snap.ask:.2f}'
            f'<span class="lvl-dist">{spread_str}</span></span></div>'
        )
    if getattr(snap, "avg_volume", None):
        v = snap.avg_volume
        if v >= 1_000_000:  v_str = f"{v/1_000_000:.1f}M"
        elif v >= 1_000:    v_str = f"{v/1_000:.0f}K"
        else:               v_str = f"{v:.0f}"
        liq_color = "#22c55e" if v >= 1_000_000 else ("#f59e0b" if v >= 100_000 else "#ef4444")
        rows.append(f'<div><span class="lbl" style="color:{liq_color}">Avg vol (20d)</span><span class="val" style="color:{liq_color}">{v_str}</span></div>')
    # EMAs — colors match the chart line colors exactly
    if getattr(snap, "ema_55", None) is not None or hasattr(snap, "ema_55"):
        # EMA 8 / 21 if exposed elsewhere (we don't have them on the Snapshot
        # but they show up in the chart legend — the Key Levels panel always
        # shows 55/100/200 which are the CC-canonical ones)
        rows.append(_row("EMA 55",  snap.ema_55,  "#94a3b8"))
        rows.append(_row("EMA 100", snap.ema_100, "#cbd5e1"))
        rows.append(_row("EMA 200", snap.ema_200, "#64748b"))
    if snap.rsi_14 is not None:
        rsi_color = "#ef4444" if snap.rsi_14 > 70 else ("#22c55e" if snap.rsi_14 < 30 else "#94a3b8")
        rows.append(f'<div><span class="lbl" style="color:{rsi_color}">RSI 14</span><span class="val" style="color:{rsi_color}">{snap.rsi_14:.1f}</span></div>')
    # Support (green — matches chart) / Resistance (red)
    for sup in (snap.support_levels or [])[-3:]:
        rows.append(_row("Support", sup, "#22c55e"))
    for res in (snap.resistance_levels or [])[-3:]:
        rows.append(_row("Resistance", res, "#ef4444"))
    # Fibonacci ladder (yellow for retracements, orange for extensions)
    fib_data = getattr(snap, "fib", None)
    if fib_data and fib_data.get("retracements"):
        retr = fib_data["retracements"]
        exts = fib_data.get("extensions") or {}
        all_fibs = [(pct, float(v), "retr") for pct, v in retr.items()] \
                 + [(pct, float(v), "ext")  for pct, v in exts.items()]
        below = [t for t in all_fibs if t[1] < px]
        above = [t for t in all_fibs if t[1] > px]
        below.sort(key=lambda x: x[1], reverse=True)
        above.sort(key=lambda x: x[1])
        for pct, v, kind in below[:2]:
            color = "#f97316" if kind == "ext" else "#fbbf24"
            rows.append(_row(f"Fib {pct} (support)", v, color))
        for pct, v, kind in above[:2]:
            color = "#f97316" if kind == "ext" else "#fbbf24"
            rows.append(_row(f"Fib {pct} (resist)", v, color))
    # Anchored VWAP (blue — matches chart)
    vwap = getattr(snap, "vwap_anchored", None)
    if vwap is not None:
        rows.append(_row("VWAP (anchored)", float(vwap), "#3b82f6"))
    # DAILY Pivot Points (yellow tones)
    pivots = getattr(snap, "pivots", None)
    if pivots:
        rows.append(_row("DAILY PP",     pivots.get("pp"), "#fde047"))
        rows.append(_row("DAILY R1",     pivots.get("r1"), "#fde047"))
        rows.append(_row("DAILY S1",     pivots.get("s1"), "#fde047"))
    # WEEKLY Pivot Points (pink)
    piv_w = getattr(snap, "pivots_weekly", None)
    if piv_w:
        rows.append(_row("WEEKLY PP",    piv_w.get("pp"), "#ec4899"))
        rows.append(_row("WEEKLY R1",    piv_w.get("r1"), "#ec4899"))
        rows.append(_row("WEEKLY S1",    piv_w.get("s1"), "#ec4899"))
    # MONTHLY Pivot Points (purple)
    piv_m = getattr(snap, "pivots_monthly", None)
    if piv_m:
        rows.append(_row("MONTHLY PP",   piv_m.get("pp"), "#a855f7"))
        rows.append(_row("MONTHLY R1",   piv_m.get("r1"), "#a855f7"))
        rows.append(_row("MONTHLY S1",   piv_m.get("s1"), "#a855f7"))
    # Volume Profile — WEEKLY (orange) / MONTHLY (red)
    vp_w = getattr(snap, "vp_weekly", None)
    if vp_w and "poc" in vp_w:
        rows.append(_row("WEEKLY POC",   vp_w.get("poc"), "#f97316"))
        rows.append(_row("WEEKLY VAH",   vp_w.get("vah"), "#f97316"))
        rows.append(_row("WEEKLY VAL",   vp_w.get("val"), "#f97316"))
    vp_m = getattr(snap, "vp_monthly", None)
    if vp_m and "poc" in vp_m:
        rows.append(_row("MONTHLY POC",  vp_m.get("poc"), "#dc2626"))
        rows.append(_row("MONTHLY VAH",  vp_m.get("vah"), "#dc2626"))
        rows.append(_row("MONTHLY VAL",  vp_m.get("val"), "#dc2626"))
    # Naked POCs (cyan)
    for n in (getattr(snap, "naked_pocs", None) or [])[:3]:
        rows.append(_row("nPOC", float(n["poc"]), "#06b6d4"))
    # Camarilla pivots (teal)
    cam = getattr(snap, "camarilla", None)
    if cam:
        for key, label in [("h4","CAM H4"),("h3","CAM H3"),("h2","CAM H2"),("h1","CAM H1"),
                           ("l1","CAM L1"),("l2","CAM L2"),("l3","CAM L3"),("l4","CAM L4")]:
            if key in cam:
                rows.append(_row(label, float(cam[key]), "#14b8a6"))
    return (
        '<div class="key-levels"><div class="kl-head">📐 Key Levels (with distance from current)</div>'
        f'<div class="setup-grid">{"".join(rows)}</div></div>'
    )


def render_html(
    setups: list[Setup],
    scanned: int,
    duration_s: float,
    snapshots: Optional[list["Snapshot"]] = None,
    levels_by_symbol: Optional[dict] = None,
    watches: Optional[list] = None,
    chart_data_by_symbol: Optional[dict] = None,
    market_regime: Optional[dict] = None,
    macro_event: Optional[tuple] = None,
    sector_counts: Optional[dict] = None,
) -> str:
    snapshots = snapshots or []
    levels_by_symbol = levels_by_symbol or {}
    watches = watches or []
    chart_data_by_symbol = chart_data_by_symbol or {}
    market_regime = market_regime or {}
    sector_counts = sector_counts or {}
    # Sort: STRONG TAKE first, then TAKE, MARGINAL, AVOID. Within each, conviction × R:R desc.
    setups_sorted = sorted(
        setups,
        key=lambda s: (_compute_verdict(s)[2], -s.conviction, -s.risk_reward),
    )

    verdict_counts: dict[str, int] = {}
    for s in setups_sorted:
        v = _compute_verdict(s)[0]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    # --- Summary table (Wave 18: PLAN column + forming-watch rows;
    # Wave 19: GROUP BY SYMBOL — if a ticker has 3 setups firing we show
    # ONE row with the strongest as primary + the other options listed inline
    # inside the PLAN cell. No more 3 separate rows for the same ticker.)
    setups_by_symbol: dict[str, list] = {}
    sym_order: list[str] = []
    for s in setups_sorted:
        if s.symbol not in setups_by_symbol:
            setups_by_symbol[s.symbol] = []
            sym_order.append(s.symbol)
        setups_by_symbol[s.symbol].append(s)

    rows = []
    for sym in sym_order:
        ticker_setups = setups_by_symbol[sym]
        s = ticker_setups[0]  # already sorted: best one first (verdict, conv × R:R)
        alts = ticker_setups[1:]  # everything else for this ticker
        verdict, vcolor, _ = _compute_verdict(s)
        long = s.direction == "long"
        tone = "#22c55e" if long else "#ef4444"
        arrow = "▲" if long else "▼"
        targets_html = " · ".join(f"${t:.2f}" for t in s.targets)
        plan_html = _compute_plan_text(s)
        # Wave 19 — additional options inside the same row
        if alts:
            alt_lines = []
            for a in alts:
                a_verdict, a_color, _ = _compute_verdict(a)
                alt_lines.append(
                    f'<div style="margin-top:6px;padding-left:8px;border-left:2px solid {a_color}">'
                    f'<span style="color:{a_color};font-weight:600;font-size:10px">{a_verdict}</span> · '
                    f'{("▲" if a.direction == "long" else "▼")} {a.name} · '
                    f'{_compute_plan_text(a)}'
                    f'</div>'
                )
            plan_html += (
                f'<details style="margin-top:8px;cursor:pointer">'
                f'<summary style="color:#a78bfa;font-size:10px;font-weight:700">'
                f'+ {len(alts)} more option{"s" if len(alts) > 1 else ""} on this ticker</summary>'
                + "".join(alt_lines)
                + '</details>'
            )
        # Aggregated setup-name cell for the SETUP column when there are alts
        setup_label = f"{arrow} {s.name}"
        if alts:
            setup_label += f' <span style="color:#a78bfa;font-size:10px;font-weight:700">+{len(alts)}</span>'
        rows.append(f"""
          <tr class="setup-row row-{verdict.lower().replace(' ', '-')} dir-{s.direction}"
              data-symbol="{s.symbol}" data-verdict="{verdict.lower().replace(' ', '-')}" data-direction="{s.direction}">
            <td class="actions">
              <button class="bell-btn" data-symbol="{s.symbol}" data-price="{s.current_price:.2f}" onclick="setAlarm(event,'{s.symbol}',{s.current_price:.2f})" title="Price alarm">🔔</button>
            </td>
            <td><span class="verdict-pill" style="background:{vcolor};color:#000">{verdict}</span></td>
            <td><b><a class="sym-link" href="/chart?symbol={s.symbol}" target="_blank" rel="noopener" title="Open full chart in new tab">{s.symbol}</a></b></td>
            <td><a class="open-chart-mini" href="/chart?symbol={s.symbol}" target="_blank" rel="noopener" title="Open full chart in new tab">📊 Open</a></td>
            <td style="color:{tone}">{setup_label}</td>
            <td style="text-align:right">${s.current_price:.2f}</td>
            <td style="text-align:right">${s.entry:.2f}</td>
            <td style="text-align:right;color:#ef4444">${s.stop_loss:.2f}</td>
            <td style="text-align:right;color:#22c55e">{targets_html}</td>
            <td style="text-align:right">{s.risk_reward:.2f}R<br><span style="color:#94a3b8">{s.move_pct:+.1f}%</span></td>
            <td style="text-align:right">{int(s.conviction*100)}%</td>
            <td style="font-size:11px;color:#cbd5e1;line-height:1.45">{plan_html}</td>
          </tr>
        """)

    # Wave 18 — Forming watches as additional rows (sorted by abs distance).
    # These appear BETWEEN fired and AVOID rows (verdict rank 3) so the
    # operator sees "next to fire" right next to "fired now".
    w_verdict_label, w_verdict_color, _ = _watch_verdict()
    watches_sorted = sorted(watches or [], key=lambda w: abs(w.distance_pct))
    for w in watches_sorted[:8]:  # cap so the table doesn't explode
        dir_arrow = "▲" if w.direction == "long" else "▼"
        dir_color = "#22c55e" if w.direction == "long" else "#ef4444"
        w_plan = _compute_watch_plan_text(w)
        rows.append(f"""
          <tr class="setup-row row-watch dir-{w.direction}"
              data-symbol="{w.symbol}" data-verdict="watch" data-direction="{w.direction}">
            <td class="actions">
              <button class="bell-btn" data-symbol="{w.symbol}" data-price="{w.current_price:.2f}" onclick="setAlarm(event,'{w.symbol}',{w.current_price:.2f})" title="Alarm me when this triggers">🔔</button>
            </td>
            <td><span class="verdict-pill" style="background:{w_verdict_color};color:#000">{w_verdict_label}</span></td>
            <td><b><a class="sym-link" href="/chart?symbol={w.symbol}" target="_blank" rel="noopener" title="Open full chart in new tab">{w.symbol}</a></b></td>
            <td><a class="open-chart-mini" href="/chart?symbol={w.symbol}" target="_blank" rel="noopener" title="Open full chart in new tab">📊 Open</a></td>
            <td style="color:{dir_color}">{dir_arrow} {w.signal}</td>
            <td style="text-align:right">${w.current_price:.2f}</td>
            <td style="text-align:right;color:#fbbf24">${w.level:.2f}</td>
            <td style="text-align:right;color:#64748b">—</td>
            <td style="text-align:right;color:#64748b">—</td>
            <td style="text-align:right;color:#fbbf24">{w.distance_pct:+.1f}%</td>
            <td style="text-align:right;color:#64748b">—</td>
            <td style="font-size:11px;color:#cbd5e1;line-height:1.45">{w_plan}</td>
          </tr>
        """)

    table_rows = "\n".join(rows) if rows else "<tr><td colspan=12 style='text-align:center;padding:24px;color:#94a3b8'>No setups firing right now — try again later or add more tickers.</td></tr>"

    # Price map exposed to client-side JS for alarm checking
    import json as _json
    price_map_json = _json.dumps({s.symbol: s.current_price for s in setups_sorted})

    # Verdict summary chips above table
    legend_html = ""
    watch_count = len(watches_sorted[:8])
    for label, color in [
        ("STRONG TAKE", "#22c55e"), ("TAKE", "#86efac"),
        ("👁 WATCH",    "#a78bfa"),  # Wave 18 — forming setups
        ("MARGINAL",    "#f59e0b"), ("AVOID", "#ef4444"),
    ]:
        n = watch_count if label == "👁 WATCH" else verdict_counts.get(label, 0)
        legend_html += f'<span class="legend-pill" style="background:{color};color:#000">{label} · {n}</span>'

    # Regime + macro banner — top-of-page risk-context strip
    regime_color = {
        "low-vol":  "#22c55e", "normal": "#94a3b8",
        "elevated": "#f59e0b", "extreme": "#ef4444", "unknown": "#475569",
    }.get(market_regime.get("vix_regime", "unknown"), "#475569")
    vix_lvl = market_regime.get("vix_level")
    vix_lvl_str = f"{vix_lvl:.1f}" if vix_lvl is not None else "—"
    regime_strip = (
        f'<div class="regime-strip">'
        f'<span class="regime-pill" style="background:{regime_color};color:#000">'
        f'VIX {vix_lvl_str} · {market_regime.get("vix_regime", "unknown").upper()}</span>'
    )
    if macro_event:
        regime_strip += (
            f'<span class="regime-pill" style="background:#ef4444;color:#000">'
            f'⚠ {macro_event[1]} on {macro_event[0]} — within 24h</span>'
        )
    # Sector concentration warning
    concentrated = [(etf, n) for etf, n in sector_counts.items() if n >= 3]
    if concentrated:
        for etf, n in concentrated:
            regime_strip += (
                f'<span class="regime-pill" style="background:#f59e0b;color:#000">'
                f'⚠ {n} setups in {etf} — correlation risk (1 trade, not {n})</span>'
            )
    regime_strip += '</div>'

    # Autocomplete suggestions: every alias key + the watchlist itself.
    # Browser shows these as a dropdown when the user types in the search box.
    _suggestion_set: set[str] = set(TICKER_ALIASES.keys()) | set(TICKER_ALIASES.values())
    _suggestion_set |= {s.symbol for s in setups_sorted}
    _suggestion_set |= {s.symbol for s in snapshots}
    ticker_suggestions_html = "".join(
        f'<option value="{sym}"></option>' for sym in sorted(_suggestion_set) if sym
    )

    import json as _json2

    def _chart_price_lines(s_list: list, snap: Optional["Snapshot"]) -> list[dict]:
        """Build the list of horizontal price lines for a Lightweight Charts pane.
        Combines:
          • Setup entry/stop/targets (yellow/red/green solid)
          • Recent swing S/R (faint green/red dashed)
          • Full Fibonacci ladder + extensions from 52-week swing (yellow dashed)
          • Pivot Points PP/R1/R2/S1/S2 (white/red/green dashed)
          • Anchored VWAP (blue solid)
          • Round numbers (gray very faint dashed)
        Each line: {price, color, lineStyle (0=solid,2=dashed), lineWidth, title}.
        """
        lines: list[dict] = []
        for s in s_list:
            lines.append({"price": s.entry,     "color": "#fbbf24", "lineStyle": 0, "lineWidth": 2, "title": f"Entry ${s.entry:.2f}"})
            lines.append({"price": s.stop_loss, "color": "#ef4444", "lineStyle": 0, "lineWidth": 2, "title": f"Stop ${s.stop_loss:.2f}"})
            for ti, t in enumerate(s.targets[:2], 1):
                lines.append({"price": t,       "color": "#22c55e", "lineStyle": 2, "lineWidth": 2, "title": f"T{ti} ${t:.2f}"})
        if snap is not None:
            # Swing S/R clusters (existing)
            for sup in (snap.support_levels or [])[-3:]:
                lines.append({"price": sup, "color": "#22c55e88", "lineStyle": 2, "lineWidth": 1, "title": f"S ${sup:.2f}"})
            for res in (snap.resistance_levels or [])[-3:]:
                lines.append({"price": res, "color": "#ef444488", "lineStyle": 2, "lineWidth": 1, "title": f"R ${res:.2f}"})
            # Fibonacci retracements + extensions from 52-week swing
            if snap.fib and snap.fib.get("retracements"):
                for pct, px in snap.fib["retracements"].items():
                    # Highlight 0.618 + 0.66 (CC region) brighter
                    is_cc = pct in ("0.618", "0.660")
                    lines.append({
                        "price": float(px),
                        "color": "#fbbf24" if is_cc else "#fbbf2488",
                        "lineStyle": 2,
                        "lineWidth": 2 if is_cc else 1,
                        "title": f"Fib {pct} ${float(px):.2f}",
                    })
                for pct, px in (snap.fib.get("extensions") or {}).items():
                    lines.append({
                        "price": float(px),
                        "color": "#f97316aa",       # orange for extensions
                        "lineStyle": 2, "lineWidth": 1,
                        "title": f"Fib ext {pct} ${float(px):.2f}",
                    })
            # DAILY Pivot Points (yellow tones — daily TF)
            if snap.pivots:
                p = snap.pivots
                lines.append({"price": p["pp"], "color": "#fde047", "lineStyle": 2, "lineWidth": 1, "title": f"DAILY PP ${p['pp']:.2f}"})
                for key, label in [("r1","R1"),("r2","R2"),("s1","S1"),("s2","S2")]:
                    if key in p:
                        color = "#fde04788" if key.startswith("r") else "#fde04788"
                        lines.append({"price": p[key], "color": color, "lineStyle": 2, "lineWidth": 1, "title": f"DAILY {label} ${p[key]:.2f}"})
            # WEEKLY Pivot Points (pink/magenta — weekly TF)
            if snap.pivots_weekly:
                p = snap.pivots_weekly
                lines.append({"price": p["pp"], "color": "#ec4899", "lineStyle": 2, "lineWidth": 2, "title": f"WEEKLY PP ${p['pp']:.2f}"})
                for key, label in [("r1","R1"),("r2","R2"),("s1","S1"),("s2","S2")]:
                    if key in p:
                        lines.append({"price": p[key], "color": "#ec489988", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY {label} ${p[key]:.2f}"})
            # MONTHLY Pivot Points (cyan/purple — monthly TF)
            if snap.pivots_monthly:
                p = snap.pivots_monthly
                lines.append({"price": p["pp"], "color": "#a855f7", "lineStyle": 2, "lineWidth": 2, "title": f"MONTHLY PP ${p['pp']:.2f}"})
                for key, label in [("r1","R1"),("s1","S1")]:
                    if key in p:
                        lines.append({"price": p[key], "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY {label} ${p[key]:.2f}"})
            # Recent WEEKLY highs and lows
            for w in (snap.recent_weekly or [])[-3:]:
                lines.append({"price": w["high"], "color": "#ec4899aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY high ${w['high']:.2f}"})
                lines.append({"price": w["low"],  "color": "#ec4899aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY low ${w['low']:.2f}"})
            # Recent MONTHLY highs and lows
            for m in (snap.recent_monthly or [])[-3:]:
                lines.append({"price": m["high"], "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY high ${m['high']:.2f}"})
                lines.append({"price": m["low"],  "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY low ${m['low']:.2f}"})
            # WEEKLY Volume Profile — POC / VAH / VAL
            if snap.vp_weekly and "poc" in snap.vp_weekly:
                vp = snap.vp_weekly
                lines.append({"price": vp["poc"], "color": "#f97316", "lineStyle": 0, "lineWidth": 2, "title": f"WEEKLY POC ${vp['poc']:.2f}"})
                if "vah" in vp:
                    lines.append({"price": vp["vah"], "color": "#f97316aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY VAH ${vp['vah']:.2f}"})
                if "val" in vp:
                    lines.append({"price": vp["val"], "color": "#f97316aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY VAL ${vp['val']:.2f}"})
            # MONTHLY Volume Profile
            if snap.vp_monthly and "poc" in snap.vp_monthly:
                vp = snap.vp_monthly
                lines.append({"price": vp["poc"], "color": "#dc2626", "lineStyle": 0, "lineWidth": 2, "title": f"MONTHLY POC ${vp['poc']:.2f}"})
                if "vah" in vp:
                    lines.append({"price": vp["vah"], "color": "#dc2626aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY VAH ${vp['vah']:.2f}"})
                if "val" in vp:
                    lines.append({"price": vp["val"], "color": "#dc2626aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY VAL ${vp['val']:.2f}"})
            # Naked POCs — POCs from prior weeks not yet retested
            for n in (snap.naked_pocs or [])[:6]:
                lines.append({
                    "price": float(n["poc"]),
                    "color": "#06b6d4",     # cyan — distinctive
                    "lineStyle": 2, "lineWidth": 1,
                    "title": f"nPOC ${float(n['poc']):.2f}",
                })
            # Camarilla pivots (if Snapshot carries them) — teal tones
            cam = getattr(snap, "camarilla", None)
            if cam:
                for key, label in [("h4","H4"),("h3","H3"),("h2","H2"),("h1","H1"),
                                   ("l1","L1"),("l2","L2"),("l3","L3"),("l4","L4")]:
                    if key in cam:
                        color = "#14b8a688" if key.startswith("h") else "#14b8a688"
                        lines.append({"price": float(cam[key]), "color": color,
                                      "lineStyle": 2, "lineWidth": 1,
                                      "title": f"CAM {label} ${float(cam[key]):.2f}"})
            # Anchored VWAP — solid blue
            if snap.vwap_anchored is not None:
                lines.append({
                    "price": float(snap.vwap_anchored),
                    "color": "#3b82f6",
                    "lineStyle": 0, "lineWidth": 2,
                    "title": f"VWAP ${float(snap.vwap_anchored):.2f}",
                })
            # Round numbers (very subtle gray dashed)
            for rn in (snap.round_numbers or [])[:6]:
                lines.append({
                    "price": float(rn),
                    "color": "#94a3b822",     # very faint
                    "lineStyle": 2, "lineWidth": 1,
                    "title": f"${float(rn):.0f}",
                })
        return lines

    # --- One Lightweight Charts chart per ticker with a setup
    charts = []
    seen_symbols: set[str] = set()
    for i, s in enumerate(setups_sorted):
        if s.symbol in seen_symbols:
            continue
        seen_symbols.add(s.symbol)
        tv = _tv_symbol(s.symbol)
        ticker_setups_all = [x for x in setups_sorted if x.symbol == s.symbol]
        snap_for_chart = levels_by_symbol.get(s.symbol)
        price_lines_json = _json2.dumps(_chart_price_lines(ticker_setups_all, snap_for_chart))
        # Collect ALL setups for this ticker
        ticker_setups = [x for x in setups_sorted if x.symbol == s.symbol]
        levels_html = ""
        for ts in ticker_setups:
            long = ts.direction == "long"
            tone = "#22c55e" if long else "#ef4444"
            levels_html += f"""
            <div class="setup-card">
              <div class="setup-head" style="color:{tone}">
                {'▲ LONG' if long else '▼ SHORT'} · {ts.name}
                <span class="conv">{int(ts.conviction*100)}%</span>
              </div>
              <div class="setup-grid">
                <div><span class="lbl">Entry</span><span class="val">${ts.entry:.2f}</span></div>
                <div><span class="lbl">Stop</span><span class="val" style="color:#ef4444">${ts.stop_loss:.2f}</span></div>
                <div><span class="lbl">Target 1</span><span class="val" style="color:#22c55e">${ts.targets[0]:.2f}</span></div>
                <div><span class="lbl">Target 2</span><span class="val" style="color:#22c55e">${ts.targets[1] if len(ts.targets)>1 else ts.targets[0]:.2f}</span></div>
                <div><span class="lbl">R:R</span><span class="val">{ts.risk_reward:.2f}R</span></div>
                <div><span class="lbl">Move</span><span class="val">{ts.move_pct:+.1f}%</span></div>
              </div>
              <div class="rationale">{ts.reasoning}</div>
              <div class="cite">📖 {ts.citation}</div>
              {_render_flags(ts.context_flags)}
              {_render_key_levels_panel(levels_by_symbol.get(ts.symbol))}
              {_render_equity_panel(getattr(levels_by_symbol.get(ts.symbol), "equity_analysis", None))}
              {(_ai_voice_block(ts.ai_analysis))}
              <div class="setup-actions">
                <button onclick="sizeTrade('{ts.symbol}', {ts.entry:.4f}, {ts.stop_loss:.4f})">📐 Size this</button>
                <button class="take-btn" onclick="takeTrade('{ts.symbol}', '{ts.name}', '{ts.direction}', {ts.entry:.4f}, {ts.stop_loss:.4f}, {ts.targets[0] if ts.targets else 0:.4f}, {ts.targets[1] if len(ts.targets)>1 else 0:.4f})">▶ Take</button>
                <button onclick="passTrade('{ts.symbol}', '{ts.name}')">⏭ Pass</button>
              </div>
            </div>
            """
        has_data = s.symbol in chart_data_by_symbol
        chart_body = (
            f'<div class="lwc-chart" id="lwc_{i}" data-symbol="{s.symbol}" '
            f"data-lines='{price_lines_json}'></div>"
            if has_data
            else '<div class="lwc-fallback">📉 Chart data unavailable — try refreshing.</div>'
        )
        # Wave 12: fired-setup card is compact — chart opens in new tab.
        # The setup details (entry/stop/targets/conviction/AI commentary) stay
        # right here in the levels_html so you see them immediately.
        charts.append(f"""
        <div class="ticker-block ticker-compact" id="chart-{i}">
          <h2>{s.symbol}
            <span class="tv-link">·
              <a href="/chart?symbol={s.symbol}" target="_blank" class="open-chart-btn">📊 Open Chart →</a>
              ·
              <a href="https://www.tradingview.com/chart/?symbol={tv}" target="_blank">open on TradingView →</a>
            </span>
          </h2>
          <div class="setups-side">{levels_html}</div>
        </div>
        """)
    charts_html = "\n".join(charts)

    # --- Watchlist Monitoring table: a row per scanned ticker, hidden by
    # default. Client-side JS reads cc_stars from localStorage and shows only
    # the rows that match. This lets the user see their watchlist as a TABLE
    # with live price + key levels, regardless of whether a CC setup is firing.
    watches_by_sym_for_monitor: dict[str, list] = {}
    for w in watches:
        watches_by_sym_for_monitor.setdefault(w.symbol, []).append(w)

    def _fmt_pct(val, ref) -> tuple[str, str]:
        """Return (text, color) for a "$X.XX (↑ +Y.Y%)" cell."""
        if val is None or ref is None or ref <= 0:
            return ("—", "#64748b")
        d = (val - ref) / ref * 100.0
        arrow = "↑" if d > 0 else ("↓" if d < 0 else "•")
        sign = "+" if d > 0 else ""
        return (f"${val:.2f} {arrow} {sign}{d:.1f}%",
                "#22c55e" if d > 0 else ("#ef4444" if d < 0 else "#94a3b8"))

    monitor_rows = []
    for sym in sorted(levels_by_symbol.keys()):
        s = levels_by_symbol[sym]
        px = s.current_price
        # Vs EMAs
        e55_txt, e55_col   = _fmt_pct(s.ema_55,  px)
        e200_txt, e200_col = _fmt_pct(s.ema_200, px)
        # RSI with regime color
        if s.rsi_14 is None:
            rsi_html = '<span style="color:#64748b">—</span>'
        else:
            rc = "#ef4444" if s.rsi_14 > 70 else ("#22c55e" if s.rsi_14 < 30 else "#94a3b8")
            rsi_html = f'<span style="color:{rc}">{s.rsi_14:.1f}</span>'
        # Nearest support below, nearest resistance above
        sup_html = res_html = '<span style="color:#64748b">—</span>'
        if s.support_levels:
            top_sup = max(s.support_levels)  # the closest one BELOW price
            sup_txt, _ = _fmt_pct(top_sup, px)
            sup_html = f'<span style="color:#22c55e">{sup_txt}</span>'
        if s.resistance_levels:
            bot_res = min(s.resistance_levels)  # closest ABOVE price
            res_txt, _ = _fmt_pct(bot_res, px)
            res_html = f'<span style="color:#ef4444">{res_txt}</span>'
        # Forming watch summary
        wlist = watches_by_sym_for_monitor.get(sym, [])
        if wlist:
            forming_html = (f'<span style="color:#fbbf24">▲ {wlist[0].signal}</span>'
                            if wlist[0].direction == "long"
                            else f'<span style="color:#f59e0b">▼ {wlist[0].signal}</span>')
        else:
            forming_html = '<span style="color:#64748b">—</span>'
        # Sector
        etf = SECTOR_ETF.get(sym, "SPY")
        sector_html = f'<span style="color:#94a3b8">{etf}</span>'
        # Whether the ticker fired a setup (so we can link to its chart anchor)
        scrolltarget = ""
        for i, fired in enumerate(setups_sorted):
            if fired.symbol == sym:
                scrolltarget = f"document.getElementById('chart-{i}').scrollIntoView({{behavior:'smooth'}})"
                break
        if not scrolltarget:
            # find its snapshot chart instead — they're appended after fired charts
            for j, snap in enumerate(snapshots):
                if snap.symbol == sym:
                    scrolltarget = f"document.getElementById('chart-{len(seen_symbols)+j}').scrollIntoView({{behavior:'smooth'}})"
                    break
        view_btn = (f'<button class="mon-btn" onclick="{scrolltarget}">📊 Chart</button>'
                    if scrolltarget else
                    '<button class="mon-btn" disabled style="opacity:0.4">no chart</button>')

        monitor_rows.append(f"""
          <tr class="monitor-row" data-symbol="{sym}" style="display:none">
            <td class="actions">
              <button class="bell-btn" data-symbol="{sym}" data-price="{px:.2f}" onclick="setAlarm(event,'{sym}',{px:.2f})" title="Price alarm">🔔</button>
            </td>
            <td><b>{sym}</b></td>
            <td style="text-align:right">${px:.2f}</td>
            <td style="text-align:right;color:{e55_col};font-family:ui-monospace,monospace">{e55_txt}</td>
            <td style="text-align:right;color:{e200_col};font-family:ui-monospace,monospace">{e200_txt}</td>
            <td style="text-align:right">{rsi_html}</td>
            <td style="text-align:right">{sup_html}</td>
            <td style="text-align:right">{res_html}</td>
            <td>{sector_html}</td>
            <td>{forming_html}</td>
            <td style="white-space:nowrap">
              <button class="mon-btn" onclick="openManualSetupModal('{sym}',{px:.2f})">✎ Setup</button>
              {view_btn}
            </td>
          </tr>
        """)

    monitor_table_html = f"""
    <h2 style="margin-top:32px">📡 My Watchlist — Live Monitoring <span class="sub" id="monitor-count">(empty)</span></h2>
    <div id="monitor-empty" class="journal-empty" style="display:none">
      Your watchlist is empty. Star any ticker (☆) below to monitor it here, or use <b>+ Add ticker</b>.
    </div>
    <table id="monitor-table" style="display:none">
      <thead><tr>
        <th>⭐🔔</th>
        <th>Symbol</th>
        <th style="text-align:right">Price</th>
        <th style="text-align:right">vs EMA 55</th>
        <th style="text-align:right">vs EMA 200</th>
        <th style="text-align:right">RSI</th>
        <th style="text-align:right">Nearest Support</th>
        <th style="text-align:right">Nearest Resistance</th>
        <th>Sector</th>
        <th>Forming Setup</th>
        <th>Action</th>
      </tr></thead>
      <tbody>{''.join(monitor_rows)}</tbody>
    </table>
    """

    # --- Watching section: setups that are FORMING but not yet firing.
    # Group by symbol so each ticker appears once with all its watch items.
    watches_by_sym: dict[str, list] = {}
    for w in watches:
        watches_by_sym.setdefault(w.symbol, []).append(w)
    # Hide tickers that already have a fired setup — those are in the table above.
    fired_syms = {s.symbol for s in setups_sorted}
    watching_blocks: list[str] = []
    for sym in sorted(watches_by_sym.keys()):
        if sym in fired_syms:
            continue
        items = watches_by_sym[sym]
        snap = levels_by_symbol.get(sym)
        items_html = ""
        for w in items[:4]:
            dir_color = "#22c55e" if w.direction == "long" else "#ef4444"
            arrow = "▲" if w.direction == "long" else "▼"
            sign = "+" if w.distance_pct > 0 else ""
            items_html += (
                f'<div class="watch-row" style="border-left:3px solid {dir_color}">'
                f'<div class="watch-head"><span style="color:{dir_color}">{arrow} {w.signal}</span>'
                f'<span class="watch-dist">{sign}{w.distance_pct:.1f}% · ~{w.bars_estimate}d</span></div>'
                f'<div class="watch-detail">Waiting for: {w.waiting_for}</div>'
                f'<div class="cite">📖 {w.citation}</div>'
                f'</div>'
            )
        kl = _render_key_levels_panel(snap) if snap else ""
        px = snap.current_price if snap else 0.0
        watching_blocks.append(
            f'<div class="watching-card" data-symbol="{sym}">'
            f'<div class="wc-head">'
            f'<b>{sym}</b> <span class="watch-price">${px:.2f}</span>'
            f'<button class="bell-btn" data-symbol="{sym}" data-price="{px:.2f}" '
            f'onclick="setAlarm(event,\'{sym}\',{px:.2f})" title="Price alarm">🔔</button>'
            f'</div>'
            f'<div class="watch-list">{items_html}</div>'
            f'{kl}'
            f'</div>'
        )
    watching_html = ""
    if watching_blocks:
        watching_html = (
            '<h2 style="margin-top:32px">👁 Watching — setups forming '
            f'<span class="sub">({len(watching_blocks)} ticker(s) close to firing)</span></h2>'
            '<div class="watching-grid">' + "".join(watching_blocks) + '</div>'
        )

    # --- Snapshot cards for ad-hoc tickers with no setup
    snap_idx = len(charts)  # continue id numbering
    snap_blocks = []
    for snap in snapshots:
        tv = _tv_symbol(snap.symbol)
        # current values block
        lines = []
        def _fmt(v): return f"${v:.2f}" if v is not None else "—"
        lines.append(f"<div><span class='lbl'>Price</span><span class='val'>${snap.current_price:.2f}</span></div>")
        lines.append(f"<div><span class='lbl'>EMA 55</span><span class='val'>{_fmt(snap.ema_55)}</span></div>")
        lines.append(f"<div><span class='lbl'>EMA 100</span><span class='val'>{_fmt(snap.ema_100)}</span></div>")
        lines.append(f"<div><span class='lbl'>EMA 200</span><span class='val'>{_fmt(snap.ema_200)}</span></div>")
        if snap.rsi_14 is not None:
            lines.append(f"<div><span class='lbl'>RSI 14</span><span class='val'>{snap.rsi_14:.1f}</span></div>")
        if snap.support_levels:
            lines.append(f"<div><span class='lbl'>Support</span><span class='val'>{', '.join(f'${s:.2f}' for s in snap.support_levels)}</span></div>")
        if snap.resistance_levels:
            lines.append(f"<div><span class='lbl'>Resistance</span><span class='val'>{', '.join(f'${s:.2f}' for s in snap.resistance_levels)}</span></div>")
        # Wave 12: compact snapshot card — NO pre-rendered chart (saves memory).
        # Chart opens in a new tab via /chart?symbol=X (full CC experience there).
        snap_blocks.append(f"""
        <div class="ticker-block ticker-compact" id="chart-{snap_idx}">
          <div class="setup-card">
            <div class="setup-head" style="color:#94a3b8;display:flex;justify-content:space-between;align-items:center">
              <span>📊 {snap.symbol} · CC context</span>
              <span class="snap-actions">
                <a href="/chart?symbol={snap.symbol}" target="_blank" class="open-chart-btn" title="Open full chart in new tab">📊 Open Chart →</a>
                <button class="bell-btn" data-symbol="{snap.symbol}" data-price="{snap.current_price:.2f}" onclick="setAlarm(event,'{snap.symbol}',{snap.current_price:.2f})" title="Set price alarm">🔔</button>
                <button class="add-list-btn" onclick="openManualSetupModal('{snap.symbol}',{snap.current_price:.2f})" title="Add manual setup for this ticker">✎ Setup</button>
              </span>
            </div>
            {_render_key_levels_panel(snap)}
            {_render_equity_panel(getattr(snap, "equity_analysis", None))}
            {_render_flags(snap.context_flags)}
            <div class="rationale" style="margin-top:10px">
              No Chart Champions setup is firing on this ticker right now. Click <b>📊 Open Chart →</b> to see candles + all levels (Fibs, pivots, POC, nPOC, VWAP, Camarilla, EMAs) on the full chart page.
            </div>
          </div>
        </div>
        """)
        snap_idx += 1
    snapshots_html = "\n".join(snap_blocks)

    # Encode all chart data into a single global JS object.
    chart_data_json = _json2.dumps(chart_data_by_symbol, default=float)

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>CC Trader — Live Setups</title>
{_favicon_link_tags()}
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://s3.tradingview.com/tv.js"></script>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background:#0a0f1c; color:#e2e8f0; margin:0; padding:24px; }}
  h1 {{ margin:0 0 4px 0; font-size:24px; }}
  h2 {{ margin:36px 0 12px 0; font-size:18px; }}
  .tv-link {{ font-size:12px; font-weight:normal; color:#94a3b8; }}
  .tv-link a {{ color:#22c55e; text-decoration:none; }}
  .sub {{ color:#94a3b8; font-size:13px; margin-bottom:24px; }}
  table {{ width:100%; border-collapse:collapse; background:#0f172a; border-radius:12px; overflow:hidden; }}
  th, td {{ padding:10px 14px; border-bottom:1px solid #1e293b; font-size:13px; vertical-align:top; }}
  th {{ background:#1e293b; color:#94a3b8; text-transform:uppercase; font-size:11px; text-align:left; }}
  tr:hover {{ background:#111827; }}
  .footer {{ margin-top:20px; color:#64748b; font-size:11px; }}

  .ticker-block {{ background:#0f172a; border-radius:12px; padding:16px; margin-top:18px; }}
  .chart-row {{ display:grid; grid-template-columns: minmax(0, 1fr) 380px; gap:16px; }}
  @media (max-width: 1100px) {{ .chart-row {{ grid-template-columns: 1fr; }} }}

  /* Wave 13 — collapsible sections on main page */
  .collapsible-section {{ margin-top:18px; background:#0f172a; border:1px solid #1e293b; border-radius:8px; padding:0; }}
  .collapsible-section > summary {{ padding:14px 18px; cursor:pointer; font-size:15px; font-weight:600; color:#fbbf24; user-select:none; list-style:none; outline:none; }}
  .collapsible-section > summary::-webkit-details-marker {{ display:none; }}
  .collapsible-section > summary::before {{ content:'▶ '; display:inline-block; margin-right:6px; font-size:10px; transition: transform 0.2s; }}
  .collapsible-section[open] > summary::before {{ transform: rotate(90deg); }}
  .collapsible-section > summary:hover {{ background:#1e293b; }}
  .collapsible-section > summary .sub {{ color:#64748b; font-weight:400; font-size:12px; margin-left:6px; }}
  .collapsible-body {{ padding:0 18px 18px 18px; }}

  /* Wave 12 — compact ticker cards (chart opens in new tab via /chart?symbol=X) */
  .ticker-compact {{ padding:10px 16px; }}
  .ticker-compact h2 {{ margin:0 0 8px 0; font-size:15px; }}
  .open-chart-btn {{ padding:5px 12px; background:#22c55e; color:#000; text-decoration:none; border-radius:4px; font-size:11px; font-weight:700; font-family:ui-monospace,monospace; }}
  .open-chart-btn:hover {{ background:#16a34a; }}
  /* Compact Open Chart button for in-table cells (Wave 13.x) */
  .open-chart-mini {{ padding:3px 8px; background:#22c55e; color:#000; text-decoration:none; border-radius:3px; font-size:10px; font-weight:700; font-family:ui-monospace,monospace; white-space:nowrap; display:inline-block; }}
  .open-chart-mini:hover {{ background:#16a34a; }}

  /* Hybrid chart host — switches between CC LWC view and TradingView widget */
  .chart-host {{ background:#0a0f1c; border-radius:8px; padding:8px; position:relative; }}
  .chart-toolbar {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:6px; flex-wrap:wrap; }}
  .view-toggle {{ display:flex; gap:0; background:#0f172a; border-radius:6px; padding:2px; }}
  .view-btn {{ padding:6px 14px; border:0; background:transparent; color:#94a3b8; border-radius:4px; font-size:11px; cursor:pointer; font-weight:600; font-family:ui-monospace,monospace; }}
  .view-btn:hover {{ background:#1e293b; color:#e2e8f0; }}
  .view-btn.active {{ background:#22c55e; color:#000; }}
  .chart-extras {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
  .anno-btn {{ padding:5px 10px; background:#0a0f1c; color:#94a3b8; border:1px solid #1e293b; border-radius:4px; font-size:11px; cursor:pointer; font-family:ui-monospace,monospace; }}
  .anno-btn:hover {{ background:#1e293b; color:#fbbf24; border-color:#fbbf24; }}
  .countdown-badge {{ padding:4px 10px; background:#1e1b4b; color:#a78bfa; border-radius:4px; font-size:11px; font-family:ui-monospace,monospace; font-weight:600; }}
  .tv-widget-host {{ height:600px; width:100%; }}
  .tv-widget-host > div {{ height:600px !important; width:100% !important; }}
  .tv-widget-host iframe {{ height:600px !important; width:100% !important; border:0 !important; border-radius:6px; }}

  /* Lightweight Charts container — entry/stop/targets drawn directly */
  .lwc-wrap {{ background:#0a0f1c; border-radius:8px; padding:8px; position:relative; }}
  /* Timeframe selector bar above each chart */
  .tf-bar {{ display:flex; gap:4px; margin-bottom:6px; padding:4px; background:#0f172a; border-radius:6px; }}
  .tf-btn {{ padding:5px 12px; border:1px solid #1e293b; background:#0a0f1c; color:#94a3b8; border-radius:4px; font-size:11px; font-family:ui-monospace,monospace; cursor:pointer; font-weight:600; }}
  .tf-btn:hover:not(:disabled) {{ background:#1e293b; color:#e2e8f0; }}
  .tf-btn.active {{ background:#22c55e; color:#000; border-color:#22c55e; }}
  .tf-btn.tf-unavailable {{ opacity:0.35; cursor:not-allowed; }}
  .lwc-chart {{ height:560px; width:100%; }}
  .lwc-fallback {{ height:560px; display:flex; align-items:center; justify-content:center; color:#64748b; font-size:13px; }}
  .lwc-legend {{ position:absolute; left:14px; top:14px; background:rgba(15,23,42,0.78); border:1px solid #1e293b; border-radius:6px; padding:6px 10px; font-size:11px; font-family:ui-monospace,monospace; color:#94a3b8; pointer-events:none; line-height:1.6; }}
  .lwc-legend .lg-row {{ display:flex; gap:8px; align-items:center; }}
  .lwc-legend .lg-dot {{ width:8px; height:2px; border-radius:1px; display:inline-block; }}
  .lwc-legend .lg-px {{ color:#fbbf24; font-weight:700; }}

  .setups-side {{ display:flex; flex-direction:column; gap:10px; }}
  .setup-card {{ background:#0a0f1c; border:1px solid #1e293b; border-radius:8px; padding:12px; }}
  .setup-head {{ font-weight:600; font-size:13px; display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .conv {{ background:#22c55e; color:#000; padding:2px 6px; border-radius:4px; font-size:11px; font-family:ui-monospace,monospace; }}
  .setup-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:4px 16px; font-size:12px; }}
  .setup-grid div {{ display:flex; justify-content:space-between; }}
  .lbl {{ color:#64748b; }}
  .val {{ font-family:ui-monospace,monospace; }}
  .rationale {{ font-size:11px; color:#94a3b8; margin-top:8px; }}
  .cite {{ font-size:10px; color:#64748b; margin-top:6px; }}
  .ai-voice {{ margin-top:10px; padding:10px; background:linear-gradient(135deg,#1e293b 0%,#0f1729 100%); border-left:3px solid #22c55e; border-radius:6px; font-size:12px; line-height:1.5; }}
  .ai-head {{ font-size:10px; text-transform:uppercase; letter-spacing:1px; color:#22c55e; margin-bottom:6px; font-weight:600; }}
  .flags {{ display:flex; flex-direction:column; gap:4px; margin-top:8px; padding-top:8px; border-top:1px solid #1e293b; }}
  .flag {{ display:flex; justify-content:space-between; padding:4px 8px; background:#0a0f1c; border-radius:4px; font-size:11px; }}
  .flag-l {{ font-weight:600; }}
  .flag-d {{ color:#94a3b8; font-size:10px; }}

  /* Verdict pills + row colors */
  .legend {{ display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }}
  .legend-pill {{ padding:4px 10px; border-radius:6px; font-size:11px; font-weight:700; letter-spacing:0.5px; font-family:ui-monospace,monospace; }}
  .verdict-pill {{ padding:3px 8px; border-radius:4px; font-size:10px; font-weight:700; letter-spacing:0.5px; font-family:ui-monospace,monospace; white-space:nowrap; }}
  tr.row-strong-take {{ border-left:4px solid #22c55e; background:rgba(34,197,94,0.06); }}
  tr.row-take        {{ border-left:4px solid #86efac; background:rgba(134,239,172,0.04); }}
  tr.row-marginal    {{ border-left:4px solid #f59e0b; background:rgba(245,158,11,0.04); }}
  tr.row-avoid       {{ border-left:4px solid #ef4444; background:rgba(239,68,68,0.05); opacity:0.7; }}
  tr.row-strong-take:hover {{ background:rgba(34,197,94,0.12); }}
  tr.row-take:hover        {{ background:rgba(134,239,172,0.10); }}
  tr.row-marginal:hover    {{ background:rgba(245,158,11,0.10); }}
  tr.row-avoid:hover       {{ background:rgba(239,68,68,0.10); }}

  /* Topbar — search + filters */
  .topbar {{ display:flex; flex-direction:column; gap:10px; margin-bottom:14px; }}
  .search-form {{ display:flex; gap:8px; align-items:center; }}
  /* Wave 23 — multi-watchlist bar */
  .watchlists-bar {{ display:flex; flex-wrap:wrap; gap:8px; padding:10px 14px; background:#0f172a; border:1px solid #1e293b; border-radius:8px; align-items:center; font-size:12px; margin-bottom:10px; }}
  .watchlists-bar .wl-label {{ color:#fbbf24; font-weight:700; font-size:11px; text-transform:uppercase; letter-spacing:0.6px; }}
  .watchlists-bar .wl-select {{ padding:6px 12px; border-radius:6px; border:1px solid #1e293b; background:#0a0f1c; color:#e2e8f0; font-size:12px; font-family:ui-monospace,monospace; cursor:pointer; min-width:200px; }}
  .watchlists-bar .wl-select:hover {{ border-color:#fbbf24; }}
  .watchlists-bar .wl-btn {{ padding:6px 12px; border-radius:6px; border:1px solid #22c55e; background:transparent; color:#22c55e; cursor:pointer; font-size:11px; font-weight:600; }}
  .watchlists-bar .wl-btn:hover {{ background:#22c55e; color:#000; }}
  .watchlists-bar .wl-btn.danger {{ border-color:#ef4444; color:#ef4444; }}
  .watchlists-bar .wl-btn.danger:hover {{ background:#ef4444; color:#000; }}
  .watchlists-bar .wl-chips {{ display:flex; flex-wrap:wrap; gap:4px; padding-left:8px; flex:1; min-width:0; }}
  .watchlists-bar .wl-chip {{ display:inline-flex; align-items:center; gap:4px; padding:3px 8px; background:#1e1b4b; color:#a78bfa; border-radius:12px; font-size:11px; font-family:ui-monospace,monospace; }}
  .watchlists-bar .wl-chip .x {{ cursor:pointer; color:#94a3b8; font-size:10px; }}
  .watchlists-bar .wl-chip .x:hover {{ color:#ef4444; }}
  .watchlists-bar .wl-empty {{ color:#64748b; font-style:italic; padding-left:8px; }}
  .search-form input {{ flex:1; max-width:560px; padding:9px 14px; border-radius:8px; border:1px solid #1e293b; background:#0f172a; color:#e2e8f0; font-size:13px; }}
  .search-form button {{ padding:9px 18px; border:0; border-radius:8px; background:#22c55e; color:#000; font-weight:700; cursor:pointer; }}
  .search-form button:hover {{ background:#16a34a; }}
  .reset-link {{ color:#94a3b8; font-size:12px; text-decoration:none; padding-left:8px; }}
  .reset-link:hover {{ color:#e2e8f0; }}
  .filter-bar {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .filter-btn {{ padding:6px 12px; border:1px solid #1e293b; background:#0f172a; color:#94a3b8; border-radius:6px; font-size:11px; cursor:pointer; font-family:ui-monospace,monospace; }}
  .filter-btn:hover {{ background:#1e293b; color:#e2e8f0; }}
  .filter-btn.active {{ background:#22c55e; color:#000; font-weight:700; border-color:#22c55e; }}

  /* Star + bell columns */
  .actions {{ white-space:nowrap; width:62px; }}
  .star-btn, .bell-btn {{ background:transparent; border:0; color:#64748b; font-size:16px; cursor:pointer; padding:2px 4px; }}
  .star-btn:hover, .bell-btn:hover {{ color:#fde047; }}
  .star-btn.on {{ color:#fbbf24; }}
  .bell-btn.on {{ color:#22c55e; }}
  .sym-link {{ cursor:pointer; color:#e2e8f0; text-decoration:none; border-bottom:1px dotted #475569; }}
  .sym-link:hover {{ color:#22c55e; border-color:#22c55e; }}

  /* Regime strip — risk context above the legend */
  .regime-strip {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }}
  .regime-pill {{ padding:5px 12px; border-radius:6px; font-size:11px; font-weight:700; letter-spacing:0.5px; font-family:ui-monospace,monospace; }}

  /* Active alarm bar (banner when an alarm fires) */
  .alarm-toast {{ position:fixed; bottom:24px; right:24px; max-width:380px; background:linear-gradient(135deg,#16a34a,#22c55e); color:#000; padding:14px 18px; border-radius:10px; font-weight:600; box-shadow:0 10px 30px rgba(0,0,0,0.6); z-index:9999; }}

  /* My-list bar — custom watchlist controls */
  .mylist-bar {{ display:flex; flex-wrap:wrap; gap:8px; padding:10px 14px; background:#0f172a; border:1px solid #1e293b; border-radius:8px; align-items:center; font-size:12px; }}
  .mylist-bar b {{ color:#fbbf24; }}
  .mylist-bar .ml-btn {{ padding:6px 12px; border-radius:6px; border:1px solid #22c55e; background:transparent; color:#22c55e; cursor:pointer; font-size:11px; font-weight:600; }}
  .mylist-bar .ml-btn:hover {{ background:#22c55e; color:#000; }}
  .mylist-bar .ml-btn.danger {{ border-color:#ef4444; color:#ef4444; }}
  .mylist-bar .ml-btn.danger:hover {{ background:#ef4444; color:#000; }}
  .mylist-chips {{ display:flex; gap:4px; flex-wrap:wrap; }}
  .mylist-chip {{ padding:2px 8px; border-radius:4px; background:#1e293b; color:#fbbf24; font-family:ui-monospace,monospace; font-size:11px; display:inline-flex; gap:6px; align-items:center; }}
  .mylist-chip .x {{ cursor:pointer; color:#94a3b8; }}
  .mylist-chip .x:hover {{ color:#ef4444; }}

  /* Key Levels panel — appears below every setup card */
  .key-levels {{ margin-top:10px; padding:10px; background:#0a0f1c; border:1px dashed #1e293b; border-radius:6px; }}
  .kl-head {{ font-size:10px; text-transform:uppercase; letter-spacing:1px; color:#fbbf24; margin-bottom:8px; font-weight:600; }}
  .lvl-dist {{ font-size:10px; color:#64748b; }}
  .ai-offline {{ border-left-color:#94a3b8 !important; opacity:0.8; }}
  .ai-offline code {{ background:#1e293b; padding:1px 4px; border-radius:3px; color:#fbbf24; font-size:11px; }}

  /* Position sizer + trade journal panels */
  .tools-bar {{ display:flex; flex-wrap:wrap; gap:12px; margin:10px 0 14px 0; padding:12px 14px; background:#0f172a; border:1px solid #1e293b; border-radius:8px; align-items:center; font-size:12px; }}
  .tools-bar label {{ color:#94a3b8; font-size:11px; }}
  .tools-bar input {{ width:90px; padding:5px 8px; border-radius:4px; border:1px solid #1e293b; background:#0a0f1c; color:#e2e8f0; font-family:ui-monospace,monospace; }}
  .tools-bar .tools-btn {{ padding:6px 12px; border-radius:6px; border:1px solid #22c55e; background:transparent; color:#22c55e; cursor:pointer; font-size:11px; font-weight:600; }}
  .tools-bar .tools-btn:hover {{ background:#22c55e; color:#000; }}
  .size-out {{ color:#fbbf24; font-weight:700; font-family:ui-monospace,monospace; }}

  /* Trade journal panel */
  .journal-panel {{ background:#0f172a; border:1px solid #1e293b; border-radius:8px; padding:14px; margin-top:18px; }}
  .journal-panel h3 {{ margin:0 0 10px 0; font-size:14px; color:#fbbf24; }}
  .journal-row {{ display:grid; grid-template-columns: 80px 60px 60px 70px 70px 70px 1fr 60px; gap:8px; padding:6px 0; border-bottom:1px solid #1e293b; font-size:11px; font-family:ui-monospace,monospace; align-items:center; }}
  .journal-row.header {{ color:#94a3b8; font-weight:700; border-bottom:2px solid #334155; }}
  .journal-row .r-win {{ color:#22c55e; }}
  .journal-row .r-loss {{ color:#ef4444; }}
  .journal-empty {{ color:#64748b; padding:18px; text-align:center; font-size:12px; }}

  /* Per-setup "Size this trade" + "Take" buttons */
  .setup-actions {{ display:flex; gap:6px; margin-top:10px; padding-top:8px; border-top:1px solid #1e293b; }}
  .setup-actions button {{ flex:1; padding:5px 8px; border-radius:4px; border:1px solid #1e293b; background:#0a0f1c; color:#94a3b8; cursor:pointer; font-size:11px; }}
  .setup-actions button:hover {{ background:#1e293b; color:#e2e8f0; }}
  .setup-actions .take-btn {{ border-color:#22c55e; color:#22c55e; }}
  .setup-actions .take-btn:hover {{ background:#22c55e; color:#000; }}

  /* Monitor table — watchlist live snapshot rows */
  #monitor-table {{ margin-top:6px; }}
  #monitor-table th {{ font-size:10px; }}
  #monitor-table td {{ font-size:12px; padding:8px 12px; }}
  .monitor-row:hover {{ background:#111827; }}
  .mon-btn {{ padding:3px 8px; border-radius:4px; border:1px solid #1e293b; background:#0a0f1c; color:#94a3b8; cursor:pointer; font-size:10px; font-family:ui-monospace,monospace; margin-right:3px; }}
  .mon-btn:hover {{ background:#1e293b; color:#e2e8f0; }}

  /* Equity Analysis panel (Structured Equity Analysis Model — fundamentals) */
  .equity-panel {{ margin-top:10px; padding:12px; background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 100%); border:1px solid #312e81; border-left:4px solid #a78bfa; border-radius:8px; font-size:11px; }}
  .eq-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .eq-title {{ font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#a78bfa; font-weight:700; }}
  .eq-band {{ padding:3px 10px; border-radius:4px; font-size:10px; font-weight:700; font-family:ui-monospace,monospace; }}
  .eq-snap {{ color:#cbd5e1; margin-bottom:8px; line-height:1.4; }}
  .eq-grid {{ display:grid; gap:3px; margin-bottom:8px; }}
  .eq-row {{ display:flex; justify-content:space-between; align-items:center; padding:3px 0; border-bottom:1px solid #1e293b; }}
  .eq-label {{ color:#94a3b8; }}
  .eq-score {{ display:flex; align-items:center; gap:8px; }}
  .eq-val {{ font-family:ui-monospace,monospace; font-weight:700; min-width:30px; text-align:right; }}
  .eq-stance {{ margin-top:6px; color:#cbd5e1; }}
  .eq-thesis {{ margin-top:6px; font-size:10px; color:#94a3b8; line-height:1.4; }}
  .eq-thesis div {{ margin-top:2px; }}
  .eq-inval {{ margin-top:8px; padding-top:8px; border-top:1px solid #1e293b; color:#fbbf24; font-size:10px; }}
  .eq-inval ul {{ margin:4px 0 0 0; padding-left:18px; color:#94a3b8; }}
  .eq-inval li {{ margin-bottom:2px; }}

  /* Snapshot card action buttons (star, bell, +list, +setup) */
  .snap-actions {{ display:flex; gap:4px; align-items:center; }}
  .snap-actions .star-btn, .snap-actions .bell-btn {{ font-size:16px; padding:2px 4px; }}
  .add-list-btn {{ padding:3px 8px; background:#0a0f1c; color:#fbbf24; border:1px solid #1e293b; border-radius:4px; cursor:pointer; font-size:10px; font-family:ui-monospace,monospace; }}
  .add-list-btn:hover {{ background:#fbbf24; color:#000; }}

  /* Manual setup modal */
  .ms-modal {{ position:fixed; top:0; left:0; right:0; bottom:0; z-index:10000; display:flex; align-items:center; justify-content:center; }}
  .ms-backdrop {{ position:absolute; inset:0; background:rgba(0,0,0,0.7); }}
  .ms-dialog {{ position:relative; background:#0f172a; border:1px solid #334155; border-radius:12px; padding:24px; max-width:480px; width:90%; box-shadow:0 20px 60px rgba(0,0,0,0.6); z-index:1; }}
  .ms-grid {{ display:grid; grid-template-columns:120px 1fr; gap:10px 12px; align-items:center; }}
  .ms-grid label {{ color:#94a3b8; font-size:12px; }}
  .ms-grid input, .ms-grid textarea {{ padding:7px 10px; border-radius:6px; border:1px solid #1e293b; background:#0a0f1c; color:#e2e8f0; font-family:ui-monospace,monospace; font-size:12px; width:100%; box-sizing:border-box; }}
  .ms-radio {{ display:inline-flex; gap:6px; align-items:center; margin-right:14px; color:#e2e8f0; font-size:12px; }}
  .ms-preview {{ padding:8px 10px; background:#0a0f1c; border-left:3px solid #fbbf24; border-radius:4px; font-size:11px; color:#94a3b8; font-family:ui-monospace,monospace; }}
  .ms-buttons {{ display:flex; gap:10px; margin-top:20px; justify-content:flex-end; }}
  .ms-cancel {{ padding:8px 16px; border-radius:6px; border:1px solid #334155; background:transparent; color:#94a3b8; cursor:pointer; }}
  .ms-cancel:hover {{ background:#1e293b; color:#e2e8f0; }}
  .ms-save {{ padding:8px 16px; border-radius:6px; border:0; background:#22c55e; color:#000; font-weight:700; cursor:pointer; }}
  .ms-save:hover {{ background:#16a34a; }}

  /* Manual setup cards — rendered client-side from localStorage */
  .manual-card {{ background:#0f172a; border:1px solid #1e293b; border-left:4px solid #fbbf24; border-radius:8px; padding:14px; margin-top:10px; }}
  .manual-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
  .manual-head .mh-left {{ display:flex; gap:10px; align-items:center; }}
  .manual-head b {{ font-size:15px; }}
  .manual-pill {{ padding:2px 8px; border-radius:4px; background:#fbbf24; color:#000; font-size:10px; font-weight:700; letter-spacing:0.5px; font-family:ui-monospace,monospace; }}
  .manual-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(110px, 1fr)); gap:4px 16px; font-size:12px; }}
  .manual-grid div {{ display:flex; justify-content:space-between; }}
  .manual-notes {{ margin-top:8px; padding:8px; background:#0a0f1c; border-radius:4px; font-size:11px; color:#94a3b8; }}
  .manual-actions {{ display:flex; gap:6px; margin-top:10px; padding-top:8px; border-top:1px solid #1e293b; flex-wrap:wrap; }}
  .manual-actions button {{ flex:1; min-width:80px; padding:5px 8px; border-radius:4px; border:1px solid #1e293b; background:#0a0f1c; color:#94a3b8; cursor:pointer; font-size:11px; }}
  .manual-actions button:hover {{ background:#1e293b; color:#e2e8f0; }}
  .manual-actions .take-btn {{ border-color:#22c55e; color:#22c55e; }}
  .manual-actions .take-btn:hover {{ background:#22c55e; color:#000; }}
  .manual-actions .del-btn {{ border-color:#ef4444; color:#ef4444; flex:0; min-width:auto; padding:5px 10px; }}
  .manual-actions .del-btn:hover {{ background:#ef4444; color:#000; }}

  /* Watching section — formed setups, not yet firing */
  .watching-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(360px, 1fr)); gap:12px; margin:12px 0 24px 0; }}
  .watching-card {{ background:#0f172a; border:1px solid #1e293b; border-left:4px solid #fbbf24; border-radius:8px; padding:12px; }}
  .wc-head {{ display:flex; gap:6px; align-items:center; margin-bottom:10px; font-size:14px; }}
  .wc-head b {{ font-size:15px; }}
  .watch-price {{ font-family:ui-monospace,monospace; color:#fbbf24; margin-left:6px; }}
  .watch-row {{ background:#0a0f1c; padding:8px 10px; border-radius:4px; margin-bottom:6px; font-size:11px; }}
  .watch-head {{ display:flex; justify-content:space-between; font-weight:600; }}
  .watch-dist {{ color:#fbbf24; font-family:ui-monospace,monospace; }}
  .watch-detail {{ color:#94a3b8; margin-top:3px; }}
</style></head>
<body>
  <h1>📈 Live CC Setups</h1>
  <div class="sub">
    Chart Champions detectors over real market data ·
    Scanned <b>{scanned}</b> tickers in {duration_s:.1f}s ·
    <b>{len(setups)}</b> setup(s) found ·
    Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  </div>

  <div class="topbar">
    <!-- Wave 23 — Multiple named watchlists row (up to 10). -->
    <div class="watchlists-bar">
      <span class="wl-label">📋 List:</span>
      <select id="wl-active-select" onchange="onWatchlistSelectChange()" class="wl-select"></select>
      <button class="wl-btn" onclick="createWatchlist()" title="Create a new named list (max 10)">+ New list</button>
      <button class="wl-btn" onclick="renameActiveWatchlist()" title="Rename current list">✎ Rename</button>
      <button class="wl-btn danger" onclick="deleteActiveWatchlist()" title="Delete current list">🗑 Delete</button>
      <span id="wl-chips" class="wl-chips"></span>
    </div>

    <form method="GET" action="/" class="search-form" id="search-form" onsubmit="return handleScanSubmit(event);">
      <input name="symbols" id="search-input" list="ticker-suggestions"
             placeholder="🔍 Add ticker to your active list — type 'bitcoin', 'apple', 'GLD', 'AAPL'..." autocomplete="off"/>
      <datalist id="ticker-suggestions">{ticker_suggestions_html}</datalist>
      <button type="submit">Add & Scan</button>
      <a href="/" class="reset-link">↩ Default watchlist</a>
    </form>

    <div class="filter-bar">
      <button class="filter-btn active" data-filter="all">All</button>
      <button class="filter-btn" data-filter="strong-take">🟢 STRONG TAKE</button>
      <button class="filter-btn" data-filter="take">🟢 TAKE</button>
      <button class="filter-btn" data-filter="watch">👁 WATCH</button>
      <button class="filter-btn" data-filter="long">▲ Long</button>
      <button class="filter-btn" data-filter="short">▼ Short</button>
    </div>
  </div>

  {regime_strip}

  <div class="tools-bar">
    <label>Account $:</label>
    <input id="acct-size" type="number" value="10000" step="100" oninput="onSizerChange()"/>
    <label>Risk %:</label>
    <input id="risk-pct"  type="number" value="0.5"  step="0.1" oninput="onSizerChange()"/>
    <span class="size-out">Risk per trade: $<span id="risk-dollars">50.00</span></span>
    <button class="tools-btn" onclick="openManualSetupModal('','')">✎ Add manual setup</button>
    <button class="tools-btn" onclick="document.getElementById('manual-section').scrollIntoView({{behavior:'smooth'}})">📝 My setups</button>
    <button class="tools-btn" onclick="document.getElementById('journal-panel').scrollIntoView({{behavior:'smooth'}})">📒 Trade Journal</button>
    <button class="tools-btn" onclick="if(confirm('Reset stars, alarms, manual setups, and journal?')){{localStorage.removeItem('cc_stars');localStorage.removeItem('cc_alarms');localStorage.removeItem('cc_journal');localStorage.removeItem('cc_manual_setups');location.reload();}}">Reset all data</button>
  </div>

  <div class="legend">{legend_html}</div>

  <table>
    <thead><tr>
      <th>🔔</th>
      <th>Verdict</th>
      <th>Symbol</th><th>Chart</th><th>Setup</th>
      <th style="text-align:right">Price</th>
      <th style="text-align:right">Entry</th>
      <th style="text-align:right">Stop</th>
      <th style="text-align:right">Targets</th>
      <th style="text-align:right">R:R / Move</th>
      <th style="text-align:right">Conv</th>
      <th>Plan + CC citation</th>
    </tr></thead>
    <tbody>{table_rows}</tbody>
  </table>

  <!-- Wave 19 — Per-ticker detail cards removed from main page. The table
       row's PLAN column carries the actionable summary; clicking the chart
       link in any row opens the FULL CC analysis at /chart?symbol=X. -->

  <details class="collapsible-section">
    <summary>📡 Watchlist Monitor <span class="sub" id="monitor-count-summary">(click to expand)</span></summary>
    <div class="collapsible-body">{monitor_table_html}</div>
  </details>

  <details class="collapsible-section">
    <summary>📝 My Manual Setups <span class="sub" id="manual-count">(click to expand)</span></summary>
    <div class="collapsible-body">
      <div id="manual-section">
        <div id="manual-cards"></div>
      </div>
    </div>
  </details>

  <details class="collapsible-section">
    <summary>👁 Forming Setups <span class="sub">(click to expand)</span></summary>
    <div class="collapsible-body">{watching_html}</div>
  </details>

  <details class="collapsible-section">
    <summary>📊 All Tickers Overview <span class="sub">(click to expand — Key Levels, Equity Analysis, flags for every watchlist ticker)</span></summary>
    <div class="collapsible-body">{snapshots_html}</div>
  </details>

  <!-- Manual setup modal -->
  <div id="manual-modal" class="ms-modal" style="display:none">
    <div class="ms-backdrop" onclick="closeManualSetupModal()"></div>
    <div class="ms-dialog">
      <h3 style="margin:0 0 16px 0;color:#fbbf24">✎ Add Manual Setup</h3>
      <div class="ms-grid">
        <label>Symbol</label>
        <input id="ms-symbol" placeholder="AAPL, BTC-USD, bitcoin..." autocomplete="off"/>

        <label>Direction</label>
        <div>
          <label class="ms-radio"><input type="radio" name="ms-dir" value="long" checked/> ▲ Long</label>
          <label class="ms-radio"><input type="radio" name="ms-dir" value="short"/> ▼ Short</label>
        </div>

        <label>Setup name</label>
        <input id="ms-name" placeholder="e.g. Breakout above resistance" value="Manual setup"/>

        <label>Entry $</label>
        <input id="ms-entry" type="number" step="0.01" placeholder="0.00"/>

        <label>Stop $</label>
        <input id="ms-stop" type="number" step="0.01" placeholder="0.00"/>

        <label>Target 1 $</label>
        <input id="ms-t1" type="number" step="0.01" placeholder="0.00"/>

        <label>Target 2 $ <span style="color:#64748b">(opt)</span></label>
        <input id="ms-t2" type="number" step="0.01" placeholder="0.00"/>

        <label>Notes <span style="color:#64748b">(opt)</span></label>
        <textarea id="ms-notes" rows="3" placeholder="Thesis, what to watch for..."></textarea>

        <label></label>
        <div id="ms-preview" class="ms-preview">Fill in entry + stop + target1 to see R:R preview</div>
      </div>
      <div class="ms-buttons">
        <button class="ms-cancel" onclick="closeManualSetupModal()">Cancel</button>
        <button class="ms-save" onclick="saveManualSetup()">💾 Save setup</button>
      </div>
    </div>
  </div>

  <details class="collapsible-section">
    <summary>📒 Trade Journal <span class="sub" id="journal-count-summary">(click to expand)</span></summary>
    <div class="collapsible-body">
  <div class="journal-panel" id="journal-panel">
    <h3>📒 Trade Journal</h3>
    <div class="sub" style="margin:0 0 10px 0">Stored in your browser — survives reloads but not cache wipes. Use the buttons on each setup card to log a trade.</div>
    <div id="journal-rows"></div>
    <div style="margin-top:10px;color:#94a3b8;font-size:11px">
      Stats: <span id="journal-stats">no trades yet</span>
      <button class="tools-btn" style="margin-left:12px" onclick="exportJournal()">⬇ Export CSV</button>
    </div>
  </div>
    </div>
  </details>

  <div class="footer">
    Methodology source: Chart Champions PDFs uploaded by operator. Run the
    script again any time — chart data refreshes live via TradingView,
    setups recompute against Yahoo Finance daily bars.
  </div>

  <script>
    // Map of current prices from this scan, exposed for client-side alarm checks.
    window.cc_prices = {price_map_json};
    // OHLCV + EMA series per symbol, used by Lightweight Charts on this page.
    window.cc_charts_data = {chart_data_json};

    // ---- Lightweight Charts init -----------------------------------------
    function _fmtNum(n) {{ return (n || 0).toFixed(2); }}
    // Track per-chart state so timeframe switching can replace series in-place.
    window.cc_chart_handles = window.cc_chart_handles || {{}};

    function _getTfData(rawSymData, tf) {{
      // New format: {{default_tf, timeframes: {{1H: {{candles,...}}}}}}.
      // Old format (legacy fallback): {{candles, volume, ema_55, ema_100, ema_200}}.
      if (rawSymData && rawSymData.timeframes) {{
        return rawSymData.timeframes[tf] || rawSymData.timeframes[rawSymData.default_tf] || null;
      }}
      // Legacy single-timeframe payload — treat it as 1D.
      if (rawSymData && rawSymData.candles) {{
        return rawSymData;
      }}
      return null;
    }}

    function _availableTfs(rawSymData) {{
      if (rawSymData && rawSymData.timeframes) {{
        return Object.keys(rawSymData.timeframes);
      }}
      return rawSymData && rawSymData.candles ? ["1D"] : [];
    }}

    function initLightweightCharts() {{
      if (typeof LightweightCharts === 'undefined') {{
        console.warn('LightweightCharts library not loaded');
        return;
      }}
      document.querySelectorAll('.lwc-chart').forEach(function(div) {{
        var sym = div.getAttribute('data-symbol');
        var rawData = window.cc_charts_data[sym];
        var avail = _availableTfs(rawData);
        if (!avail.length) {{
          div.innerHTML = '<div style="padding:30px;color:#64748b">No chart data for ' + sym + '</div>';
          return;
        }}
        // Default to 1D if available, otherwise first available
        var defaultTf = (rawData.default_tf && avail.indexOf(rawData.default_tf) >= 0)
                          ? rawData.default_tf : avail[0];
        var initial = _getTfData(rawData, defaultTf);
        if (!initial || !initial.candles || !initial.candles.length) {{
          div.innerHTML = '<div style="padding:30px;color:#64748b">No chart data for ' + sym + '</div>';
          return;
        }}

        var chart = LightweightCharts.createChart(div, {{
          layout:        {{ background: {{ type:'solid', color:'#0a0f1c' }}, textColor:'#94a3b8' }},
          grid:          {{ vertLines: {{ color:'#1e293b' }}, horzLines: {{ color:'#1e293b' }} }},
          rightPriceScale: {{ borderColor:'#1e293b' }},
          timeScale:     {{ borderColor:'#1e293b', timeVisible:(defaultTf==='1H') }},
          crosshair:     {{ mode: 1 }},
          autoSize:      true,
        }});

        var candleSeries = chart.addCandlestickSeries({{
          upColor:'#22c55e', downColor:'#ef4444',
          borderUpColor:'#22c55e', borderDownColor:'#ef4444',
          wickUpColor:'#22c55e', wickDownColor:'#ef4444',
        }});
        candleSeries.setData(initial.candles);

        var volSeries = chart.addHistogramSeries({{
          priceFormat: {{ type:'volume' }},
          priceScaleId: '',
          color:'#22c55e55',
        }});
        volSeries.priceScale().applyOptions({{ scaleMargins: {{ top:0.85, bottom:0 }} }});
        if (initial.volume && initial.volume.length) volSeries.setData(initial.volume);

        // EMAs — keep references so we can swap data on TF change
        var emaSeries = {{}};
        function addEMA(key, series, color, title) {{
          if (!series || !series.length) {{ emaSeries[key] = null; return; }}
          var s = chart.addLineSeries({{
            color: color, lineWidth: 1, title: title,
            lastValueVisible:false, priceLineVisible:false,
          }});
          s.setData(series);
          emaSeries[key] = s;
        }}
        addEMA('ema_8',   initial.ema_8,   '#fbbf24', 'EMA 8');
        addEMA('ema_21',  initial.ema_21,  '#f59e0b', 'EMA 21');
        addEMA('ema_55',  initial.ema_55,  '#94a3b8', 'EMA 55');
        addEMA('ema_100', initial.ema_100, '#cbd5e1', 'EMA 100');
        addEMA('ema_200', initial.ema_200, '#64748b', 'EMA 200');

        // Horizontal price-lines (Entry/Stop/Targets/Fibs/Pivots/POCs/VWAP).
        // Drawn on the candle series — they appear on ALL timeframes since
        // they're absolute price levels, not bar-relative.
        var rawLines = div.getAttribute('data-lines') || '[]';
        var lines;
        try {{ lines = JSON.parse(rawLines); }} catch(_) {{ lines = []; }}
        var priceLineHandles = [];
        function applyPriceLines() {{
          priceLineHandles.forEach(function(h) {{ try {{ candleSeries.removePriceLine(h); }} catch(_) {{}} }});
          priceLineHandles = lines.map(function(l) {{
            return candleSeries.createPriceLine({{
              price: l.price, color: l.color, lineWidth: l.lineWidth || 2,
              lineStyle: l.lineStyle === 2 ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
              axisLabelVisible: true,
              title: l.title || '',
            }});
          }});
        }}
        applyPriceLines();

        // Legend
        var legendId = div.id.replace('lwc_', 'lg_');
        var legend = document.getElementById(legendId);
        if (legend) {{
          legend.innerHTML =
            '<div class="lg-row"><span class="lg-dot" style="background:#22c55e"></span> Bull candle</div>'
          + '<div class="lg-row"><span class="lg-dot" style="background:#fbbf24"></span> EMA 8</div>'
          + '<div class="lg-row"><span class="lg-dot" style="background:#f59e0b"></span> EMA 21</div>'
          + '<div class="lg-row"><span class="lg-dot" style="background:#94a3b8"></span> EMA 55</div>'
          + '<div class="lg-row"><span class="lg-dot" style="background:#cbd5e1"></span> EMA 100</div>'
          + '<div class="lg-row"><span class="lg-dot" style="background:#64748b"></span> EMA 200</div>'
          + '<div class="lg-row"><span class="lg-px">' + sym + '</span></div>';
        }}

        chart.timeScale().fitContent();
        new ResizeObserver(function() {{
          chart.applyOptions({{ width: div.clientWidth, height: div.clientHeight }});
        }}).observe(div);

        // Store handles for the TF selector to swap data
        window.cc_chart_handles[div.id] = {{
          chart: chart,
          candleSeries: candleSeries,
          volSeries: volSeries,
          emaSeries: emaSeries,
          currentTf: defaultTf,
          rawData: rawData,
        }};

        // Wire timeframe selector buttons (if present near this chart)
        var wrap = div.closest('.lwc-wrap');
        if (wrap) {{
          var btns = wrap.querySelectorAll('.tf-btn');
          btns.forEach(function(btn) {{
            var tf = btn.getAttribute('data-tf');
            // Disable buttons for TFs not available for this symbol
            if (avail.indexOf(tf) < 0) {{
              btn.classList.add('tf-unavailable');
              btn.title = tf + ' not available (yfinance limit or insufficient history)';
              btn.disabled = true;
            }}
            if (tf === defaultTf) btn.classList.add('active');
            btn.addEventListener('click', function() {{
              if (btn.disabled) return;
              switchTimeframe(div.id, tf, btn);
            }});
          }});
        }}
      }});
    }}

    function switchTimeframe(chartId, tf, btn) {{
      var h = window.cc_chart_handles[chartId];
      if (!h) return;
      var tfData = _getTfData(h.rawData, tf);
      if (!tfData || !tfData.candles || !tfData.candles.length) return;
      h.candleSeries.setData(tfData.candles);
      if (h.volSeries && tfData.volume) h.volSeries.setData(tfData.volume);
      // Refresh EMA series. Some TFs may not have EMAs (not enough bars).
      ['ema_8','ema_21','ema_55','ema_100','ema_200'].forEach(function(k) {{
        var s = h.emaSeries[k];
        if (s) {{
          s.setData(tfData[k] || []);
        }}
      }});
      // Update timeVisible flag for intraday
      h.chart.applyOptions({{ timeScale: {{ timeVisible: (tf === '1H') }} }});
      h.chart.timeScale().fitContent();
      h.currentTf = tf;
      // Highlight active button
      if (btn) {{
        var wrap = btn.closest('.lwc-wrap');
        if (wrap) {{
          wrap.querySelectorAll('.tf-btn').forEach(function(b) {{ b.classList.remove('active'); }});
          btn.classList.add('active');
        }}
      }}
    }}

    function getStars()  {{ try {{ return JSON.parse(localStorage.getItem('cc_stars')  || '[]'); }} catch(_) {{ return []; }} }}
    function getAlarms() {{ try {{ return JSON.parse(localStorage.getItem('cc_alarms') || '[]'); }} catch(_) {{ return []; }} }}
    function saveStars(v)  {{ localStorage.setItem('cc_stars',  JSON.stringify(v)); }}
    function saveAlarms(v) {{ localStorage.setItem('cc_alarms', JSON.stringify(v)); }}

    function toggleStar(ev, sym) {{
      ev.stopPropagation();
      const s = getStars();
      const i = s.indexOf(sym);
      if (i >= 0) s.splice(i, 1); else s.push(sym);
      saveStars(s);
      applyStarUI();
      applyFilter();
      renderMyListBar();
      renderMonitorTable();
    }}

    // --- Watchlist Monitoring table -------------------------------------
    // Server pre-renders 1 row per scanned ticker, hidden. We show only the
    // rows whose symbol is in cc_stars.
    function renderMonitorTable() {{
      const stars = new Set(getStars());
      const table = document.getElementById('monitor-table');
      const empty = document.getElementById('monitor-empty');
      const label = document.getElementById('monitor-count');
      if (!table) return;
      let shown = 0;
      document.querySelectorAll('.monitor-row').forEach(tr => {{
        const sym = tr.dataset.symbol;
        if (stars.has(sym)) {{
          tr.style.display = '';
          shown++;
        }} else {{
          tr.style.display = 'none';
        }}
      }});
      if (shown === 0) {{
        table.style.display = 'none';
        if (empty) empty.style.display = '';
        if (label) label.textContent = '(empty)';
      }} else {{
        table.style.display = '';
        if (empty) empty.style.display = 'none';
        if (label) label.textContent = '(' + shown + ' ' + (shown === 1 ? 'stock' : 'stocks') + ')';
      }}
    }}

    // Wave 20 — The My Watchlist UI was removed. getStars() / saveStars()
    // are kept as private helpers used by the 'Add & Scan' search bar to
    // mirror what was typed into localStorage (so the page-load sync to
    // /api/watchlist still works). No DOM element to render anymore.
    function renderMyListBar() {{ /* no-op since Wave 20 */ }}
    // ----- Wave 23 — Multi-watchlist state ----------------------------
    // State shape (localStorage 'cc_watchlists'):
    //   lists is an object mapping name to ticker-array, plus an 'active'
    //   key naming which list is currently selected.
    // The dropdown reads 'active' to know which list 'Add & Scan' inserts
    // into. The 'getStars()' / 'saveStars()' helpers below are wrappers
    // around the ACTIVE list so all the legacy add-ticker code keeps
    // working unchanged.
    var WL_DEFAULT_NAME = 'My Watchlist';
    var WL_MAX = 10;
    function getWatchlists() {{
      try {{
        var raw = localStorage.getItem('cc_watchlists');
        if (raw) {{
          var d = JSON.parse(raw);
          if (d && d.lists && typeof d.lists === 'object') {{
            if (!d.active || !(d.active in d.lists)) {{
              d.active = Object.keys(d.lists)[0] || WL_DEFAULT_NAME;
            }}
            return d;
          }}
        }}
      }} catch(_) {{}}
      // Migrate from legacy cc_stars flat list if present
      var legacy = null;
      try {{ legacy = JSON.parse(localStorage.getItem('cc_stars') || '[]'); }} catch(_) {{}}
      var lists = {{}};
      lists[WL_DEFAULT_NAME] = Array.isArray(legacy) ? legacy : [];
      return {{ lists: lists, active: WL_DEFAULT_NAME }};
    }}
    function saveWatchlists(d) {{
      try {{ localStorage.setItem('cc_watchlists', JSON.stringify(d)); }} catch(_) {{}}
      // Also keep legacy cc_stars in sync (= active list) for any old
      // code path that still reads it.
      try {{ localStorage.setItem('cc_stars', JSON.stringify(d.lists[d.active] || [])); }} catch(_) {{}}
    }}
    function syncWatchlistsToBackend() {{
      var d = getWatchlists();
      return fetch('/api/watchlists', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(d),
      }}).catch(function(err) {{ console.warn('multi-watchlist sync failed', err); }});
    }}

    function renderWatchlistsUI() {{
      var d = getWatchlists();
      var sel = document.getElementById('wl-active-select');
      if (sel) {{
        sel.innerHTML = Object.keys(d.lists).map(function(name) {{
          var n = (d.lists[name] || []).length;
          var label = name + ' (' + n + ')';
          return '<option value="' + name.replace(/"/g, '&quot;') + '"'
                 + (name === d.active ? ' selected' : '') + '>' + label + '</option>';
        }}).join('');
      }}
      var chipsEl = document.getElementById('wl-chips');
      if (chipsEl) {{
        var tickers = d.lists[d.active] || [];
        if (!tickers.length) {{
          chipsEl.innerHTML = '<span class="wl-empty">Empty — type a ticker in the search bar to add</span>';
        }} else {{
          chipsEl.innerHTML = tickers.map(function(t) {{
            return '<span class="wl-chip">' + t
                 + ' <span class="x" onclick="removeFromActiveList(\\'' + t + '\\')" title="Remove">✕</span>'
                 + '</span>';
          }}).join('');
        }}
      }}
      // Update search-bar placeholder to reflect active list.
      var inp = document.getElementById('search-input');
      if (inp) inp.placeholder = '🔍 Add ticker to "' + d.active + '" — type AAPL, bitcoin, GLD…';
    }}

    function onWatchlistSelectChange() {{
      var sel = document.getElementById('wl-active-select');
      var d = getWatchlists();
      if (!sel || !(sel.value in d.lists)) return;
      d.active = sel.value;
      saveWatchlists(d);
      syncWatchlistsToBackend();
      renderWatchlistsUI();
      showToast('📋 Active list: ' + d.active);
    }}

    function createWatchlist() {{
      var d = getWatchlists();
      if (Object.keys(d.lists).length >= WL_MAX) {{
        return alert('Max ' + WL_MAX + ' lists reached. Delete one first.');
      }}
      var name = prompt('Name for the new list (e.g. "Future Buys", "Current Holdings"):');
      if (!name) return;
      name = name.trim().slice(0, 40);
      if (!name) return;
      if (name in d.lists) return alert('A list named "' + name + '" already exists.');
      d.lists[name] = [];
      d.active = name;
      saveWatchlists(d);
      syncWatchlistsToBackend();
      renderWatchlistsUI();
      showToast('✓ Created list "' + name + '" and set as active');
    }}

    function renameActiveWatchlist() {{
      var d = getWatchlists();
      var current = d.active;
      var name = prompt('Rename "' + current + '" to:', current);
      if (!name) return;
      name = name.trim().slice(0, 40);
      if (!name || name === current) return;
      if (name in d.lists) return alert('A list named "' + name + '" already exists.');
      d.lists[name] = d.lists[current];
      delete d.lists[current];
      d.active = name;
      saveWatchlists(d);
      syncWatchlistsToBackend();
      renderWatchlistsUI();
      showToast('✓ Renamed to "' + name + '"');
    }}

    function deleteActiveWatchlist() {{
      var d = getWatchlists();
      var name = d.active;
      if (Object.keys(d.lists).length <= 1) {{
        return alert('Cannot delete the last list. Rename it or add another first.');
      }}
      if (!confirm('Delete list "' + name + '" with ' + (d.lists[name] || []).length + ' ticker(s)? Tickers in OTHER lists are kept.')) return;
      delete d.lists[name];
      d.active = Object.keys(d.lists)[0];
      saveWatchlists(d);
      syncWatchlistsToBackend();
      renderWatchlistsUI();
      showToast('🗑 Deleted "' + name + '"');
    }}

    function removeFromActiveList(sym) {{
      var d = getWatchlists();
      var arr = d.lists[d.active] || [];
      d.lists[d.active] = arr.filter(function(t) {{ return t !== sym; }});
      saveWatchlists(d);
      syncWatchlistsToBackend();
      renderWatchlistsUI();
    }}

    // ----- Active-list wrappers (legacy 'stars' API) -------------------
    function getStars() {{
      var d = getWatchlists();
      return (d.lists[d.active] || []).slice();
    }}
    function saveStars(arr) {{
      var d = getWatchlists();
      d.lists[d.active] = arr || [];
      saveWatchlists(d);
    }}
    // Legacy single-list backend sync — kept for any code path that still
    // calls it. Wave 23 prefers syncWatchlistsToBackend() but this alias
    // still works (it POSTs ALL lists, not just one).
    function syncWatchlistToBackend() {{
      return syncWatchlistsToBackend();
    }}
    function triggerImmediateScan(sym) {{
      // Wave 15 — Kick a one-shot full scan for the newly-added ticker so it
      // appears in the main table within seconds, not after the next 5-min
      // background cycle. Result rendered as a toast + a meta-refresh once
      // the scanner re-runs naturally.
      return fetch('/api/scan-now?symbol=' + encodeURIComponent(sym))
        .then(function(r) {{ return r.json(); }})
        .then(function(j) {{
          if (j.error) {{
            showToast('⚠ ' + sym + ': ' + j.error);
            return;
          }}
          var msg = '🎯 ' + sym + ' analyzed';
          if (j.setups_count > 0) msg += ' — ' + j.setups_count + ' setup(s) firing!';
          else                    msg += ' — no setup firing, snapshot ready';
          showToast(msg);
        }})
        .catch(function(err) {{ showToast('⚠ ' + sym + ' scan failed'); }});
    }}
    // Wave 17 → Wave 22 — Search bar submit handler.
    // Old behavior: persist + reload, which showed CACHED background-scan
    // HTML that DIDN'T contain the new ticker yet (background loop runs
    // every 5 min). User saw 'Added' but the ticker never appeared.
    // New behavior: persist + scan-now in parallel, then INJECT the result
    // row directly into the table. No reload, no waiting for the next cycle.
    function _injectScanRow(j) {{
      // Build a synthetic <tr> from /api/scan-now JSON and prepend to the
      // main scan table so the operator sees their freshly-added ticker
      // immediately. Uses the WATCH purple if no setup fired, else the
      // first setup's verdict color.
      var tbody = document.querySelector('table tbody');
      if (!tbody) return;
      var sym = j.symbol;
      var px = (j.current_price || 0).toFixed(2);
      var primary = (j.setups && j.setups.length) ? j.setups[0] : null;
      var verdict, vcolor, dir, dirArrow, dirColor, setupName, entry, stop, targets, rr, conv, planHtml;
      if (primary) {{
        var entryF = parseFloat(primary.entry);
        var stopF  = parseFloat(primary.stop);
        var t1     = (primary.targets && primary.targets.length) ? parseFloat(primary.targets[0]) : entryF;
        var risk   = Math.abs(entryF - stopF) || 0.001;
        var rrVal  = Math.abs(t1 - entryF) / risk;
        verdict   = (rrVal >= 2.0 && primary.conviction >= 0.75) ? 'STRONG TAKE'
                  : (rrVal >= 1.5 && primary.conviction >= 0.65) ? 'TAKE'
                  : (rrVal < 1.0) ? 'AVOID' : 'MARGINAL';
        vcolor    = {{ 'STRONG TAKE':'#22c55e','TAKE':'#86efac','MARGINAL':'#f59e0b','AVOID':'#ef4444' }}[verdict];
        dir       = primary.direction;
        dirArrow  = dir === 'long' ? '▲' : '▼';
        dirColor  = dir === 'long' ? '#22c55e' : '#ef4444';
        setupName = primary.name;
        entry     = '$' + entryF.toFixed(2);
        stop      = '$' + stopF.toFixed(2);
        targets   = (primary.targets || []).map(function(t) {{ return '$' + parseFloat(t).toFixed(2); }}).join(' · ');
        rr        = rrVal.toFixed(2) + 'R';
        conv      = Math.round(primary.conviction * 100) + '%';
        var holds = dir === 'long' ? 'holds above' : 'holds below';
        var abort = dir === 'long' ? 'breaks below' : 'breaks above';
        planHtml  = '🎯 <b>IF ' + holds + ' ' + entry + '</b> → ride <b>' + dir + '</b> to '
                  + '<span style="color:#22c55e">$' + t1.toFixed(2) + '</span> (' + rrVal.toFixed(1) + 'R). '
                  + '<b>ABORT</b> if ' + abort + ' <span style="color:#ef4444">' + stop + '</span>. '
                  + '<i style="color:#94a3b8">📖 ' + (primary.citation || 'CC') + '</i>';
      }} else {{
        verdict = '👁 WATCH';  vcolor = '#a78bfa';
        dir = 'long';  dirArrow = '·';  dirColor = '#94a3b8';
        setupName = 'Snapshot — no setup firing right now';
        entry = '—'; stop = '—'; targets = '—'; rr = '—'; conv = '—';
        planHtml = '⏳ <b>No setup firing yet.</b> Levels computed (EMA 55 $'
                 + (j.ema_55 ? j.ema_55.toFixed(2) : '—')
                 + ', EMA 200 $' + (j.ema_200 ? j.ema_200.toFixed(2) : '—')
                 + ', RSI ' + (j.rsi_14 ? j.rsi_14.toFixed(1) : '—')
                 + '). Open chart for full CC analysis.';
      }}
      var row = document.createElement('tr');
      row.className = 'setup-row row-' + verdict.toLowerCase().replace(/[\\s👁]/g, '').trim() + ' dir-' + dir;
      row.setAttribute('data-symbol', sym);
      row.setAttribute('data-verdict', verdict.toLowerCase().replace(/[\\s👁]/g, '').trim());
      row.setAttribute('data-direction', dir);
      row.innerHTML =
        '<td class="actions"><button class="bell-btn" data-symbol="' + sym
          + '" data-price="' + px + '" onclick="setAlarm(event,\\'' + sym + '\\',' + px + ')" title="Price alarm">🔔</button></td>'
        + '<td><span class="verdict-pill" style="background:' + vcolor + ';color:#000">' + verdict + '</span></td>'
        + '<td><b><a class="sym-link" href="/chart?symbol=' + sym + '" target="_blank" rel="noopener">' + sym + '</a></b></td>'
        + '<td><a class="open-chart-mini" href="/chart?symbol=' + sym + '" target="_blank" rel="noopener">📊 Open</a></td>'
        + '<td style="color:' + dirColor + '">' + dirArrow + ' ' + setupName + '</td>'
        + '<td style="text-align:right">$' + px + '</td>'
        + '<td style="text-align:right">' + entry + '</td>'
        + '<td style="text-align:right;color:#ef4444">' + stop + '</td>'
        + '<td style="text-align:right;color:#22c55e">' + targets + '</td>'
        + '<td style="text-align:right">' + rr + '</td>'
        + '<td style="text-align:right">' + conv + '</td>'
        + '<td style="font-size:11px;color:#cbd5e1;line-height:1.45">' + planHtml + '</td>';
      // Remove any existing row for this same symbol (replace, don't dupe)
      var dup = tbody.querySelector('tr[data-symbol="' + sym + '"]');
      if (dup) dup.remove();
      // Highlight briefly so the operator sees where it landed
      row.style.background = 'rgba(167, 139, 250, 0.18)';
      row.style.transition = 'background 4s ease';
      tbody.insertBefore(row, tbody.firstChild);
      setTimeout(function() {{ row.style.background = ''; }}, 100);
    }}

    function handleScanSubmit(ev) {{
      if (ev && ev.preventDefault) ev.preventDefault();
      var input = document.getElementById('search-input');
      if (!input) return false;
      var raw = (input.value || '').trim();
      if (!raw) return false;
      var stars = getStars();
      var added = [];
      raw.split(',').map(function(x) {{ return x.trim(); }}).filter(Boolean).forEach(function(t) {{
        var sym = t.toUpperCase();
        if (sym && stars.indexOf(sym) < 0) {{ stars.push(sym); added.push(sym); }}
      }});
      saveStars(stars);
      syncWatchlistToBackend();
      renderWatchlistsUI();  // Wave 23 — refresh chips so the new ticker shows up.
      input.value = '';
      if (!added.length) {{
        showToast('⭐ Already in active list');
        return false;
      }}
      var d = getWatchlists();
      showToast('⚡ Added ' + added.join(', ') + ' to "' + d.active + '" — scanning…');
      // Wave 22 — Fetch /api/scan-now for each new ticker and INJECT
      // the resulting row into the table immediately. No reload, no
      // waiting for the 5-min background cycle.
      added.forEach(function(sym) {{
        fetch('/api/scan-now?symbol=' + encodeURIComponent(sym) + '&t=' + Date.now())
          .then(function(r) {{ return r.json(); }})
          .then(function(j) {{
            if (j.error) {{
              showToast('⚠ ' + sym + ': ' + j.error);
              return;
            }}
            _injectScanRow(j);
            var msg = (j.setups_count > 0)
              ? ('🎯 ' + sym + ' — ' + j.setups_count + ' setup(s) firing!')
              : ('📊 ' + sym + ' — snapshot ready (no setup firing yet)');
            showToast(msg);
          }})
          .catch(function(err) {{
            console.error('[CC] scan-now failed for ' + sym, err);
            showToast('⚠ ' + sym + ' scan failed');
          }});
      }});
      return false;
    }}

    // Wave 20 — These watchlist functions are kept as no-ops for backwards
    // compatibility (in case any inline-onclick attributes elsewhere call
    // them). The real entry point is now handleScanSubmit() on the search
    // form. Operator-level concept: 'one scan universe, one list'.
    function addToMyList() {{ /* no-op since Wave 20 — use search bar instead */ }}
    function removeFromMyList(sym) {{ /* no-op since Wave 20 */ }}
    function clearMyList() {{ /* no-op since Wave 20 */ }}
    function scanMyList() {{ /* no-op since Wave 20 */ }}

    function setAlarm(ev, sym, currentPrice) {{
      ev.stopPropagation();
      const target = prompt(`Alert when ${{sym}} crosses price level:\\n(current: $${{currentPrice}})`, currentPrice.toFixed(2));
      if (!target) return;
      const level = parseFloat(target);
      if (isNaN(level)) return alert('Invalid price');
      const alarms = getAlarms();
      const direction = level > currentPrice ? 'above' : 'below';
      alarms.push({{symbol: sym, level: level, direction: direction, set_at: Date.now(), set_price: currentPrice}});
      saveAlarms(alarms);
      applyBellUI();
      if (Notification.permission !== 'granted') Notification.requestPermission();
      showToast(`🔔 Alarm set: ${{sym}} ${{direction}} $${{level.toFixed(2)}}`);
    }}

    function clearAlarm(sym) {{
      saveAlarms(getAlarms().filter(a => a.symbol !== sym));
      applyBellUI();
    }}

    function applyStarUI() {{
      const s = getStars();
      document.querySelectorAll('.star-btn').forEach(btn => {{
        const on = s.includes(btn.dataset.symbol);
        btn.textContent = on ? '⭐' : '☆';
        btn.classList.toggle('on', on);
      }});
    }}

    function applyBellUI() {{
      const a = getAlarms();
      document.querySelectorAll('.bell-btn').forEach(btn => {{
        const on = a.some(x => x.symbol === btn.dataset.symbol);
        btn.classList.toggle('on', on);
        btn.title = on ? a.filter(x=>x.symbol===btn.dataset.symbol).map(x=>`${{x.direction}} $${{x.level}}`).join(', ') : 'Set price alarm';
      }});
    }}

    let currentFilter = 'all';
    function applyFilter() {{
      document.querySelectorAll('.setup-row').forEach(tr => {{
        const verdict = tr.dataset.verdict;
        const dir = tr.dataset.direction;
        let show = true;
        if (currentFilter === 'strong-take')      show = verdict === 'strong-take';
        else if (currentFilter === 'take')        show = verdict === 'take' || verdict === 'strong-take';
        else if (currentFilter === 'watch')       show = verdict === 'watch' || verdict === 'strong-take' || verdict === 'take';
        else if (currentFilter === 'long')        show = dir === 'long';
        else if (currentFilter === 'short')       show = dir === 'short';
        tr.style.display = show ? '' : 'none';
      }});
    }}

    document.querySelectorAll('.filter-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter = btn.dataset.filter;
        applyFilter();
      }});
    }});

    function checkAlarms() {{
      const fired = [];
      const remaining = [];
      getAlarms().forEach(a => {{
        const cur = window.cc_prices[a.symbol];
        if (cur === undefined) {{ remaining.push(a); return; }}
        const hit = (a.direction === 'above' && cur >= a.level) || (a.direction === 'below' && cur <= a.level);
        if (hit) fired.push({{...a, current: cur}});
        else remaining.push(a);
      }});
      if (fired.length) {{
        saveAlarms(remaining);
        fired.forEach(a => {{
          const msg = `🔔 ${{a.symbol}} crossed ${{a.direction}} $${{a.level}}  (now $${{a.current}})`;
          if (Notification.permission === 'granted') {{
            try {{ new Notification('CC Alert: ' + a.symbol, {{ body: msg, requireInteraction: true }}); }} catch(_) {{}}
          }}
          showToast(msg);
        }});
        applyBellUI();
      }}
    }}

    function showToast(msg) {{
      const t = document.createElement('div');
      t.className = 'alarm-toast';
      t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(() => t.remove(), 8000);
    }}

    // Restore search input from URL ?symbols=
    (function() {{
      const params = new URLSearchParams(window.location.search);
      const s = params.get('symbols');
      if (s) document.getElementById('search-input').value = s;
    }})();

    // --- Position sizer ---------------------------------------------------
    function onSizerChange() {{
      var acct = parseFloat(document.getElementById('acct-size').value) || 0;
      var pct  = parseFloat(document.getElementById('risk-pct').value)  || 0;
      var risk = acct * (pct / 100.0);
      document.getElementById('risk-dollars').textContent = risk.toFixed(2);
      localStorage.setItem('cc_acct', JSON.stringify({{acct: acct, pct: pct}}));
    }}
    function sizeTrade(sym, entry, stop) {{
      var acct = parseFloat(document.getElementById('acct-size').value) || 0;
      var pct  = parseFloat(document.getElementById('risk-pct').value)  || 0;
      var riskDollars = acct * (pct / 100.0);
      var perShare = Math.abs(entry - stop);
      if (perShare <= 0) return alert('Risk per share is zero — check entry vs stop.');
      var shares = Math.floor(riskDollars / perShare);
      var notional = shares * entry;
      alert(
        sym + ' position size\\n\\n' +
        'Account:        $' + acct.toFixed(2) + '\\n' +
        'Risk:           ' + pct + '%  =  $' + riskDollars.toFixed(2) + '\\n' +
        'Per-share risk: $' + perShare.toFixed(4) + '\\n' +
        'Shares:         ' + shares + '\\n' +
        'Notional:       $' + notional.toFixed(2) + '\\n' +
        'Leverage vs acct: ' + (acct > 0 ? (notional / acct).toFixed(2) + 'x' : '—')
      );
    }}

    // --- Trade journal ---------------------------------------------------
    function getJournal() {{
      try {{ return JSON.parse(localStorage.getItem('cc_journal') || '[]'); }} catch(_) {{ return []; }}
    }}
    function saveJournal(j) {{ localStorage.setItem('cc_journal', JSON.stringify(j)); }}

    function takeTrade(sym, name, dir, entry, stop, t1, t2) {{
      var acct = parseFloat(document.getElementById('acct-size').value) || 0;
      var pct  = parseFloat(document.getElementById('risk-pct').value)  || 0;
      var riskDollars = acct * (pct / 100.0);
      var perShare = Math.abs(entry - stop);
      var shares = perShare > 0 ? Math.floor(riskDollars / perShare) : 0;
      var j = getJournal();
      j.unshift({{
        id: Date.now(),
        date: new Date().toISOString().slice(0,10),
        symbol: sym, name: name, direction: dir,
        entry: entry, stop: stop, t1: t1, t2: t2,
        shares: shares, risk_dollars: riskDollars,
        status: 'open', exit: null, r_outcome: null, notes: ''
      }});
      saveJournal(j);
      renderJournal();
      showToast('▶ Trade logged: ' + sym + ' ' + dir + ' ' + shares + ' shares');
    }}
    function passTrade(sym, name) {{
      var note = prompt('Why are you passing on ' + sym + ' — ' + name + '? (optional)') || '';
      var j = getJournal();
      j.unshift({{
        id: Date.now(),
        date: new Date().toISOString().slice(0,10),
        symbol: sym, name: name, direction: '—',
        entry: null, stop: null, t1: null, t2: null,
        shares: 0, risk_dollars: 0,
        status: 'passed', exit: null, r_outcome: null, notes: note
      }});
      saveJournal(j);
      renderJournal();
      showToast('⏭ Passed on ' + sym);
    }}
    function closeTrade(id) {{
      var exitStr = prompt('Exit price?');
      if (!exitStr) return;
      var exit = parseFloat(exitStr);
      if (isNaN(exit)) return alert('Invalid price');
      var j = getJournal();
      var t = j.find(x => x.id === id);
      if (!t) return;
      var risk = Math.abs(t.entry - t.stop);
      var pnl = t.direction === 'long' ? (exit - t.entry) : (t.entry - exit);
      var r = risk > 0 ? pnl / risk : 0;
      t.status = 'closed';
      t.exit = exit;
      t.r_outcome = r;
      saveJournal(j);
      renderJournal();
      showToast((r >= 0 ? '✓ ' : '✗ ') + t.symbol + '  ' + r.toFixed(2) + 'R');
    }}
    function deleteTrade(id) {{
      if (!confirm('Delete this journal entry?')) return;
      saveJournal(getJournal().filter(x => x.id !== id));
      renderJournal();
    }}
    function renderJournal() {{
      var j = getJournal();
      var box = document.getElementById('journal-rows');
      var stats = document.getElementById('journal-stats');
      if (!box) return;
      if (!j.length) {{
        box.innerHTML = '<div class="journal-empty">No trades logged yet. Click ▶ Take or ⏭ Pass on any setup to start.</div>';
        if (stats) stats.textContent = 'no trades yet';
        return;
      }}
      var header = '<div class="journal-row header">'
        + '<div>Date</div><div>Symbol</div><div>Dir</div><div>Entry</div><div>Stop</div><div>Exit</div><div>Setup · Notes</div><div>R</div></div>';
      var rows = j.map(t => {{
        var rClass = t.r_outcome === null ? '' : (t.r_outcome >= 0 ? 'r-win' : 'r-loss');
        var rText = t.r_outcome === null ? (t.status === 'open' ? `<button class="tools-btn" onclick="closeTrade(${{t.id}})" style="padding:2px 6px;font-size:10px">Close</button>` : '—')
                                          : t.r_outcome.toFixed(2) + 'R';
        return '<div class="journal-row">'
          + '<div>' + t.date + '</div>'
          + '<div><b>' + t.symbol + '</b></div>'
          + '<div>' + (t.direction || '—') + '</div>'
          + '<div>' + (t.entry !== null ? '$' + t.entry.toFixed(2) : '—') + '</div>'
          + '<div>' + (t.stop  !== null ? '$' + t.stop.toFixed(2)  : '—') + '</div>'
          + '<div>' + (t.exit  !== null ? '$' + t.exit.toFixed(2)  : '—') + '</div>'
          + '<div style="color:#94a3b8">' + (t.name || '') + (t.notes ? ' — <i>' + t.notes + '</i>' : '') + '</div>'
          + '<div class="' + rClass + '">' + rText + ' <span class="x" style="color:#64748b;cursor:pointer" onclick="deleteTrade(' + t.id + ')">✕</span></div>'
          + '</div>';
      }}).join('');
      box.innerHTML = header + rows;
      var closed = j.filter(t => t.status === 'closed' && t.r_outcome !== null);
      if (closed.length && stats) {{
        var wins = closed.filter(t => t.r_outcome > 0).length;
        var sumR = closed.reduce((a,t) => a + t.r_outcome, 0);
        stats.textContent =
          closed.length + ' closed · ' + wins + ' wins · ' +
          'win-rate ' + (100 * wins / closed.length).toFixed(0) + '% · ' +
          'expectancy ' + (sumR / closed.length).toFixed(2) + 'R · ' +
          'total ' + sumR.toFixed(2) + 'R';
      }} else if (stats) {{
        stats.textContent = j.length + ' logged · 0 closed yet';
      }}
    }}
    function exportJournal() {{
      var j = getJournal();
      if (!j.length) return alert('Journal is empty');
      var headers = ['date','symbol','name','direction','entry','stop','t1','t2','shares','risk_dollars','status','exit','r_outcome','notes'];
      var lines = [headers.join(',')];
      j.forEach(t => {{
        lines.push(headers.map(h => {{
          var v = t[h];
          if (v === null || v === undefined) return '';
          if (typeof v === 'string' && v.includes(',')) return '"' + v.replace(/"/g,'""') + '"';
          return v;
        }}).join(','));
      }});
      var blob = new Blob([lines.join('\\n')], {{type:'text/csv'}});
      var url  = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = 'cc-trader-journal-' + new Date().toISOString().slice(0,10) + '.csv';
      a.click();
      URL.revokeObjectURL(url);
    }}

    // --- "+ List" helper — add ticker to watchlist directly (no prompt) ----
    function addToMyListBySymbol(ev, sym) {{
      ev.stopPropagation();
      var stars = getStars();
      sym = (sym || '').toUpperCase();
      if (!sym) return;
      var isNew = stars.indexOf(sym) < 0;
      if (isNew) stars.push(sym);
      saveStars(stars);
      applyStarUI();
      renderMyListBar();
      renderMonitorTable();
      showToast('⭐ ' + sym + ' added to your watchlist');
      // Wave 15 — persist + immediate scan
      if (isNew) {{
        syncWatchlistToBackend();
        triggerImmediateScan(sym);
      }}
    }}

    // --- Manual setup modal ---------------------------------------------
    function openManualSetupModal(prefillSymbol, prefillPrice) {{
      var modal = document.getElementById('manual-modal');
      if (!modal) return;
      modal.style.display = 'flex';
      // Reset / prefill
      document.getElementById('ms-symbol').value = prefillSymbol || '';
      document.getElementById('ms-name').value = 'Manual setup';
      document.getElementById('ms-notes').value = '';
      var entry = document.getElementById('ms-entry');
      var stop  = document.getElementById('ms-stop');
      var t1    = document.getElementById('ms-t1');
      var t2    = document.getElementById('ms-t2');
      entry.value = prefillPrice ? prefillPrice : '';
      stop.value = '';
      t1.value = '';
      t2.value = '';
      // Live preview of R:R as user types
      [entry, stop, t1, t2].forEach(el => {{
        el.oninput = updateManualPreview;
      }});
      updateManualPreview();
      document.getElementById('ms-symbol').focus();
    }}
    function closeManualSetupModal() {{
      var modal = document.getElementById('manual-modal');
      if (modal) modal.style.display = 'none';
    }}
    function updateManualPreview() {{
      var entry = parseFloat(document.getElementById('ms-entry').value);
      var stop  = parseFloat(document.getElementById('ms-stop').value);
      var t1    = parseFloat(document.getElementById('ms-t1').value);
      var dir   = document.querySelector('input[name="ms-dir"]:checked').value;
      var preview = document.getElementById('ms-preview');
      if (isNaN(entry) || isNaN(stop) || isNaN(t1)) {{
        preview.textContent = 'Fill in entry + stop + target1 to see R:R preview';
        preview.style.borderLeftColor = '#fbbf24';
        return;
      }}
      var risk = Math.abs(entry - stop);
      var reward = Math.abs(t1 - entry);
      var rr = risk > 0 ? (reward / risk) : 0;
      var movePct = entry > 0 ? (reward / entry * 100) : 0;
      // Sanity check direction makes sense
      var sane = (dir === 'long' && stop < entry && t1 > entry) ||
                 (dir === 'short' && stop > entry && t1 < entry);
      if (!sane) {{
        preview.innerHTML = '⚠ Direction does not match levels.<br>'
          + 'Long: stop &lt; entry &lt; target.   Short: target &lt; entry &lt; stop.';
        preview.style.borderLeftColor = '#ef4444';
        return;
      }}
      var rrColor = rr >= 2 ? '#22c55e' : (rr >= 1.5 ? '#f59e0b' : '#ef4444');
      preview.innerHTML = 'R:R = <b style="color:' + rrColor + '">' + rr.toFixed(2) + 'R</b>'
        + '   |   Risk/share $' + risk.toFixed(2)
        + '   |   Reward to T1 $' + reward.toFixed(2) + ' (' + (dir === 'long' ? '+' : '-') + movePct.toFixed(1) + '%)';
      preview.style.borderLeftColor = rrColor;
    }}

    function getManualSetups() {{
      try {{ return JSON.parse(localStorage.getItem('cc_manual_setups') || '[]'); }} catch(_) {{ return []; }}
    }}
    function saveManualSetups(arr) {{ localStorage.setItem('cc_manual_setups', JSON.stringify(arr)); }}

    function saveManualSetup() {{
      var symbol = document.getElementById('ms-symbol').value.trim().toUpperCase();
      var name   = document.getElementById('ms-name').value.trim() || 'Manual setup';
      var dir    = document.querySelector('input[name="ms-dir"]:checked').value;
      var entry  = parseFloat(document.getElementById('ms-entry').value);
      var stop   = parseFloat(document.getElementById('ms-stop').value);
      var t1     = parseFloat(document.getElementById('ms-t1').value);
      var t2raw  = document.getElementById('ms-t2').value;
      var t2     = t2raw === '' ? null : parseFloat(t2raw);
      var notes  = document.getElementById('ms-notes').value.trim();

      if (!symbol)          return alert('Symbol is required');
      if (isNaN(entry))     return alert('Entry price is required');
      if (isNaN(stop))      return alert('Stop price is required');
      if (isNaN(t1))        return alert('Target 1 price is required');
      var sane = (dir === 'long' && stop < entry && t1 > entry) ||
                 (dir === 'short' && stop > entry && t1 < entry);
      if (!sane) {{
        if (!confirm('Stop/target placement looks inconsistent with ' + dir + ' direction. Save anyway?')) return;
      }}

      var setups = getManualSetups();
      setups.unshift({{
        id: Date.now(),
        created: new Date().toISOString(),
        symbol: symbol, name: name, direction: dir,
        entry: entry, stop: stop, t1: t1, t2: t2,
        notes: notes,
      }});
      saveManualSetups(setups);
      // Also star the ticker so it joins the watchlist
      var stars = getStars();
      if (stars.indexOf(symbol) < 0) {{
        stars.push(symbol);
        saveStars(stars);
        applyStarUI();
        renderMyListBar();
        renderMonitorTable();
      }}
      closeManualSetupModal();
      renderManualSetups();
      // Scroll to the new card
      setTimeout(function() {{
        document.getElementById('manual-section').scrollIntoView({{behavior:'smooth'}});
      }}, 50);
      showToast('💾 Saved manual setup: ' + symbol + ' ' + dir);
    }}
    function deleteManualSetup(id) {{
      if (!confirm('Delete this manual setup?')) return;
      saveManualSetups(getManualSetups().filter(x => x.id !== id));
      renderManualSetups();
    }}
    function editManualSetup(id) {{
      var s = getManualSetups().find(x => x.id === id);
      if (!s) return;
      // Open modal pre-filled, and replace save handler to update-in-place
      openManualSetupModal(s.symbol, s.entry);
      document.getElementById('ms-name').value = s.name;
      document.getElementById('ms-stop').value = s.stop;
      document.getElementById('ms-t1').value = s.t1;
      document.getElementById('ms-t2').value = s.t2 === null ? '' : s.t2;
      document.getElementById('ms-notes').value = s.notes || '';
      document.querySelectorAll('input[name="ms-dir"]').forEach(r => r.checked = (r.value === s.direction));
      updateManualPreview();
      // Replace Save button behavior — delete old then save new
      var saveBtn = document.querySelector('.ms-save');
      saveBtn.onclick = function() {{
        saveManualSetups(getManualSetups().filter(x => x.id !== id));
        saveManualSetup();
        saveBtn.onclick = null;   // restore default
      }};
    }}

    function renderManualSetups() {{
      var setups = getManualSetups();
      var box = document.getElementById('manual-cards');
      var label = document.getElementById('manual-count');
      if (!box) return;
      if (label) label.textContent = setups.length === 0 ? '(empty)' : '(' + setups.length + ')';
      if (!setups.length) {{
        box.innerHTML = '<div style="color:#64748b;padding:14px;font-size:12px">No manual setups yet. Click <b>✎ Add manual setup</b> above to create one, or use ✎ Setup button on any snapshot card.</div>';
        return;
      }}
      box.innerHTML = setups.map(s => {{
        var dirColor = s.direction === 'long' ? '#22c55e' : '#ef4444';
        var arrow = s.direction === 'long' ? '▲' : '▼';
        var risk = Math.abs(s.entry - s.stop);
        var rr = risk > 0 ? Math.abs(s.t1 - s.entry) / risk : 0;
        var move = s.entry > 0 ? Math.abs(s.t1 - s.entry) / s.entry * 100 : 0;
        var t2html = s.t2 !== null && !isNaN(s.t2) ?
          '<div><span class="lbl">Target 2</span><span class="val" style="color:#22c55e">$' + s.t2.toFixed(2) + '</span></div>' : '';
        return ''
          + '<div class="manual-card">'
          + '  <div class="manual-head">'
          + '    <div class="mh-left">'
          + '      <span class="manual-pill">MANUAL</span>'
          + '      <span style="color:' + dirColor + ';font-weight:700">' + arrow + ' ' + s.direction.toUpperCase() + '</span>'
          + '      <b>' + s.symbol + '</b>'
          + '      <span style="color:#94a3b8;font-size:12px">· ' + s.name + '</span>'
          + '    </div>'
          + '    <div style="color:#64748b;font-size:10px">' + (s.created || '').slice(0, 10) + '</div>'
          + '  </div>'
          + '  <div class="manual-grid">'
          + '    <div><span class="lbl">Entry</span><span class="val">$' + s.entry.toFixed(2) + '</span></div>'
          + '    <div><span class="lbl">Stop</span><span class="val" style="color:#ef4444">$' + s.stop.toFixed(2) + '</span></div>'
          + '    <div><span class="lbl">Target 1</span><span class="val" style="color:#22c55e">$' + s.t1.toFixed(2) + '</span></div>'
          +      t2html
          + '    <div><span class="lbl">R:R</span><span class="val" style="color:' + (rr >= 2 ? '#22c55e' : '#f59e0b') + '">' + rr.toFixed(2) + 'R</span></div>'
          + '    <div><span class="lbl">Move</span><span class="val">' + (s.direction === 'long' ? '+' : '-') + move.toFixed(1) + '%</span></div>'
          + '  </div>'
          + (s.notes ? '<div class="manual-notes">📝 ' + s.notes + '</div>' : '')
          + '  <div class="manual-actions">'
          + '    <button onclick="sizeTrade(\\'' + s.symbol + '\\',' + s.entry + ',' + s.stop + ')">📐 Size this</button>'
          + '    <button class="take-btn" onclick="takeTrade(\\'' + s.symbol + '\\',\\'' + s.name.replace(/[\\\\\\\\\\\\\\']/g, '') + ' (manual)\\',\\''+s.direction+'\\','+s.entry+','+s.stop+','+s.t1+','+(s.t2||0)+')">▶ Take</button>'
          + '    <button onclick="editManualSetup(' + s.id + ')">✎ Edit</button>'
          + '    <button class="del-btn" onclick="deleteManualSetup(' + s.id + ')" title="Delete">✕</button>'
          + '  </div>'
          + '</div>';
      }}).join('');
    }}

    // Close modal with ESC
    document.addEventListener('keydown', function(e) {{
      if (e.key === 'Escape') closeManualSetupModal();
    }});

    function loadSavedAccount() {{
      try {{
        var s = JSON.parse(localStorage.getItem('cc_acct') || '{{}}');
        if (s.acct) document.getElementById('acct-size').value = s.acct;
        if (s.pct)  document.getElementById('risk-pct').value  = s.pct;
      }} catch(_) {{}}
      onSizerChange();
    }}

    // -------- View toggle (CC LWC vs TradingView widget) --------------------
    window.cc_tv_loaded = window.cc_tv_loaded || {{}};

    function loadTradingViewWidget(targetId, symbol) {{
      if (window.cc_tv_loaded[targetId]) return;
      var hostEl = document.getElementById('tv_host_' + targetId);
      if (!hostEl) return;
      if (typeof TradingView === 'undefined') {{
        hostEl.innerHTML = '<div style="padding:30px;color:#64748b">TradingView library not loaded yet — refresh in a few seconds.</div>';
        return;
      }}
      // Empty the host first (in case of re-mount)
      hostEl.innerHTML = '<div id="tv_inner_' + targetId + '"></div>';
      try {{
        new TradingView.widget({{
          container_id: 'tv_inner_' + targetId,
          autosize: true,
          symbol: symbol,
          interval: 'D',
          timezone: 'America/New_York',
          theme: 'dark',
          style: '1',
          locale: 'en',
          toolbar_bg: '#0a0f1c',
          enable_publishing: false,
          hide_top_toolbar: false,
          hide_legend: false,
          save_image: false,
          allow_symbol_change: false,
          withdateranges: true,
          studies: [
            'MAExp@tv-basicstudies', 'MAExp@tv-basicstudies', 'MAExp@tv-basicstudies',
            'RSI@tv-basicstudies', 'Volume@tv-basicstudies',
          ],
          studies_overrides: {{
            'moving average exponential.length': 55,
          }},
          drawings_access: {{ type: 'rectangle' }},
          // Drawing tools available by default in the widget — no extra config needed
        }});
        window.cc_tv_loaded[targetId] = true;
      }} catch(e) {{
        hostEl.innerHTML = '<div style="padding:30px;color:#ef4444">TradingView widget failed: ' + e.message + '</div>';
      }}
    }}

    // Wire up view-toggle buttons
    function _bindViewToggles() {{
      document.querySelectorAll('.view-btn').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
          var target = btn.dataset.target;
          var view = btn.dataset.view;
          var host = btn.closest('.chart-host');
          if (!host) return;
          // Highlight active button
          host.querySelectorAll('.view-btn').forEach(function(b) {{ b.classList.remove('active'); }});
          btn.classList.add('active');
          // Toggle visibility of CC vs TV containers
          host.querySelectorAll('.view-cc, .view-tv').forEach(function(v) {{
            v.style.display = 'none';
          }});
          if (view === 'cc') {{
            var ccDiv = host.querySelector('.view-cc[data-view-id="' + target + '"]');
            if (ccDiv) ccDiv.style.display = '';
          }} else if (view === 'tv') {{
            var tvDiv = host.querySelector('.view-tv[data-view-id="' + target + '"]');
            if (tvDiv) {{
              tvDiv.style.display = '';
              var sym = tvDiv.dataset.tvSymbol;
              loadTradingViewWidget(target, sym);
            }}
          }}
        }});
      }});
    }}

    // -------- User annotations on LWC chart -----------------------
    function getAnnotations(sym) {{
      try {{ return (JSON.parse(localStorage.getItem('cc_annotations') || '{{}}'))[sym] || []; }}
      catch(_) {{ return []; }}
    }}
    function saveAnnotations(sym, arr) {{
      var all;
      try {{ all = JSON.parse(localStorage.getItem('cc_annotations') || '{{}}'); }} catch(_) {{ all = {{}}; }}
      all[sym] = arr;
      localStorage.setItem('cc_annotations', JSON.stringify(all));
    }}

    function addAnnotation(sym, chartId, kind) {{
      var priceStr = prompt(kind === 'note'
        ? 'Add a note — price level:'
        : 'Add a horizontal line at price:');
      if (!priceStr) return;
      var price = parseFloat(priceStr);
      if (isNaN(price)) return alert('Invalid price');
      var text = kind === 'note'
        ? (prompt('Note text (optional):') || '')
        : '';
      var color = kind === 'note' ? '#fbbf24' : '#22d3ee';
      var arr = getAnnotations(sym);
      arr.push({{
        id: Date.now(), kind: kind, price: price, text: text, color: color,
      }});
      saveAnnotations(sym, arr);
      applyAnnotationsToChart(sym, chartId);
      showToast('✏ Added ' + (kind === 'note' ? 'note' : 'line') + ' at $' + price.toFixed(2));
    }}

    function clearAnnotations(sym, chartId) {{
      if (!confirm('Remove all YOUR drawings for ' + sym + '? (scanner levels remain)')) return;
      saveAnnotations(sym, []);
      applyAnnotationsToChart(sym, chartId);
    }}

    function applyAnnotationsToChart(sym, chartId) {{
      // The LWC chart handle was stored by initLightweightCharts under
      // window.cc_chart_handles. Find the matching one by symbol.
      var h = null;
      for (var k in window.cc_chart_handles) {{
        var entry = window.cc_chart_handles[k];
        // The chart's div has data-symbol=sym; the handle has the chart instance
        var divEl = document.getElementById(k);
        if (divEl && divEl.getAttribute('data-symbol') === sym) {{
          h = entry; break;
        }}
      }}
      if (!h) return;
      // Remove previous user lines
      if (h.userLineHandles) {{
        h.userLineHandles.forEach(function(pl) {{
          try {{ h.candleSeries.removePriceLine(pl); }} catch(_) {{}}
        }});
      }}
      // Add fresh user annotations as price lines
      var annos = getAnnotations(sym);
      h.userLineHandles = annos.map(function(a) {{
        return h.candleSeries.createPriceLine({{
          price: a.price,
          color: a.color || '#fbbf24',
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: '👤 ' + (a.text ? a.text + ' · ' : '') + '$' + a.price.toFixed(2),
        }});
      }});
    }}

    function applyAllAnnotationsOnLoad() {{
      // After charts init, walk every chart-host div and re-apply its saved annotations
      document.querySelectorAll('.chart-host').forEach(function(host) {{
        var sym = host.dataset.symbol;
        var chartIdx = host.dataset.chartIdx;
        applyAnnotationsToChart(sym, chartIdx);
      }});
    }}

    // -------- 24h countdown badge -----------------------
    function updateCountdownBadges() {{
      var now = new Date();
      // For US stocks: next 4:00 PM ET (= 21:00 UTC during standard time, 20:00 UTC daylight savings).
      // We'll use 20:00 UTC as a reasonable approximation (active session close).
      // For crypto tickers (ending -USD): next 00:00 UTC.
      document.querySelectorAll('.countdown-badge').forEach(function(badge) {{
        var host = badge.closest('.chart-host');
        if (!host) return;
        var sym = host.dataset.symbol;
        var isCrypto = sym && sym.indexOf('-USD') >= 0;
        var target = new Date(now);
        if (isCrypto) {{
          target.setUTCHours(24, 0, 0, 0);
        }} else {{
          // Roughly NYSE close — 16:00 ET = 20:00 UTC (DST) or 21:00 UTC (standard).
          // We'll target 20:00 UTC and skip weekends.
          target.setUTCHours(20, 0, 0, 0);
          if (target <= now) target.setUTCDate(target.getUTCDate() + 1);
          // Skip Saturday/Sunday
          while (target.getUTCDay() === 6 || target.getUTCDay() === 0) {{
            target.setUTCDate(target.getUTCDate() + 1);
          }}
        }}
        var diffMs = target - now;
        if (diffMs < 0) diffMs = 0;
        var totalMin = Math.floor(diffMs / 60000);
        var hours = Math.floor(totalMin / 60);
        var mins = totalMin % 60;
        var label = isCrypto ? 'next UTC close' : 'next NYSE close';
        badge.textContent = '⏱ ' + label + ': ' + hours + 'h ' + (mins < 10 ? '0' : '') + mins + 'm';
        badge.title = 'Target: ' + target.toUTCString();
      }});
    }}

    window.addEventListener('load', () => {{
      applyStarUI();
      applyBellUI();
      applyFilter();
      checkAlarms();
      renderMyListBar();
      renderMonitorTable();
      initLightweightCharts();
      _bindViewToggles();
      applyAllAnnotationsOnLoad();
      updateCountdownBadges();
      setInterval(updateCountdownBadges, 60000);   // refresh countdown every minute
      loadSavedAccount();
      renderJournal();
      renderManualSetups();
      if (Notification.permission === 'default') Notification.requestPermission();
      // Wave 15 — sync localStorage → backend on every page load. Render's
      // disk is ephemeral so this ensures the backend always has the user's
      // current watchlist (auto-recovers after redeploys).
      if (typeof syncWatchlistToBackend === 'function') syncWatchlistToBackend();
      // Wave 23 — Hydrate the multi-watchlist UI. Fetch backend state and
      // merge with localStorage (backend is source of truth for lists +
      // active selection; localStorage is the cache for offline reads).
      fetch('/api/watchlists').then(function(r) {{ return r.json(); }}).then(function(server) {{
        if (server && server.lists && Object.keys(server.lists).length) {{
          saveWatchlists(server);
        }}
        renderWatchlistsUI();
      }}).catch(function() {{ renderWatchlistsUI(); }});
    }});
  </script>
</body></html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
MIN_RISK_REWARD = 2.0  # CC discipline + pro convention: skip anything below 2:1


# ---------------------------------------------------------------------------
# Macro event calendar — hardcoded high-impact dates so we can warn users
# when a setup fires within 24h of a release. Easier to maintain than scraping
# a calendar API for a single-operator tool. Update each year.
# ---------------------------------------------------------------------------
MACRO_EVENTS_2026: list[tuple[str, str]] = [
    # FOMC meeting dates 2026 (announcement at 2pm ET, presser at 2:30pm)
    ("2026-01-28", "FOMC rate decision"),
    ("2026-03-18", "FOMC rate decision"),
    ("2026-04-29", "FOMC rate decision"),
    ("2026-06-17", "FOMC rate decision"),
    ("2026-07-29", "FOMC rate decision"),
    ("2026-09-16", "FOMC rate decision"),
    ("2026-10-28", "FOMC rate decision"),
    ("2026-12-09", "FOMC rate decision"),
    # CPI releases (~8:30am ET, mid-month)
    ("2026-01-14", "CPI release"), ("2026-02-11", "CPI release"),
    ("2026-03-11", "CPI release"), ("2026-04-15", "CPI release"),
    ("2026-05-13", "CPI release"), ("2026-06-10", "CPI release"),
    ("2026-07-15", "CPI release"), ("2026-08-12", "CPI release"),
    ("2026-09-09", "CPI release"), ("2026-10-14", "CPI release"),
    ("2026-11-13", "CPI release"), ("2026-12-10", "CPI release"),
    # NFP (first Friday of the month)
    ("2026-01-02", "Non-Farm Payrolls"), ("2026-02-06", "Non-Farm Payrolls"),
    ("2026-03-06", "Non-Farm Payrolls"), ("2026-04-03", "Non-Farm Payrolls"),
    ("2026-05-01", "Non-Farm Payrolls"), ("2026-06-05", "Non-Farm Payrolls"),
    ("2026-07-02", "Non-Farm Payrolls"), ("2026-08-07", "Non-Farm Payrolls"),
    ("2026-09-04", "Non-Farm Payrolls"), ("2026-10-02", "Non-Farm Payrolls"),
    ("2026-11-06", "Non-Farm Payrolls"), ("2026-12-04", "Non-Farm Payrolls"),
]


def upcoming_macro_within(days_ahead: int = 1) -> Optional[tuple[str, str]]:
    """Return (date_str, name) for the soonest macro event within `days_ahead`,
    or None if nothing is scheduled. Used to flag setups taken just before a
    high-impact release."""
    today = date.today()
    soon = today + timedelta(days=days_ahead)
    for d_str, name in MACRO_EVENTS_2026:
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if today <= d <= soon:
            return (d_str, name)
    return None


# ---------------------------------------------------------------------------
# Market regime — VIX level + breadth. Used to downgrade verdicts in
# high-volatility / weak-breadth regimes where setups historically misfire.
# ---------------------------------------------------------------------------
def fetch_market_regime() -> dict:
    """Return {vix_level, vix_regime, breadth_pct}. Cached for the scan."""
    out = {"vix_level": None, "vix_regime": "unknown", "breadth_pct": None}
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            v = yf.download("^VIX", period="5d", interval="1d",
                            auto_adjust=True, progress=False, threads=False)
        if v is not None and not v.empty:
            if isinstance(v.columns, pd.MultiIndex):
                v.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in v.columns]
            else:
                v.columns = [c.lower() for c in v.columns]
            vix = float(v["close"].iloc[-1])
            out["vix_level"] = vix
            if vix < 15:    out["vix_regime"] = "low-vol"
            elif vix < 25:  out["vix_regime"] = "normal"
            elif vix < 35:  out["vix_regime"] = "elevated"
            else:           out["vix_regime"] = "extreme"
    except Exception:
        pass
    # Breadth: use SPY trend as a coarse proxy for now. A full breadth calc
    # would scan all S&P 500 members and is too expensive for free-tier hosting.
    # We expose vix_regime; breadth_pct stays None until breadth scan is added.
    return out


def regime_adjusts_conviction(base: float, regime: dict) -> float:
    """Apply regime-based haircut to a setup's conviction.
    - 'extreme' VIX: -15% (most setups fail in 35+ VIX panic)
    - 'elevated':    -8%
    - 'normal':      no change
    - 'low-vol':     +3% (trends are smoother)"""
    r = regime.get("vix_regime", "unknown")
    if r == "extreme":  return max(0.10, base * 0.85)
    if r == "elevated": return max(0.20, base * 0.92)
    if r == "low-vol":  return min(0.95, base * 1.03)
    return base


# ---------------------------------------------------------------------------
# Correlation check — if multiple setups fire in the same sector, the
# operator should treat them as one trade, not N. Flags concentration.
# ---------------------------------------------------------------------------
def correlation_warning(setups: list["Setup"]) -> dict[str, int]:
    """Count setups per sector ETF. Sectors with ≥3 setups are concentration
    risk — operator should pick the best 1-2 and skip the rest."""
    by_sector: dict[str, int] = {}
    for s in setups:
        etf = SECTOR_ETF.get(s.symbol.upper(), "SPY")
        by_sector[etf] = by_sector.get(etf, 0) + 1
    return by_sector


# ---------------------------------------------------------------------------
# Backtest engine — walks each detector through historical bars and reports
# realized win-rate, expectancy in R, and max consecutive losses.
# Run with:  python scan_setups.py --backtest
# Writes results to backtest_results.json which gets picked up on next launch.
# ---------------------------------------------------------------------------
def _simulate_setup(setup: "Setup", future_df: pd.DataFrame, max_bars: int = 30) -> tuple[float, int]:
    """Replay a single setup against future bars. Returns (R_outcome, bars_held).
    R_outcome is positive if T1 was hit before stop, negative if stop hit first.
    Uses bar high/low against entry/stop/T1, not close — matches real fills."""
    risk = abs(setup.entry - setup.stop_loss)
    if risk <= 0:
        return (0.0, 0)
    long = setup.direction == "long"
    t1 = setup.targets[0] if setup.targets else (setup.entry + 2*risk if long else setup.entry - 2*risk)

    for i in range(min(len(future_df), max_bars)):
        bar = future_df.iloc[i]
        if long:
            # Stop hit first if low ≤ stop
            if bar["low"] <= setup.stop_loss:
                return (-1.0, i + 1)
            if bar["high"] >= t1:
                return (abs(t1 - setup.entry) / risk, i + 1)
        else:
            if bar["high"] >= setup.stop_loss:
                return (-1.0, i + 1)
            if bar["low"] <= t1:
                return (abs(setup.entry - t1) / risk, i + 1)
    # Time-stop at max_bars — exit at the close, compute outcome
    exit_px = float(future_df["close"].iloc[min(len(future_df)-1, max_bars-1)])
    pnl = (exit_px - setup.entry) if long else (setup.entry - exit_px)
    return (pnl / risk, min(len(future_df), max_bars))


def backtest_detector(name: str, detector_fn, tickers: list[str],
                      max_bars: int = 30, history_days: int = 730) -> dict:
    """Walk-forward backtest for one detector. For each ticker, slide a
    rolling window through history and replay the detector against each
    historical bar. Each fired setup is then replayed against the next
    max_bars to compute its R-outcome."""
    n_signals = 0
    wins = 0
    losses = 0
    r_outcomes: list[float] = []
    bars_held: list[int] = []
    for sym in tickers:
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                df = yf.download(sym, period=f"{history_days}d", interval="1d",
                                 auto_adjust=True, progress=False, threads=False)
            if df is None or df.empty or len(df) < 260:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df[["open","high","low","close","volume"]].dropna()
        except Exception:
            continue
        # Step through history. Detectors need ~220 bars of warmup.
        for end_idx in range(220, len(df) - max_bars, 1):
            past = df.iloc[:end_idx + 1]
            try:
                setup = detector_fn(sym, past)
            except Exception:
                setup = None
            if setup is None or setup.risk_reward < MIN_RISK_REWARD:
                continue
            future = df.iloc[end_idx + 1:]
            r, held = _simulate_setup(setup, future, max_bars=max_bars)
            n_signals += 1
            r_outcomes.append(r)
            bars_held.append(held)
            if r > 0:
                wins += 1
            else:
                losses += 1
    win_rate = wins / n_signals if n_signals else 0.0
    avg_r = (sum(r_outcomes) / n_signals) if n_signals else 0.0
    return {
        "name": name,
        "signals": n_signals,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "expectancy_R": avg_r,
        "avg_bars_held": (sum(bars_held) / n_signals) if n_signals else 0,
    }


def run_backtest(tickers: list[str]) -> dict:
    """Run all detectors through walk-forward backtest, print a summary, and
    persist the win-rates so future scans use real conviction numbers."""
    print("\n" + "=" * 70)
    print("  Walk-forward backtest — Chart Champions detectors")
    print(f"  Tickers: {len(tickers)} symbols, ~2 years of daily bars each")
    print("=" * 70)
    results = []
    detector_to_name = {
        detect_ema_pullback:         "EMA Pullback",
        detect_cc_region_pullback:   "CC Region",
        detect_sr_flip:              "S/R Flip",
        detect_volume_spike:         "Volume Spike",
        detect_inside_day:           "Inside Day",
        detect_rsi_reversal:         "RSI Reversal",
        detect_third_touch:          "3rd Touch",
        detect_trendline_break:      "Trendline Break",
        detect_orb_breakout:         "ORB",
        detect_bos:                  "BoS",
        detect_choch:                "ChoCh",
        detect_liquidity_grab:       "Liquidity Grab",
        detect_order_block_retest:   "Order Block",
        detect_fvg_fill:             "FVG",
        detect_wyckoff_spring:       "Wyckoff",
        detect_three_drives:         "Three Drives",
        detect_channel_break:        "Channel",
        detect_volume_profile_test:  "VolProfile",
        detect_bb_squeeze:           "BB Squeeze",
        detect_gap_play:             "Gap",
        detect_climax_bar:           "Climax",
        detect_double_top:           "Double Top",
        detect_double_bottom:        "Double Bottom",
        detect_head_and_shoulders:   "Head & Shoulders",
        detect_triangle:             "Triangle",
        detect_wedge:                "Wedge",
        detect_flag:                 "Flag",
        detect_cup_handle:           "Cup Handle",
        detect_abcd:                 "ABCD",
        detect_gartley:              "Gartley",
        detect_bat:                  "Bat",
        detect_butterfly:            "Butterfly",
        detect_crab:                 "Crab",
        detect_cypher:               "Cypher",
        detect_shark:                "Shark",
        detect_wolfe_wave:           "Wolfe",
        detect_breaker_block:        "Breaker",
        detect_premium_discount_ote: "OTE",
    }
    for fn, name in detector_to_name.items():
        print(f"\n  → backtesting: {name}...")
        r = backtest_detector(name, fn, tickers, max_bars=30, history_days=730)
        results.append(r)
        print(f"      signals={r['signals']:>4}   wins={r['wins']:>4}   losses={r['losses']:>4}   "
              f"win_rate={r['win_rate']*100:.1f}%   expectancy={r['expectancy_R']:+.2f}R   "
              f"avg_held={r['avg_bars_held']:.1f} bars")
    # Convert win-rate + expectancy → conviction in 0..1
    new_conviction: dict[str, float] = {}
    for r in results:
        if r["signals"] >= 20:
            # Blend win-rate (60%) and expectancy clipped (40%) for stability.
            conv = 0.60 * r["win_rate"] + 0.40 * max(0.0, min(1.0, 0.5 + r["expectancy_R"] / 2.0))
            new_conviction[r["name"]] = round(max(0.20, min(0.92, conv)), 3)
        else:
            # Not enough data — keep the prior
            new_conviction[r["name"]] = BACKTESTED_CONVICTION.get(r["name"], 0.55)
    summary = {
        "ran_at": datetime.utcnow().isoformat(),
        "n_tickers": len(tickers),
        "results": results,
        "conviction": new_conviction,
    }
    _BT_FILE.write_text(json.dumps(summary, indent=2, default=str))
    print("\n  ✓ Backtest complete. Updated conviction values:")
    for k, v in new_conviction.items():
        old = BACKTESTED_CONVICTION.get(k, 0)
        print(f"      {k:<18} {old:.2f}  →  {v:.2f}")
    print(f"\n  💾 Saved to {_BT_FILE.name} — next scan will use these.\n")
    BACKTESTED_CONVICTION.update(new_conviction)
    return summary


def _scan_index_trend(symbol: str) -> str:
    """Fetch one index/ETF and return its 50-day-EMA trend ('up'/'down'/'side')."""
    sym = symbol.strip().upper()
    if not _VALID_TICKER.match(sym):
        return "side"
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            df = yf.download(sym, period="1y", interval="1d",
                             auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            return "side"
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        # Compare close to 200-day SMA for market regime
        sma200 = df["close"].rolling(200).mean()
        if pd.isna(sma200.iloc[-1]):
            return _trend_from_df(df)
        if df["close"].iloc[-1] > sma200.iloc[-1]:
            return "up"
        if df["close"].iloc[-1] < sma200.iloc[-1]:
            return "down"
        return "side"
    except Exception:
        return "side"


# ---------------------------------------------------------------------------
# Wave 12 — single-ticker helpers used by /chart route + reused by run_full_scan.
# These are module-level so they can be called from anywhere (not closure-bound).
# ---------------------------------------------------------------------------
def serialize_chart_tf(d: pd.DataFrame, daily_format: bool) -> dict:
    """Serialize one OHLCV timeframe → dict for Lightweight Charts.
    daily_format=True → time as 'YYYY-MM-DD'; False → unix seconds (intraday).
    Includes EMA 8 / 21 / 55 / 100 / 200 as line series.
    """
    if d is None or d.empty:
        return {"candles": [], "volume": [],
                "ema_8": [], "ema_21": [],
                "ema_55": [], "ema_100": [], "ema_200": []}
    d = d.copy()
    if daily_format:
        times = [t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t) for t in d.index]
    else:
        times = [int(t.timestamp()) if hasattr(t, "timestamp") else 0 for t in d.index]
    candles = []
    for ts, row in zip(times, d.itertuples(index=False)):
        candles.append({"time": ts, "open": float(row.open), "high": float(row.high),
                        "low": float(row.low), "close": float(row.close)})
    vols, prev_c = [], None
    for ts, row in zip(times, d.itertuples(index=False)):
        v = float(row.volume) if not pd.isna(row.volume) else 0
        color = "#22c55e55" if (prev_c is None or row.close >= prev_c) else "#ef444455"
        vols.append({"time": ts, "value": v, "color": color})
        prev_c = row.close
    close_s = d["close"]
    def _ema_series(length: int) -> list[dict]:
        s = ema(close_s, length)
        return [{"time": ts, "value": float(v)} for ts, v in zip(times, s.values) if pd.notna(v)]
    return {
        "candles": candles,
        "volume":  vols,
        "ema_8":   _ema_series(8)   if len(close_s) > 8   else [],
        "ema_21":  _ema_series(21)  if len(close_s) > 21  else [],
        "ema_55":  _ema_series(55)  if len(close_s) > 55  else [],
        "ema_100": _ema_series(100) if len(close_s) > 100 else [],
        "ema_200": _ema_series(200) if len(close_s) > 200 else [],
    }


def fetch_hourly_bars(sym_u: str) -> Optional[pd.DataFrame]:
    """Fetch ~60 days of 60-minute bars for one ticker. yfinance's safe limit
    for interval=60m is 60 days at a time."""
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            d = yf.download(sym_u, period="60d", interval="60m",
                            auto_adjust=True, progress=False, threads=False)
        if d is None or d.empty:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
        else:
            d.columns = [c.lower() for c in d.columns]
        return d[["open","high","low","close","volume"]].dropna()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Wave 14 — full TradingView-style TF selector helpers.
# yfinance interval/period constraints (must be obeyed):
#   1m       → period ≤ 7d
#   2m,5m,15m,30m → period ≤ 60d
#   60m/90m  → period ≤ 730d (we use 60d for safety/speed)
#   1d/5d/1wk/1mo/3mo → period="max" works
# Derived intervals (3m, 45m, 2h, 3h, 4h, 3M, 6M, 12M, ALL) are produced by
# resampling a finer raw interval — saves an extra network round-trip and keeps
# bars aligned with the user's local TZ.
# ---------------------------------------------------------------------------
def fetch_intraday_bars(sym_u: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    """Generic intraday fetch — handles 1m / 5m / 15m / 30m / 60m / etc.
    Returns OHLCV dataframe or None on failure / empty result.
    """
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            d = yf.download(sym_u, period=period, interval=interval,
                            auto_adjust=True, progress=False, threads=False)
        if d is None or d.empty:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
        else:
            d.columns = [c.lower() for c in d.columns]
        return d[["open","high","low","close","volume"]].dropna()
    except Exception:
        return None


def resample_bars(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample an OHLCV dataframe to a coarser pandas frequency rule (e.g.
    '3min', '45min', '2h', '4h', 'QE', '2QE', 'YE', 'ME').
    Drops empty bars; returns empty df if input is None/empty."""
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        agg = df.resample(rule).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()
        return agg
    except Exception:
        return pd.DataFrame()


def fetch_max_history(sym_u: str) -> Optional[pd.DataFrame]:
    """Fetch full-history daily bars (period='max') for ALL-time analysis.
    May return thousands of rows for older tickers (e.g. AAPL since 1980)."""
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            d = yf.download(sym_u, period="max", interval="1d",
                            auto_adjust=True, progress=False, threads=False)
        if d is None or d.empty:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
        else:
            d.columns = [c.lower() for c in d.columns]
        return d[["open","high","low","close","volume"]].dropna()
    except Exception:
        return None


# Map of TF → (raw_interval, raw_period, optional resample rule) for the
# /chart-tf lazy-load endpoint. None resample = use raw as-is.
TF_FETCH_MAP: dict[str, tuple[str, str, Optional[str]]] = {
    "1m":  ("1m",  "7d",  None),
    "3m":  ("1m",  "7d",  "3min"),
    "5m":  ("5m",  "60d", None),
    "15m": ("15m", "60d", None),
    "30m": ("30m", "60d", None),
    "45m": ("15m", "60d", "45min"),
    "1h":  ("60m", "60d", None),
    "2h":  ("60m", "60d", "2h"),
    "3h":  ("60m", "60d", "3h"),
    "4h":  ("60m", "60d", "4h"),
}

# Daily-derived TFs — resampled from the cached daily_df (no extra fetch).
# Pandas resample rule + tail count.
TF_DAILY_DERIVED: dict[str, tuple[str, int]] = {
    "1D":  ("D",  1000),    # ~4 years
    "1W":  ("W",  520),     # ~10 years
    "1M":  ("ME", 240),     # ~20 years
    "3M":  ("QE", 80),      # ~20 years of quarters
    "6M":  ("2QE", 40),     # ~20 years of semesters
    "12M": ("YE", 30),      # ~30 years of annual bars
}


def fetch_tf_bars(sym_u: str, tf: str,
                  daily_df: Optional[pd.DataFrame] = None) -> Optional[pd.DataFrame]:
    """Return the OHLCV dataframe for ONE timeframe — the workhorse behind
    the /chart-tf lazy-load endpoint.

    Strategy:
      • Intraday (1m..4h)  → fetch from yfinance per TF_FETCH_MAP, optionally
                              resample to derived TF.
      • Daily-derived      → resample from caller-provided daily_df (cheap).
      • ALL                → fetch period='max' daily, resample to monthly
                              (Aaron's preference: 'el all que sea con bar
                              mensuales').
    """
    if tf in TF_FETCH_MAP:
        raw_int, raw_period, rule = TF_FETCH_MAP[tf]
        # 60m has its own historical helper (kept for backwards compat).
        if raw_int == "60m" and rule is None:
            raw = fetch_hourly_bars(sym_u)
        else:
            raw = fetch_intraday_bars(sym_u, raw_int, raw_period)
        if raw is None or raw.empty:
            return None
        if rule:
            return resample_bars(raw, rule)
        return raw
    if tf in TF_DAILY_DERIVED:
        if daily_df is None or daily_df.empty:
            return None
        rule, tail = TF_DAILY_DERIVED[tf]
        if rule == "D":
            return daily_df.tail(tail).copy()
        agg = resample_bars(daily_df, rule)
        return agg.tail(tail) if not agg.empty else None
    if tf == "ALL":
        # Monthly bars from inception — Aaron's spec.
        full = fetch_max_history(sym_u)
        if full is None or full.empty:
            return None
        monthly = resample_bars(full, "ME")
        # Cap to keep payload small (max 600 monthly bars = 50 years)
        return monthly.tail(600) if not monthly.empty else None
    return None


# Whether a TF should be serialized with intraday (unix-seconds) vs daily-string
# time format — required by Lightweight Charts to render correctly.
TF_IS_INTRADAY: dict[str, bool] = {
    "1m": True, "3m": True, "5m": True, "15m": True, "30m": True, "45m": True,
    "1h": True, "2h": True, "3h": True, "4h": True,
    "1D": False, "1W": False, "1M": False,
    "3M": False, "6M": False, "12M": False, "ALL": False,
}

# Master list of valid TFs (used by /chart-tf endpoint for validation).
VALID_TFS = list(TF_FETCH_MAP.keys()) + list(TF_DAILY_DERIVED.keys()) + ["ALL"]


def fetch_daily_history(sym_u: str, period: str = "5y") -> Optional[pd.DataFrame]:
    """Lightweight daily-bar fetch — used by /chart-tf when a daily-derived TF
    (1D / 1W / 1M / 3M / 6M / 12M) is requested. Avoids re-running the full
    scan_one detectors path."""
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            d = yf.download(sym_u, period=period, interval="1d",
                            auto_adjust=True, progress=False, threads=False)
        if d is None or d.empty:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
        else:
            d.columns = [c.lower() for c in d.columns]
        return d[["open","high","low","close","volume"]].dropna()
    except Exception:
        return None


def serialize_tf_for_chart(sym_u: str, tf: str) -> dict:
    """Top-level dispatch: fetch ONE timeframe's bars and serialize to the
    LWC-ready dict (candles + volume + EMAs). Used by the /chart-tf lazy-load
    HTTP endpoint."""
    daily_df = None
    if tf in TF_DAILY_DERIVED:
        # Daily-derived TFs need the underlying daily df.
        daily_df = fetch_daily_history(sym_u, period="5y")
    bars = fetch_tf_bars(sym_u, tf, daily_df=daily_df)
    daily_format = not TF_IS_INTRADAY.get(tf, False)
    return serialize_chart_tf(bars, daily_format=daily_format)


def build_multi_tf_chart_data(sym_u: str, daily_df: pd.DataFrame,
                               fetch_hourly: bool = True) -> dict:
    """Build the {default_tf, timeframes:{1H,1D,1W,1M}} payload for LWC."""
    tfs: dict[str, dict] = {}
    d = daily_df.tail(1000).copy()
    tfs["1D"] = serialize_chart_tf(d, daily_format=True)
    weekly_full = resample_period(daily_df, "W")
    if not weekly_full.empty:
        tfs["1W"] = serialize_chart_tf(weekly_full.tail(520), daily_format=True)
    monthly_full = resample_period(daily_df, "ME")
    if not monthly_full.empty:
        tfs["1M"] = serialize_chart_tf(monthly_full.tail(240), daily_format=True)
    if fetch_hourly:
        hourly = fetch_hourly_bars(sym_u)
        if hourly is not None and not hourly.empty:
            tfs["1H"] = serialize_chart_tf(hourly, daily_format=False)
    return {"default_tf": "1D", "timeframes": tfs}


def build_snapshot_for_symbol(sym_u: str, daily_df: pd.DataFrame,
                               weekly_df: Optional[pd.DataFrame] = None,
                               spy_trend: str = "side",
                               sector_trend: str = "side") -> Snapshot:
    """Module-level Snapshot builder — used by /chart route. Computes every
    key-level field (Wave 1, Wave 5, Wave 7-compatible) for a single ticker."""
    close = daily_df["close"]
    try:
        e55  = float(ema(close, 55).iloc[-1])  if len(close) > 55  else None
        e100 = float(ema(close, 100).iloc[-1]) if len(close) > 100 else None
        e200 = float(ema(close, 200).iloc[-1]) if len(close) > 200 else None
        rsi_v = float(rsi(close, 14).iloc[-1]) if len(close) > 14 else None
        px = float(close.iloc[-1])
    except Exception:
        e55 = e100 = e200 = rsi_v = None
        px = float(close.iloc[-1]) if len(close) else 0.0
    try:
        sr = support_resistance(daily_df.tail(750))
    except Exception:
        sr = {"support": [], "resistance": []}
    bid = ask = spread_pct = None
    try:
        t = yf.Ticker(sym_u)
        fi = getattr(t, "fast_info", None) or {}
        b = fi.get("bid") if isinstance(fi, dict) else getattr(fi, "bid", None)
        a = fi.get("ask") if isinstance(fi, dict) else getattr(fi, "ask", None)
        if b and a and a > 0 and b > 0:
            bid = float(b); ask = float(a)
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else None
    except Exception:
        pass
    avg_vol = None
    try:
        if "volume" in daily_df.columns and len(daily_df) >= 21:
            avg_vol = float(daily_df["volume"].iloc[-21:-1].mean())
    except Exception:
        pass
    try:    fib_data = compute_fib_levels(daily_df, lookback_bars=750)
    except Exception: fib_data = None
    try:    pivots_data = compute_pivot_points(daily_df)
    except Exception: pivots_data = None
    try:    vwap_val = compute_anchored_vwap(daily_df, lookback_bars=250)
    except Exception: vwap_val = None
    round_nums = compute_round_numbers(px, count=3)
    try:    mtf_piv = compute_multi_timeframe_pivots(daily_df)
    except Exception: mtf_piv = {}
    try:    mtf_vp = compute_multi_timeframe_volume_profile(daily_df)
    except Exception: mtf_vp = {}
    try:    recent_w = recent_period_extremes(resample_period(daily_df, "W"), count=3).get("periods", [])
    except Exception: recent_w = []
    try:    recent_m = recent_period_extremes(resample_period(daily_df, "ME"), count=3).get("periods", [])
    except Exception: recent_m = []
    try:    npocs = find_naked_pocs(daily_df, periods=8)
    except Exception: npocs = []
    try:    cam = compute_camarilla_pivots(daily_df)
    except Exception: cam = None
    return Snapshot(
        symbol=sym_u, current_price=px,
        ema_55=e55, ema_100=e100, ema_200=e200, rsi_14=rsi_v,
        support_levels=sr.get("support", [])[-3:],
        resistance_levels=sr.get("resistance", [])[-3:],
        bid=bid, ask=ask, spread_pct=spread_pct, avg_volume=avg_vol,
        fib=fib_data, pivots=pivots_data,
        vwap_anchored=vwap_val, round_numbers=round_nums,
        pivots_weekly=mtf_piv.get("weekly"),
        pivots_monthly=mtf_piv.get("monthly"),
        recent_weekly=recent_w, recent_monthly=recent_m,
        vp_weekly=mtf_vp.get("weekly"),
        vp_monthly=mtf_vp.get("monthly"),
        vp_quarterly=mtf_vp.get("quarterly"),
        naked_pocs=npocs,
        camarilla=cam,
        context_flags=build_context(
            daily_df=daily_df, symbol=sym_u, setup_direction="long",
            spy_trend=spy_trend, sector_trend=sector_trend,
            weekly_df=weekly_df,
        ),
    )


def run_full_scan(
    tickers: list[str],
    always_show: bool = False,
) -> tuple[list[Setup], float, str]:
    """Single scan pass — returns (setups, duration_seconds, html).

    When ``always_show`` is True (used for ad-hoc searches), every requested
    ticker gets a Snapshot in the output even if no CC setup fired — so the
    user always sees a chart + current indicator readout.
    """
    import time

    print(f"\n→ Scanning {len(tickers)} tickers at {datetime.now().strftime('%H:%M:%S')}...")
    started = time.time()

    # Pull market + sector context ONCE per scan (cached across tickers).
    print("  Fetching market regime (SPY + VIX)...")
    spy_trend = _scan_index_trend("SPY")
    market_regime = fetch_market_regime()
    macro_event = upcoming_macro_within(days_ahead=1)
    print(f"    SPY trend: {spy_trend} · VIX regime: {market_regime['vix_regime']} (level {market_regime.get('vix_level')})")
    if macro_event:
        print(f"    ⚠ Macro event within 24h: {macro_event[1]} on {macro_event[0]}")

    # Sector ETF trends — fetch each unique ETF once.
    unique_sectors = set(SECTOR_ETF.get(t.upper(), "SPY") for t in tickers)
    print(f"  Fetching sector trends for {len(unique_sectors)} ETFs...")
    sector_trends: dict[str, str] = {}
    for etf in unique_sectors:
        sector_trends[etf] = _scan_index_trend(etf)
    print(f"    Sector states: {sector_trends}")

    all_setups: list[Setup] = []
    snapshots: list[Snapshot] = []     # ad-hoc "no setup" snapshots
    levels_by_symbol: dict[str, Snapshot] = {}  # key-levels for EVERY ticker
    chart_data_by_symbol: dict[str, dict] = {}  # OHLCV + EMA arrays for charting
    all_watches: list[WatchItem] = []

    def _serialize_tf(d: pd.DataFrame, daily_format: bool) -> dict:
        """Serialize one timeframe of OHLCV → dict for Lightweight Charts.
        daily_format=True → time strings like "YYYY-MM-DD"; False → unix seconds
        (required by LWC for intraday so it doesn't aggregate them as daily)."""
        if d is None or d.empty:
            return {"candles": [], "volume": [], "ema_55": [], "ema_100": [], "ema_200": []}
        d = d.copy()
        if daily_format:
            times = [t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t) for t in d.index]
        else:
            times = [int(t.timestamp()) if hasattr(t, "timestamp") else 0 for t in d.index]
        candles = []
        for ts, row in zip(times, d.itertuples(index=False)):
            o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
            candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        vols = []
        prev_c = None
        for ts, row in zip(times, d.itertuples(index=False)):
            v = float(row.volume) if not pd.isna(row.volume) else 0
            color = "#22c55e55" if (prev_c is None or row.close >= prev_c) else "#ef444455"
            vols.append({"time": ts, "value": v, "color": color})
            prev_c = row.close
        close_s = d["close"]
        def _ema_series(length: int) -> list[dict]:
            s = ema(close_s, length)
            return [{"time": ts, "value": float(v)} for ts, v in zip(times, s.values) if pd.notna(v)]
        return {
            "candles": candles,
            "volume":  vols,
            "ema_8":   _ema_series(8)   if len(close_s) > 8   else [],
            "ema_21":  _ema_series(21)  if len(close_s) > 21  else [],
            "ema_55":  _ema_series(55)  if len(close_s) > 55  else [],
            "ema_100": _ema_series(100) if len(close_s) > 100 else [],
            "ema_200": _ema_series(200) if len(close_s) > 200 else [],
        }

    def _fetch_hourly(sym_u: str) -> Optional[pd.DataFrame]:
        """Fetch the last ~60 days of 60-minute bars for this ticker. yfinance
        caps interval=60m at 730 days but each query at ~60 days. We use 60d
        which is the sweet spot of breadth vs reliability."""
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                d = yf.download(sym_u, period="60d", interval="60m",
                                auto_adjust=True, progress=False, threads=False)
            if d is None or d.empty:
                return None
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
            else:
                d.columns = [c.lower() for c in d.columns]
            return d[["open","high","low","close","volume"]].dropna()
        except Exception:
            return None

    def _build_chart_data(sym_u: str, daily_df: pd.DataFrame) -> dict:
        """Build multi-timeframe chart data for the LWC chart.
        Returns:
          {
            "default_tf": "1D",
            "timeframes": {
              "1H":  {candles, volume, ema_55, ema_100, ema_200},   # last ~60 days hourly
              "1D":  {...}, # last ~4 years of daily bars
              "1W":  {...}, # all available weekly bars
              "1M":  {...}, # all available monthly bars
            }
          }
        """
        # 1000 daily bars ≈ 4 years — captures multi-year highs/lows AND keeps
        # LWC fast. CC's multi-year levels (e.g. CELH 2024 ATH at ~$100) are
        # required context.
        d = daily_df.tail(1000).copy()
        tfs: dict[str, dict] = {}
        tfs["1D"] = _serialize_tf(d, daily_format=True)
        # Weekly + Monthly are resampled from daily — cheap & always available
        weekly_full = resample_period(daily_df, "W")
        if not weekly_full.empty:
            # 520 weekly bars = 10 years (covers all CC reference moves)
            tfs["1W"] = _serialize_tf(weekly_full.tail(520), daily_format=True)
        monthly_full = resample_period(daily_df, "ME")
        if not monthly_full.empty:
            # 240 monthly bars = 20 years (max useful)
            tfs["1M"] = _serialize_tf(monthly_full.tail(240), daily_format=True)
        # Hourly is a separate yfinance fetch — only ~60 days available
        hourly = _fetch_hourly(sym_u)
        if hourly is not None and not hourly.empty:
            tfs["1H"] = _serialize_tf(hourly, daily_format=False)
        return {"default_tf": "1D", "timeframes": tfs}

    def _build_chart_data_OLD_UNUSED(sym_u: str, daily_df: pd.DataFrame) -> dict:
        """Kept for reference."""
        d = daily_df.tail(260).copy()
        times = [t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t) for t in d.index]
        candles = []
        for ts, row in zip(times, d.itertuples(index=False)):
            o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
            candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        vols = []
        prev_c = None
        for ts, row in zip(times, d.itertuples(index=False)):
            v = float(row.volume) if not pd.isna(row.volume) else 0
            color = "#22c55e55" if (prev_c is None or row.close >= prev_c) else "#ef444455"
            vols.append({"time": ts, "value": v, "color": color})
            prev_c = row.close
        close_s = d["close"]
        def _ema_series(length: int) -> list[dict]:
            s = ema(close_s, length)
            out = []
            for ts, v in zip(times, s.values):
                if pd.notna(v):
                    out.append({"time": ts, "value": float(v)})
            return out
        return {
            "candles": candles,
            "volume":  vols,
            "ema_55":  _ema_series(55)  if len(close_s) > 55  else [],
            "ema_100": _ema_series(100) if len(close_s) > 100 else [],
            "ema_200": _ema_series(200) if len(close_s) > 200 else [],
        }

    def _build_snapshot(sym_u: str, daily_df: pd.DataFrame, weekly_df, etf_u: str) -> Snapshot:
        close = daily_df["close"]
        try:
            e55  = float(ema(close, 55).iloc[-1])  if len(close) > 55 else None
            e100 = float(ema(close, 100).iloc[-1]) if len(close) > 100 else None
            e200 = float(ema(close, 200).iloc[-1]) if len(close) > 200 else None
            rsi_v = float(rsi(close, 14).iloc[-1]) if len(close) > 14 else None
            px = float(close.iloc[-1])
        except Exception:
            e55 = e100 = e200 = rsi_v = None
            px = float(close.iloc[-1]) if len(close) else 0.0
        try:
            # Use multi-year history so deeper S/R levels (e.g. CELH 2024 ATH
            # at ~$100) show up alongside the recent ones.
            sr = support_resistance(daily_df.tail(750))
        except Exception:
            sr = {"support": [], "resistance": []}
        # Best-effort bid/ask + liquidity from yfinance fast_info.
        bid = ask = spread_pct = None
        try:
            t = yf.Ticker(sym_u)
            fi = getattr(t, "fast_info", None) or {}
            b = fi.get("bid") if isinstance(fi, dict) else getattr(fi, "bid", None)
            a = fi.get("ask") if isinstance(fi, dict) else getattr(fi, "ask", None)
            if b and a and a > 0 and b > 0:
                bid = float(b); ask = float(a)
                mid = (bid + ask) / 2.0
                spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else None
        except Exception:
            pass
        avg_vol = None
        try:
            if "volume" in daily_df.columns and len(daily_df) >= 21:
                avg_vol = float(daily_df["volume"].iloc[-21:-1].mean())
        except Exception:
            pass
        # Wave 1: comprehensive level overlays.
        # Use a multi-year lookback (750 bars ≈ 3y) so the Fib ladder is
        # anchored to the major swing, not just the last 12 months.
        try:
            fib_data = compute_fib_levels(daily_df, lookback_bars=750)
        except Exception:
            fib_data = None
        try:
            pivots_data = compute_pivot_points(daily_df)
        except Exception:
            pivots_data = None
        try:
            vwap_val = compute_anchored_vwap(daily_df, lookback_bars=250)
        except Exception:
            vwap_val = None
        round_nums = compute_round_numbers(px, count=3)
        # Wave 5: multi-timeframe pivots, VPs, recent extremes, naked POCs
        try:
            mtf_piv = compute_multi_timeframe_pivots(daily_df)
        except Exception:
            mtf_piv = {}
        try:
            mtf_vp = compute_multi_timeframe_volume_profile(daily_df)
        except Exception:
            mtf_vp = {}
        try:
            weekly_df = resample_period(daily_df, "W")
            recent_weekly = recent_period_extremes(weekly_df, count=3).get("periods", [])
        except Exception:
            recent_weekly = []
        try:
            monthly_df = resample_period(daily_df, "ME")
            recent_monthly = recent_period_extremes(monthly_df, count=3).get("periods", [])
        except Exception:
            recent_monthly = []
        try:
            npocs = find_naked_pocs(daily_df, periods=8)
        except Exception:
            npocs = []
        try:
            cam = compute_camarilla_pivots(daily_df)
        except Exception:
            cam = None
        return Snapshot(
            symbol=sym_u,
            current_price=px,
            ema_55=e55, ema_100=e100, ema_200=e200, rsi_14=rsi_v,
            support_levels=sr.get("support", [])[-3:],
            resistance_levels=sr.get("resistance", [])[-3:],
            bid=bid, ask=ask, spread_pct=spread_pct, avg_volume=avg_vol,
            fib=fib_data, pivots=pivots_data,
            vwap_anchored=vwap_val, round_numbers=round_nums,
            pivots_weekly=mtf_piv.get("weekly"),
            pivots_monthly=mtf_piv.get("monthly"),
            recent_weekly=recent_weekly,
            recent_monthly=recent_monthly,
            vp_weekly=mtf_vp.get("weekly"),
            vp_monthly=mtf_vp.get("monthly"),
            vp_quarterly=mtf_vp.get("quarterly"),
            naked_pocs=npocs,
            camarilla=cam,
            context_flags=build_context(
                daily_df=daily_df, symbol=sym_u,
                setup_direction="long",
                spy_trend=spy_trend,
                sector_trend=sector_trends.get(etf_u, "side"),
                weekly_df=weekly_df,
            ),
        )

    # Small inter-ticker sleep avoids yfinance rate-limit + Render-side
    # gateway timeout when many tickers are scanned in a row.
    import time as _time
    for i, sym in enumerate(tickers, 1):
        if i > 1:
            _time.sleep(0.15)
        daily_df, setups, weekly_df = scan_one(sym)
        setups = [s for s in setups if s.risk_reward >= MIN_RISK_REWARD]
        sym_u = sym.upper()
        etf = SECTOR_ETF.get(sym_u, "SPY")
        for s in setups:
            s.context_flags = build_context(
                daily_df=daily_df,
                symbol=sym_u,
                setup_direction=s.direction,
                spy_trend=spy_trend,
                sector_trend=sector_trends.get(etf, "side"),
                weekly_df=weekly_df,
            )
            # Regime haircut: high-vol environments drop conviction.
            s.conviction = regime_adjusts_conviction(s.conviction, market_regime)
        all_setups.extend(setups)

        # Always-on: build a key-levels Snapshot for every ticker.
        # This powers the "Key Levels" panel on every setup card, plus the
        # standalone snapshot card when no setup fired.
        if daily_df is not None and not daily_df.empty:
            snap_levels = _build_snapshot(sym_u, daily_df, weekly_df, etf)
            levels_by_symbol[sym_u] = snap_levels
            # Wave 12: do NOT pre-build chart data here. Charts open in their
            # own tab via /chart?symbol=X — that endpoint fetches the data for
            # ONE ticker at a time on demand. This keeps the main page memory
            # footprint tiny (< 200 MB) regardless of how many tickers we scan.
            # chart_data_by_symbol stays empty.
            # Forming-setup detection runs on every ticker.
            try:
                watches = find_watches(sym_u, daily_df)
                all_watches.extend(watches)
            except Exception:
                pass
            # If no setup fired AND we're in always_show, the snapshot is
            # also the visible card.
            if always_show and not setups:
                snapshots.append(snap_levels)

        marker_bits = []
        if setups:
            marker_bits.append(f"{len(setups)} setup(s)")
        if sym_u in levels_by_symbol:
            n_watches = sum(1 for w in all_watches if w.symbol == sym_u)
            if n_watches:
                marker_bits.append(f"{n_watches} watch")
        marker = " · ".join(marker_bits) if marker_bits else ("snapshot" if always_show else "—")
        print(f"  [{i:>2}/{len(tickers)}] {sym_u:<10} {marker}")

    api_key, model = _load_groq_config()
    if all_setups and api_key:
        print(f"\n  AI senior-trader commentary on {len(all_setups)} setup(s)...")
        for s in all_setups:
            s.ai_analysis = ai_enhance_setup(s, api_key, model)
    # Wave 7 — Equity Model fundamental analysis per scanned ticker.
    # Cached on disk for 24h so we don't burn quota every scan.
    if api_key:
        fundamental_syms: set[str] = set()
        for s in all_setups:
            fundamental_syms.add(s.symbol)
        for snap in snapshots:
            fundamental_syms.add(snap.symbol)
        # Limit to first 8 tickers per scan to stay under Groq daily quota.
        eligible = sorted(fundamental_syms)[:8]
        if eligible:
            print(f"\n  Equity Model (fundamental) analysis on {len(eligible)} ticker(s)...")
        for sym_u in eligible:
            try:
                eq = get_equity_analysis(sym_u, api_key, model, max_age_hours=24)
            except Exception:
                eq = None
            if eq is None:
                continue
            if sym_u in levels_by_symbol:
                levels_by_symbol[sym_u].equity_analysis = eq

    duration = time.time() - started
    # Compute correlation concentration across the fired setups.
    sector_counts = correlation_warning(all_setups)
    html = render_html(
        all_setups, len(tickers), duration,
        snapshots=snapshots,
        levels_by_symbol=levels_by_symbol,
        watches=all_watches,
        chart_data_by_symbol=chart_data_by_symbol,
        market_regime=market_regime,
        macro_event=macro_event,
        sector_counts=sector_counts,
    )
    print(f"✓ Scan complete: {len(all_setups)} setup(s), {len(snapshots)} snapshot(s), {len(all_watches)} watch(es) in {duration:.1f}s\n")
    return all_setups, duration, html


# ---------------------------------------------------------------------------
# Wave 13 — favicon + PWA icon (replaces browser's default world icon, also
# used when adding the app to a phone home screen).
# Design: dark-navy rounded-square with a rising candlestick chart trend.
# ---------------------------------------------------------------------------
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <rect width="64" height="64" rx="14" fill="#0a0f1c"/>
  <!-- Rising trend line behind candles -->
  <path d="M6 50 L18 38 L30 44 L42 22 L58 12" stroke="#fbbf24" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="0.6"/>
  <!-- Three ascending candles -->
  <rect x="12" y="30" width="6" height="16" fill="#22c55e" rx="1"/>
  <line x1="15" y1="25" x2="15" y2="30" stroke="#22c55e" stroke-width="1.5"/>
  <line x1="15" y1="46" x2="15" y2="50" stroke="#22c55e" stroke-width="1.5"/>
  <rect x="26" y="22" width="6" height="22" fill="#22c55e" rx="1"/>
  <line x1="29" y1="18" x2="29" y2="22" stroke="#22c55e" stroke-width="1.5"/>
  <line x1="29" y1="44" x2="29" y2="46" stroke="#22c55e" stroke-width="1.5"/>
  <rect x="40" y="12" width="6" height="26" fill="#fbbf24" rx="1"/>
  <line x1="43" y1="8" x2="43" y2="12" stroke="#fbbf24" stroke-width="1.5"/>
  <line x1="43" y1="38" x2="43" y2="42" stroke="#fbbf24" stroke-width="1.5"/>
  <!-- CC monogram in corner -->
  <text x="58" y="60" text-anchor="end" font-family="Arial Black, sans-serif" font-weight="900" font-size="11" fill="#fbbf24" letter-spacing="-1">CC</text>
</svg>"""


def _favicon_link_tags() -> str:
    """Return the <link>/<meta> tags to insert in any page's <head> so the
    browser tab and phone-home-screen icon both use our CC logo, not the
    default world icon."""
    return (
        '<link rel="icon" type="image/svg+xml" href="/icon.svg"/>'
        '<link rel="apple-touch-icon" href="/icon.svg"/>'
        '<link rel="manifest" href="/manifest.webmanifest"/>'
        '<meta name="theme-color" content="#0a0f1c"/>'
        '<meta name="apple-mobile-web-app-capable" content="yes"/>'
        '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>'
        '<meta name="apple-mobile-web-app-title" content="CC Trader"/>'
    )


def _build_manifest_json() -> str:
    """Return the PWA manifest JSON content for /manifest.webmanifest."""
    import json as _json
    return _json.dumps({
        "name": "CC Trader",
        "short_name": "CC Trader",
        "description": "Chart Champions setup scanner — 38 detectors, multi-TF levels, AI commentary, fundamental scoring",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0a0f1c",
        "theme_color": "#fbbf24",
        "icons": [
            {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
        ],
    })


def render_single_chart_html(
    symbol: str,
    snap: Optional[Snapshot],
    chart_data: dict,
    setups: Optional[List[Setup]] = None,
    watches: Optional[List["WatchItem"]] = None,
    equity_analysis: Optional[dict] = None,
    market_regime: Optional[dict] = None,
) -> str:
    """Standalone /chart?symbol=X page — full CC experience for one ticker.

    Includes: candles + EMA 8/21/55/100/200, all scanner-drawn levels (Fibs,
    Pivots D/W/M, Camarilla, POCs, nPOCs, VWAP, round numbers, S/R), view
    toggle (CC LWC vs TradingView widget), TF selector (1H/1D/1W/1M),
    annotation tools, countdown badge, key levels panel, context flags,
    fired setup card (if any), forming watches, Structured Equity Analysis.

    Built memory-light — only ONE ticker's data in memory at a time. Multi-
    tabbing lets the operator open many charts side-by-side in browser tabs.
    """
    import json as _json
    setups = setups or []
    watches = watches or []
    market_regime = market_regime or {}
    tv_sym = _tv_symbol(symbol)

    # Build price-lines list for THIS ticker — every CC level
    price_lines: list[dict] = []
    if snap is not None:
        # Fired setup entry/stop/targets (if any)
        for s in setups:
            price_lines.append({"price": s.entry, "color": "#fbbf24", "lineStyle": 0, "lineWidth": 2, "title": f"Entry ${s.entry:.2f}"})
            price_lines.append({"price": s.stop_loss, "color": "#ef4444", "lineStyle": 0, "lineWidth": 2, "title": f"Stop ${s.stop_loss:.2f}"})
            for ti, t in enumerate(s.targets[:2], 1):
                price_lines.append({"price": t, "color": "#22c55e", "lineStyle": 2, "lineWidth": 2, "title": f"T{ti} ${t:.2f}"})
        # Swing S/R
        for sup in (snap.support_levels or [])[-3:]:
            price_lines.append({"price": sup, "color": "#22c55e88", "lineStyle": 2, "lineWidth": 1, "title": f"S ${sup:.2f}"})
        for res in (snap.resistance_levels or [])[-3:]:
            price_lines.append({"price": res, "color": "#ef444488", "lineStyle": 2, "lineWidth": 1, "title": f"R ${res:.2f}"})
        # Fibonacci ladder + extensions
        if snap.fib and snap.fib.get("retracements"):
            for pct, px in snap.fib["retracements"].items():
                is_cc = pct in ("0.618", "0.660")
                price_lines.append({"price": float(px),
                                    "color": "#fbbf24" if is_cc else "#fbbf2488",
                                    "lineStyle": 2, "lineWidth": 2 if is_cc else 1,
                                    "title": f"Fib {pct} ${float(px):.2f}"})
            for pct, px in (snap.fib.get("extensions") or {}).items():
                price_lines.append({"price": float(px), "color": "#f97316aa",
                                    "lineStyle": 2, "lineWidth": 1,
                                    "title": f"Fib ext {pct} ${float(px):.2f}"})
        # DAILY pivots
        if snap.pivots:
            p = snap.pivots
            price_lines.append({"price": p["pp"], "color": "#fde047", "lineStyle": 2, "lineWidth": 1, "title": f"DAILY PP ${p['pp']:.2f}"})
            for key, label in [("r1","R1"),("r2","R2"),("s1","S1"),("s2","S2")]:
                if key in p:
                    price_lines.append({"price": p[key], "color": "#fde04788", "lineStyle": 2, "lineWidth": 1, "title": f"DAILY {label} ${p[key]:.2f}"})
        # WEEKLY pivots
        if snap.pivots_weekly:
            p = snap.pivots_weekly
            price_lines.append({"price": p["pp"], "color": "#ec4899", "lineStyle": 2, "lineWidth": 2, "title": f"WEEKLY PP ${p['pp']:.2f}"})
            for key, label in [("r1","R1"),("r2","R2"),("s1","S1"),("s2","S2")]:
                if key in p:
                    price_lines.append({"price": p[key], "color": "#ec489988", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY {label} ${p[key]:.2f}"})
        # MONTHLY pivots
        if snap.pivots_monthly:
            p = snap.pivots_monthly
            price_lines.append({"price": p["pp"], "color": "#a855f7", "lineStyle": 2, "lineWidth": 2, "title": f"MONTHLY PP ${p['pp']:.2f}"})
            for key, label in [("r1","R1"),("s1","S1")]:
                if key in p:
                    price_lines.append({"price": p[key], "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY {label} ${p[key]:.2f}"})
        # Recent weekly + monthly highs/lows
        for w in (snap.recent_weekly or [])[-3:]:
            price_lines.append({"price": w["high"], "color": "#ec4899aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY high ${w['high']:.2f}"})
            price_lines.append({"price": w["low"],  "color": "#ec4899aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY low ${w['low']:.2f}"})
        for m in (snap.recent_monthly or [])[-3:]:
            price_lines.append({"price": m["high"], "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY high ${m['high']:.2f}"})
            price_lines.append({"price": m["low"],  "color": "#a855f7aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY low ${m['low']:.2f}"})
        # Multi-TF Volume Profile
        if snap.vp_weekly and "poc" in snap.vp_weekly:
            vp = snap.vp_weekly
            price_lines.append({"price": vp["poc"], "color": "#f97316", "lineStyle": 0, "lineWidth": 2, "title": f"WEEKLY POC ${vp['poc']:.2f}"})
            if "vah" in vp: price_lines.append({"price": vp["vah"], "color": "#f97316aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY VAH ${vp['vah']:.2f}"})
            if "val" in vp: price_lines.append({"price": vp["val"], "color": "#f97316aa", "lineStyle": 2, "lineWidth": 1, "title": f"WEEKLY VAL ${vp['val']:.2f}"})
        if snap.vp_monthly and "poc" in snap.vp_monthly:
            vp = snap.vp_monthly
            price_lines.append({"price": vp["poc"], "color": "#dc2626", "lineStyle": 0, "lineWidth": 2, "title": f"MONTHLY POC ${vp['poc']:.2f}"})
            if "vah" in vp: price_lines.append({"price": vp["vah"], "color": "#dc2626aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY VAH ${vp['vah']:.2f}"})
            if "val" in vp: price_lines.append({"price": vp["val"], "color": "#dc2626aa", "lineStyle": 2, "lineWidth": 1, "title": f"MONTHLY VAL ${vp['val']:.2f}"})
        # Naked POCs
        for n in (snap.naked_pocs or [])[:6]:
            price_lines.append({"price": float(n["poc"]), "color": "#06b6d4", "lineStyle": 2, "lineWidth": 1, "title": f"nPOC ${float(n['poc']):.2f}"})
        # Camarilla pivots
        if snap.camarilla:
            for key, label in [("h4","H4"),("h3","H3"),("h2","H2"),("h1","H1"),
                               ("l1","L1"),("l2","L2"),("l3","L3"),("l4","L4")]:
                if key in snap.camarilla:
                    price_lines.append({"price": float(snap.camarilla[key]), "color": "#14b8a688",
                                        "lineStyle": 2, "lineWidth": 1,
                                        "title": f"CAM {label} ${float(snap.camarilla[key]):.2f}"})
        # Anchored VWAP
        if snap.vwap_anchored is not None:
            price_lines.append({"price": float(snap.vwap_anchored), "color": "#3b82f6",
                                "lineStyle": 0, "lineWidth": 2,
                                "title": f"VWAP ${float(snap.vwap_anchored):.2f}"})
        # Round numbers
        for rn in (snap.round_numbers or [])[:6]:
            price_lines.append({"price": float(rn), "color": "#94a3b822",
                                "lineStyle": 2, "lineWidth": 1, "title": f"${float(rn):.0f}"})

    # Side-panel content
    px = snap.current_price if snap else 0.0
    bid_str = f"${snap.bid:.2f}" if (snap and snap.bid is not None) else "—"
    ask_str = f"${snap.ask:.2f}" if (snap and snap.ask is not None) else "—"
    spread_str = f"{snap.spread_pct:.2f}%" if (snap and snap.spread_pct is not None) else "—"
    avg_vol_str = "—"
    if snap and snap.avg_volume:
        v = snap.avg_volume
        avg_vol_str = (f"{v/1_000_000:.1f}M" if v >= 1_000_000
                       else (f"{v/1_000:.0f}K" if v >= 1_000 else f"{v:.0f}"))
    bar_pat = "—"
    rsi_html = "—"
    if snap and snap.rsi_14 is not None:
        rc = "#ef4444" if snap.rsi_14 > 70 else ("#22c55e" if snap.rsi_14 < 30 else "#94a3b8")
        rsi_html = f'<span style="color:{rc}">{snap.rsi_14:.1f}</span>'

    key_levels_panel = _render_key_levels_panel(snap)
    equity_panel = _render_equity_panel(equity_analysis) if equity_analysis else ""
    flags_panel = _render_flags(snap.context_flags) if (snap and snap.context_flags) else ""

    setup_panel = ""
    if setups:
        s = setups[0]
        long_dir = s.direction == "long"
        tone = "#22c55e" if long_dir else "#ef4444"
        arrow = "▲" if long_dir else "▼"
        ai_block = _ai_voice_block(getattr(s, "ai_analysis", "") or "")
        setup_panel = f"""
        <div class="setup-card">
          <div class="setup-head" style="color:{tone}">
            {arrow} {s.name} <span class="conv">{int(s.conviction*100)}%</span>
          </div>
          <div class="setup-grid">
            <div><span class="lbl">Entry</span><span class="val">${s.entry:.2f}</span></div>
            <div><span class="lbl">Stop</span><span class="val" style="color:#ef4444">${s.stop_loss:.2f}</span></div>
            <div><span class="lbl">Target 1</span><span class="val" style="color:#22c55e">${s.targets[0]:.2f}</span></div>
            <div><span class="lbl">Target 2</span><span class="val" style="color:#22c55e">${s.targets[1] if len(s.targets)>1 else s.targets[0]:.2f}</span></div>
            <div><span class="lbl">R:R</span><span class="val">{s.risk_reward:.2f}R</span></div>
            <div><span class="lbl">Move</span><span class="val">{s.move_pct:+.1f}%</span></div>
          </div>
          <div class="rationale">{s.reasoning}</div>
          <div class="cite">📖 {s.citation}</div>
          {ai_block}
          <div class="setup-actions">
            <button onclick="sizeTrade('{symbol}', {s.entry:.4f}, {s.stop_loss:.4f})">📐 Size this</button>
            <button class="take-btn" onclick="takeTrade('{symbol}', '{s.name}', '{s.direction}', {s.entry:.4f}, {s.stop_loss:.4f}, {s.targets[0] if s.targets else 0:.4f}, {s.targets[1] if len(s.targets)>1 else 0:.4f})">▶ Take</button>
          </div>
        </div>
        """

    watch_panel = ""
    if watches:
        items = ""
        for w in watches[:4]:
            dir_col = "#22c55e" if w.direction == "long" else "#ef4444"
            sign = "+" if w.distance_pct > 0 else ""
            items += (f'<div class="watch-row" style="border-left:3px solid {dir_col}">'
                      f'<div class="watch-head"><span style="color:{dir_col}">{w.signal}</span>'
                      f'<span class="watch-dist">{sign}{w.distance_pct:.1f}% · ~{w.bars_estimate}d</span></div>'
                      f'<div class="watch-detail">Waiting for: {w.waiting_for}</div>'
                      f'<div class="cite">📖 {w.citation}</div></div>')
        watch_panel = f'<h3 class="side-h">👁 Watching</h3>{items}'

    chart_data_json = _json.dumps({symbol: chart_data}, default=float)
    price_lines_json = _json.dumps(price_lines)

    vix_lvl = market_regime.get("vix_level")
    vix_lvl_str = f"{vix_lvl:.1f}" if vix_lvl is not None else "—"
    vix_regime = market_regime.get("vix_regime", "unknown")

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{symbol} · CC Chart</title>
{_favicon_link_tags()}
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://s3.tradingview.com/tv.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; background:#0a0f1c; color:#e2e8f0; margin:0; padding:14px; }}
  h1 {{ margin:0 0 4px 0; font-size:22px; }}
  h3 {{ margin:14px 0 6px 0; font-size:13px; color:#fbbf24; text-transform:uppercase; letter-spacing:1px; }}
  .layout {{ display:grid; grid-template-columns: minmax(0, 1fr) 380px; gap:14px; }}
  @media (max-width: 1100px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  .chart-host {{ background:#0a0f1c; border-radius:8px; padding:8px; }}
  .chart-toolbar {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:6px; flex-wrap:wrap; }}
  .view-toggle {{ display:flex; gap:0; background:#0f172a; border-radius:6px; padding:2px; }}
  .view-btn {{ padding:6px 14px; border:0; background:transparent; color:#94a3b8; border-radius:4px; font-size:11px; cursor:pointer; font-weight:600; font-family:ui-monospace,monospace; }}
  .view-btn:hover {{ background:#1e293b; color:#e2e8f0; }}
  .view-btn.active {{ background:#22c55e; color:#000; }}
  .chart-extras {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
  .anno-btn {{ padding:5px 10px; background:#0a0f1c; color:#94a3b8; border:1px solid #1e293b; border-radius:4px; font-size:11px; cursor:pointer; font-family:ui-monospace,monospace; }}
  .anno-btn:hover {{ background:#1e293b; color:#fbbf24; border-color:#fbbf24; }}
  .countdown-badge {{ padding:4px 10px; background:#1e1b4b; color:#a78bfa; border-radius:4px; font-size:11px; font-family:ui-monospace,monospace; font-weight:600; }}
  /* Wave 16 — chart-style selector (12 TradingView-parity styles) */
  .chart-style-select {{ padding:5px 10px; background:#0a0f1c; color:#fbbf24; border:1px solid #1e293b; border-radius:4px; font-size:11px; cursor:pointer; font-family:ui-monospace,monospace; font-weight:600; appearance:menulist; }}
  .chart-style-select:hover {{ background:#1e293b; border-color:#fbbf24; }}
  .chart-style-select option {{ background:#0a0f1c; color:#e2e8f0; }}
  .tv-widget-host {{ height:680px; width:100%; }}
  .tv-widget-host > div {{ height:680px !important; width:100% !important; }}
  .tv-widget-host iframe {{ height:680px !important; width:100% !important; border:0 !important; border-radius:6px; }}
  .lwc-wrap {{ background:#0a0f1c; border-radius:8px; padding:8px; position:relative; }}
  .tf-bar {{ display:flex; gap:4px; margin-bottom:6px; padding:4px; background:#0f172a; border-radius:6px; }}
  .tf-bar-grouped {{ flex-wrap:wrap; align-items:center; gap:3px; }}
  .tf-group-label {{ font-size:9px; color:#475569; text-transform:uppercase; letter-spacing:0.6px; padding:0 4px; font-weight:700; }}
  .tf-group-sep {{ width:1px; height:18px; background:#1e293b; margin:0 4px; }}
  .tf-btn {{ padding:5px 10px; border:1px solid #1e293b; background:#0a0f1c; color:#94a3b8; border-radius:4px; font-size:11px; font-family:ui-monospace,monospace; cursor:pointer; font-weight:600; }}
  .tf-btn:hover:not(:disabled) {{ background:#1e293b; color:#e2e8f0; }}
  .tf-btn.active {{ background:#22c55e; color:#000; border-color:#22c55e; }}
  .tf-btn.tf-unavailable {{ opacity:0.35; cursor:not-allowed; }}
  .tf-btn.tf-loading {{ background:#1e1b4b; color:#a78bfa; border-color:#a78bfa; }}
  .tf-btn-all {{ background:#1e1b4b; border-color:#312e81; color:#a78bfa; }}
  .tf-btn-all:hover:not(:disabled) {{ background:#312e81; color:#fff; }}
  .tf-loading {{ position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); background:rgba(15,23,42,0.95); border:1px solid #fbbf24; border-radius:8px; padding:14px 20px; display:flex; align-items:center; gap:10px; z-index:9999; font-size:12px; color:#fbbf24; box-shadow:0 8px 30px rgba(0,0,0,0.6); }}
  .tf-spinner {{ width:14px; height:14px; border:2px solid #1e293b; border-top-color:#fbbf24; border-radius:50%; animation:cc-spin 0.8s linear infinite; }}
  @keyframes cc-spin {{ to {{ transform:rotate(360deg); }} }}
  /* Wave 14 — All-Time Analysis opt-in CTA + result panel */
  .all-time-cta {{ display:flex; align-items:center; gap:8px; padding:10px 14px; margin-top:10px; background:linear-gradient(135deg,#1e1b4b 0%,#3b0764 100%); border:1px solid #6d28d9; border-radius:8px; flex-wrap:wrap; }}
  .all-time-btn {{ padding:8px 16px; background:#7c3aed; border:0; color:#fff; border-radius:6px; cursor:pointer; font-weight:600; font-size:12px; font-family:ui-monospace,monospace; }}
  .all-time-btn:hover:not(:disabled) {{ background:#6d28d9; }}
  .all-time-btn:disabled {{ opacity:0.55; cursor:wait; }}
  .all-time-warn {{ font-size:10px; color:#c4b5fd; flex:1; min-width:160px; }}
  .all-time-result {{ margin-top:10px; padding:14px; background:#0f172a; border-left:4px solid #a78bfa; border-radius:6px; font-size:12px; }}
  .all-time-result h4 {{ margin:0 0 8px 0; color:#a78bfa; font-size:13px; }}
  .all-time-result .at-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:4px 16px; margin-bottom:8px; }}
  .all-time-result .at-row {{ display:flex; justify-content:space-between; padding:2px 0; font-family:ui-monospace,monospace; }}
  .all-time-result .at-setups {{ margin-top:8px; padding-top:8px; border-top:1px solid #1e293b; }}
  .all-time-result .at-setup-row {{ padding:6px 8px; margin-top:4px; background:#0a0f1c; border-left:3px solid #22c55e; border-radius:4px; font-size:11px; }}
  .lwc-chart {{ height:680px; width:100%; }}
  .lwc-fallback {{ height:680px; display:flex; align-items:center; justify-content:center; color:#64748b; font-size:13px; }}
  .lwc-legend {{ position:absolute; left:14px; top:60px; background:rgba(15,23,42,0.78); border:1px solid #1e293b; border-radius:6px; padding:6px 10px; font-size:11px; font-family:ui-monospace,monospace; color:#94a3b8; pointer-events:none; line-height:1.6; }}
  .lwc-legend .lg-row {{ display:flex; gap:8px; align-items:center; }}
  .lwc-legend .lg-dot {{ width:8px; height:2px; border-radius:1px; display:inline-block; }}
  .lwc-legend .lg-px {{ color:#fbbf24; font-weight:700; }}
  .side-panel {{ display:flex; flex-direction:column; gap:8px; }}
  .side-h {{ margin:8px 0 4px 0; font-size:12px; color:#fbbf24; text-transform:uppercase; letter-spacing:1px; }}
  .header-row {{ display:flex; justify-content:space-between; align-items:baseline; padding:10px 14px; background:#0f172a; border-radius:8px; }}
  .price-big {{ font-size:28px; font-weight:700; color:#fbbf24; font-family:ui-monospace,monospace; }}
  .ticker-meta {{ font-size:11px; color:#94a3b8; }}
  .info-bar {{ display:flex; gap:14px; padding:8px 14px; background:#0f172a; border-radius:8px; font-size:11px; flex-wrap:wrap; }}
  .info-bar span b {{ color:#fbbf24; }}
  .key-levels {{ margin-top:0; padding:10px; background:#0a0f1c; border:1px dashed #1e293b; border-radius:6px; }}
  .kl-head {{ font-size:10px; text-transform:uppercase; letter-spacing:1px; color:#fbbf24; margin-bottom:8px; font-weight:600; }}
  .setup-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:4px 16px; font-size:12px; }}
  .setup-grid div {{ display:flex; justify-content:space-between; }}
  .lbl {{ color:#64748b; }}
  .val {{ font-family:ui-monospace,monospace; }}
  .lvl-dist {{ font-size:10px; color:#64748b; }}
  .flags {{ display:flex; flex-direction:column; gap:4px; padding:6px 0; }}
  .flag {{ display:flex; justify-content:space-between; padding:4px 8px; background:#0a0f1c; border-radius:4px; font-size:11px; }}
  .flag-l {{ font-weight:600; }}
  .flag-d {{ color:#94a3b8; font-size:10px; }}
  .setup-card {{ background:#0a0f1c; border:1px solid #1e293b; border-radius:8px; padding:12px; }}
  .setup-head {{ font-weight:600; font-size:13px; display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .conv {{ background:#22c55e; color:#000; padding:2px 6px; border-radius:4px; font-size:11px; font-family:ui-monospace,monospace; }}
  .rationale {{ font-size:11px; color:#94a3b8; margin-top:8px; }}
  .cite {{ font-size:10px; color:#64748b; margin-top:6px; }}
  .ai-voice {{ margin-top:10px; padding:10px; background:linear-gradient(135deg,#1e293b 0%,#0f1729 100%); border-left:3px solid #22c55e; border-radius:6px; font-size:12px; line-height:1.5; }}
  .ai-head {{ font-size:10px; text-transform:uppercase; letter-spacing:1px; color:#22c55e; margin-bottom:6px; font-weight:600; }}
  .ai-offline {{ border-left-color:#94a3b8 !important; opacity:0.8; }}
  .ai-offline code {{ background:#1e293b; padding:1px 4px; border-radius:3px; color:#fbbf24; font-size:11px; }}
  .setup-actions {{ display:flex; gap:6px; margin-top:10px; padding-top:8px; border-top:1px solid #1e293b; }}
  .setup-actions button {{ flex:1; padding:5px 8px; border-radius:4px; border:1px solid #1e293b; background:#0a0f1c; color:#94a3b8; cursor:pointer; font-size:11px; }}
  .setup-actions .take-btn {{ border-color:#22c55e; color:#22c55e; }}
  .setup-actions .take-btn:hover {{ background:#22c55e; color:#000; }}
  .watch-row {{ background:#0a0f1c; padding:8px 10px; border-radius:4px; margin-bottom:6px; font-size:11px; }}
  .watch-head {{ display:flex; justify-content:space-between; font-weight:600; }}
  .watch-dist {{ color:#fbbf24; font-family:ui-monospace,monospace; }}
  .watch-detail {{ color:#94a3b8; margin-top:3px; }}
  .equity-panel {{ padding:12px; background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 100%); border:1px solid #312e81; border-left:4px solid #a78bfa; border-radius:8px; font-size:11px; }}
  .eq-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .eq-title {{ font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#a78bfa; font-weight:700; }}
  .eq-band {{ padding:3px 10px; border-radius:4px; font-size:10px; font-weight:700; font-family:ui-monospace,monospace; }}
  .eq-snap {{ color:#cbd5e1; margin-bottom:8px; line-height:1.4; }}
  .eq-grid {{ display:grid; gap:3px; margin-bottom:8px; }}
  .eq-row {{ display:flex; justify-content:space-between; align-items:center; padding:3px 0; border-bottom:1px solid #1e293b; }}
  .eq-label {{ color:#94a3b8; }}
  .eq-score {{ display:flex; align-items:center; gap:8px; }}
  .eq-val {{ font-family:ui-monospace,monospace; font-weight:700; min-width:30px; text-align:right; }}
  .eq-stance {{ margin-top:6px; color:#cbd5e1; }}
  .eq-thesis {{ margin-top:6px; font-size:10px; color:#94a3b8; line-height:1.4; }}
  .eq-thesis div {{ margin-top:2px; }}
  .eq-inval {{ margin-top:8px; padding-top:8px; border-top:1px solid #1e293b; color:#fbbf24; font-size:10px; }}
  .eq-inval ul {{ margin:4px 0 0 0; padding-left:18px; color:#94a3b8; }}
  .back-link {{ color:#94a3b8; text-decoration:none; font-size:12px; margin-bottom:8px; display:inline-block; }}
  .back-link:hover {{ color:#22c55e; }}
  .action-bar {{ display:flex; gap:6px; margin-top:10px; flex-wrap:wrap; }}
  .action-bar button, .action-bar a {{ padding:6px 12px; background:#0a0f1c; color:#94a3b8; border:1px solid #1e293b; border-radius:4px; font-size:11px; cursor:pointer; text-decoration:none; font-family:ui-monospace,monospace; display:inline-flex; align-items:center; gap:4px; }}
  .action-bar button:hover, .action-bar a:hover {{ background:#1e293b; color:#fbbf24; border-color:#fbbf24; }}
  .alarm-toast {{ position:fixed; bottom:24px; right:24px; max-width:380px; background:linear-gradient(135deg,#16a34a,#22c55e); color:#000; padding:14px 18px; border-radius:10px; font-weight:600; box-shadow:0 10px 30px rgba(0,0,0,0.6); z-index:9999; }}
  /* Wave 13 — hover tooltip on chart lines */
  .hover-tooltip {{ position:absolute; background:rgba(15,23,42,0.97); border:1px solid #fbbf24; border-radius:6px; padding:8px 12px; font-size:11px; color:#e2e8f0; font-family:ui-monospace,monospace; z-index:10000; pointer-events:none; box-shadow:0 6px 20px rgba(0,0,0,0.6); max-width:340px; line-height:1.6; }}
  .hover-tooltip > div {{ margin:2px 0; }}
  /* Wave 13 — My Drawings list */
  .annotations-list {{ background:#0a0f1c; border:1px dashed #1e293b; border-radius:6px; padding:10px; font-size:11px; }}
  .anno-row {{ display:flex; justify-content:space-between; align-items:center; padding:5px 0; border-bottom:1px solid #1e293b; }}
  .anno-row:last-child {{ border-bottom:0; }}
  .anno-row .anno-info {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
  .anno-row .anno-kind {{ color:#fbbf24; font-weight:700; }}
  .anno-row .anno-price {{ color:#e2e8f0; font-family:ui-monospace,monospace; }}
  .anno-row .anno-text {{ color:#94a3b8; font-style:italic; }}
  .anno-row .anno-del {{ background:transparent; border:0; color:#ef4444; cursor:pointer; font-size:14px; padding:2px 6px; }}
  .anno-row .anno-del:hover {{ color:#fff; background:#ef4444; border-radius:3px; }}
  .anno-empty {{ color:#64748b; text-align:center; padding:10px; font-style:italic; }}
</style></head>
<body>
  <a href="/" class="back-link">← Back to scanner</a>
  <div class="layout">
    <div>
      <div class="header-row">
        <div>
          <h1>{symbol}</h1>
          <div class="ticker-meta">Bid {bid_str} · Ask {ask_str} · Spread {spread_str} · Avg vol(20d) {avg_vol_str}</div>
        </div>
        <div class="price-big">${px:.2f}</div>
      </div>
      <div class="info-bar">
        <span>RSI 14: <b>{rsi_html.replace('<span', '').replace('</span>', '').replace('style="color:', '').split('"')[0] if False else ''}</b></span>
        <span>RSI: {rsi_html}</span>
        <span>VIX: <b>{vix_lvl_str} · {vix_regime}</b></span>
      </div>

      <div class="chart-host" data-symbol="{symbol}" data-chart-idx="chart_solo">
        <div class="chart-toolbar">
          <div class="view-toggle">
            <button class="view-btn active" data-view="cc" data-target="chart_solo">📊 CC View</button>
            <button class="view-btn" data-view="tv" data-target="chart_solo">📈 TradingView</button>
          </div>
          <div class="chart-extras">
            <span class="countdown-badge" id="cd_chart_solo">⏱ —</span>
            <select class="chart-style-select" id="chart-style-select" onchange="setChartStyle(this.value)" title="Chart style">
              <option value="candles">📊 Candles</option>
              <option value="hollow_candles">⬚ Hollow candles</option>
              <option value="volume_candles">🎨 Volume candles</option>
              <option value="line">📈 Line</option>
              <option value="line_markers">⦿ Line w/ markers</option>
              <option value="step_line">⛟ Step line</option>
              <option value="area">⛰ Area</option>
              <option value="hlc_area">🟦 HLC area</option>
              <option value="baseline">⥯ Baseline</option>
              <option value="columns">▮ Columns</option>
              <option value="high_low">⫼ High-low</option>
              <option value="heikin_ashi">🕯 Heikin Ashi</option>
            </select>
            <button class="anno-btn" onclick="addAnnotation('{symbol}','chart_solo','note')">✏ Note</button>
            <button class="anno-btn" onclick="addAnnotation('{symbol}','chart_solo','line')">+ Line</button>
            <button class="anno-btn" onclick="clearAnnotations('{symbol}','chart_solo')">⌫ Clear my drawings</button>
          </div>
        </div>
        <div class="view-cc" data-view-id="chart_solo">
          <div class="lwc-wrap">
            <div class="tf-bar tf-bar-grouped">
              <span class="tf-group-label">Min</span>
              <button class="tf-btn" data-tf="1m">1m</button>
              <button class="tf-btn" data-tf="3m">3m</button>
              <button class="tf-btn" data-tf="5m">5m</button>
              <button class="tf-btn" data-tf="15m">15m</button>
              <button class="tf-btn" data-tf="30m">30m</button>
              <button class="tf-btn" data-tf="45m">45m</button>
              <span class="tf-group-sep"></span>
              <span class="tf-group-label">Hour</span>
              <button class="tf-btn" data-tf="1h">1h</button>
              <button class="tf-btn" data-tf="2h">2h</button>
              <button class="tf-btn" data-tf="3h">3h</button>
              <button class="tf-btn" data-tf="4h">4h</button>
              <span class="tf-group-sep"></span>
              <span class="tf-group-label">Day+</span>
              <button class="tf-btn" data-tf="1D">1D</button>
              <button class="tf-btn" data-tf="1W">1W</button>
              <button class="tf-btn" data-tf="1M">1M</button>
              <span class="tf-group-sep"></span>
              <span class="tf-group-label">Range</span>
              <button class="tf-btn" data-tf="3M">3M</button>
              <button class="tf-btn" data-tf="6M">6M</button>
              <button class="tf-btn" data-tf="12M">12M</button>
              <button class="tf-btn tf-btn-all" data-tf="ALL">ALL</button>
            </div>
            <div class="lwc-chart" id="lwc_chart_solo" data-symbol="{symbol}" data-lines='{price_lines_json}'></div>
            <div class="lwc-legend" id="lg_chart_solo"></div>
            <div class="tf-loading" id="tf_loading_chart_solo" style="display:none">
              <div class="tf-spinner"></div>
              <span class="tf-loading-text">Loading…</span>
            </div>
          </div>
        </div>
        <div class="view-tv" data-view-id="chart_solo" data-tv-symbol="{tv_sym}" style="display:none">
          <div class="tv-widget-host" id="tv_host_chart_solo"></div>
        </div>
      </div>

      <div class="action-bar">
        <button onclick="toggleStarSolo('{symbol}')" id="star-solo-btn">⭐ Toggle Watchlist</button>
        <button onclick="setAlarmSolo('{symbol}', {px:.2f})">🔔 Set price alarm</button>
        <button onclick="openManualSetupSolo('{symbol}', {px:.2f})">✎ Add manual setup</button>
        <a href="https://www.tradingview.com/chart/?symbol={tv_sym}" target="_blank">🔗 Open in TradingView.com</a>
      </div>

      <div class="all-time-cta">
        <button class="all-time-btn" id="all-time-btn" onclick="runAllTimeAnalysis('{symbol}')">🔬 Run All-Time Analysis (~15 min)</button>
        <span class="all-time-warn">⏱ Analyzes the FULL price history (since inception). Heavy — only run when you really want it. NOT automatic.</span>
      </div>
      <div id="all-time-result"></div>
    </div>

    <div class="side-panel">
      <h3 class="side-h">📐 Key Levels (with distance from current)</h3>
      {key_levels_panel}
      <h3 class="side-h">✏ My Drawings <span style="font-weight:400;color:#64748b">(saved per ticker)</span></h3>
      <div id="my-drawings-list" class="annotations-list">
        <div class="anno-empty">No drawings yet — click ✏ Note or + Line on the chart to add.</div>
      </div>
      {('<h3 class="side-h">🚦 Context Flags</h3>' + '<div class="setup-card">' + flags_panel + '</div>') if flags_panel else ''}
      {('<h3 class="side-h">🎯 Fired Setup</h3>' + setup_panel) if setup_panel else ''}
      {watch_panel}
      {('<h3 class="side-h">📊 Structured Equity Analysis</h3>' + equity_panel) if equity_panel else ''}
    </div>
  </div>

  <script>
    window.cc_charts_data = {chart_data_json};
    window.cc_chart_handles = window.cc_chart_handles || {{}};
    window.cc_tv_loaded = window.cc_tv_loaded || {{}};

    function _getTfData(rawSymData, tf) {{
      if (rawSymData && rawSymData.timeframes) return rawSymData.timeframes[tf] || rawSymData.timeframes[rawSymData.default_tf] || null;
      if (rawSymData && rawSymData.candles) return rawSymData;
      return null;
    }}
    function _availableTfs(rawSymData) {{
      if (rawSymData && rawSymData.timeframes) return Object.keys(rawSymData.timeframes);
      return rawSymData && rawSymData.candles ? ["1D"] : [];
    }}

    function getStars() {{ try {{ return JSON.parse(localStorage.getItem('cc_stars') || '[]'); }} catch(_) {{ return []; }} }}
    function saveStars(v) {{ localStorage.setItem('cc_stars', JSON.stringify(v)); }}
    function getAlarms() {{ try {{ return JSON.parse(localStorage.getItem('cc_alarms') || '[]'); }} catch(_) {{ return []; }} }}
    function saveAlarms(v) {{ localStorage.setItem('cc_alarms', JSON.stringify(v)); }}

    function getAnnotations(sym) {{
      try {{ return (JSON.parse(localStorage.getItem('cc_annotations') || '{{}}'))[sym] || []; }}
      catch(_) {{ return []; }}
    }}
    function saveAnnotations(sym, arr) {{
      var all;
      try {{ all = JSON.parse(localStorage.getItem('cc_annotations') || '{{}}'); }} catch(_) {{ all = {{}}; }}
      all[sym] = arr;
      localStorage.setItem('cc_annotations', JSON.stringify(all));
    }}
    function showToast(msg) {{
      var t = document.createElement('div');
      t.className = 'alarm-toast'; t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(function() {{ t.remove(); }}, 4500);
    }}

    function addAnnotation(sym, chartId, kind) {{
      var priceStr = prompt(kind === 'note' ? 'Note — price level:' : 'Line — price level:');
      if (!priceStr) return;
      var price = parseFloat(priceStr);
      if (isNaN(price)) return alert('Invalid price');
      var text = kind === 'note' ? (prompt('Note text (optional):') || '') : '';
      var color = kind === 'note' ? '#fbbf24' : '#22d3ee';
      var arr = getAnnotations(sym);
      arr.push({{id: Date.now(), kind: kind, price: price, text: text, color: color}});
      saveAnnotations(sym, arr);
      applyAnnotations(sym, chartId);
      renderDrawingsList(sym, chartId);
      showToast('✏ Added ' + (kind === 'note' ? 'note' : 'line') + ' at $' + price.toFixed(2));
    }}
    function clearAnnotations(sym, chartId) {{
      if (!confirm('Remove ALL your drawings for ' + sym + '?')) return;
      saveAnnotations(sym, []);
      applyAnnotations(sym, chartId);
      renderDrawingsList(sym, chartId);
    }}
    function deleteAnnotation(sym, chartId, annoId) {{
      var arr = getAnnotations(sym).filter(function(a) {{ return a.id !== annoId; }});
      saveAnnotations(sym, arr);
      applyAnnotations(sym, chartId);
      renderDrawingsList(sym, chartId);
    }}
    function renderDrawingsList(sym, chartId) {{
      var listEl = document.getElementById('my-drawings-list');
      if (!listEl) return;
      var arr = getAnnotations(sym);
      if (!arr.length) {{
        listEl.innerHTML = '<div class="anno-empty">No drawings yet — click ✏ Note or + Line on the chart to add.</div>';
        return;
      }}
      listEl.innerHTML = arr.map(function(a) {{
        var icon = a.kind === 'note' ? '✏' : '─';
        var textBlock = a.text ? '<span class="anno-text">"' + a.text + '"</span>' : '';
        return '<div class="anno-row">'
          + '<div class="anno-info">'
          + '<span class="anno-kind">' + icon + ' ' + (a.kind === 'note' ? 'Note' : 'Line') + '</span>'
          + '<span class="anno-price">$' + a.price.toFixed(2) + '</span>'
          + textBlock
          + '</div>'
          + '<button class="anno-del" onclick="deleteAnnotation(\\'' + sym + '\\', \\'' + chartId + '\\', ' + a.id + ')" title="Delete this drawing">✕</button>'
          + '</div>';
      }}).join('');
    }}
    function applyAnnotations(sym, chartId) {{
      var h = window.cc_chart_handles['lwc_' + chartId];
      if (!h) return;
      if (h.userLineHandles) {{
        h.userLineHandles.forEach(function(pl) {{ try {{ h.candleSeries.removePriceLine(pl); }} catch(_) {{}} }});
      }}
      var annos = getAnnotations(sym);
      h.userLineHandles = annos.map(function(a) {{
        return h.candleSeries.createPriceLine({{
          price: a.price, color: a.color || '#fbbf24', lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: '👤 ' + (a.text ? a.text + ' · ' : '') + '$' + a.price.toFixed(2),
        }});
      }});
    }}

    function toggleStarSolo(sym) {{
      var stars = getStars();
      var i = stars.indexOf(sym);
      if (i >= 0) {{ stars.splice(i, 1); showToast('☆ Removed ' + sym + ' from watchlist'); }}
      else {{ stars.push(sym); showToast('⭐ Added ' + sym + ' to watchlist'); }}
      saveStars(stars);
      var btn = document.getElementById('star-solo-btn');
      if (btn) btn.textContent = (stars.indexOf(sym) >= 0 ? '⭐' : '☆') + ' Toggle Watchlist';
    }}
    function setAlarmSolo(sym, currentPrice) {{
      var target = prompt('Alert when ' + sym + ' crosses price:\\n(current: $' + currentPrice + ')', currentPrice.toFixed(2));
      if (!target) return;
      var level = parseFloat(target);
      if (isNaN(level)) return alert('Invalid');
      var alarms = getAlarms();
      var dir = level > currentPrice ? 'above' : 'below';
      alarms.push({{symbol: sym, level: level, direction: dir, set_at: Date.now(), set_price: currentPrice}});
      saveAlarms(alarms);
      if (Notification.permission !== 'granted') Notification.requestPermission();
      showToast('🔔 Alarm set: ' + sym + ' ' + dir + ' $' + level.toFixed(2));
    }}
    function openManualSetupSolo(sym, price) {{
      var entry = prompt('Entry price (current ' + price + '):', price);
      if (!entry) return;
      var stop = prompt('Stop price:');
      if (!stop) return;
      var t1 = prompt('Target 1 price:');
      if (!t1) return;
      var t2raw = prompt('Target 2 price (optional):');
      var dir = parseFloat(stop) < parseFloat(entry) ? 'long' : 'short';
      var setups;
      try {{ setups = JSON.parse(localStorage.getItem('cc_manual_setups') || '[]'); }} catch(_) {{ setups = []; }}
      setups.unshift({{
        id: Date.now(), created: new Date().toISOString(),
        symbol: sym, name: 'Manual setup', direction: dir,
        entry: parseFloat(entry), stop: parseFloat(stop),
        t1: parseFloat(t1), t2: t2raw ? parseFloat(t2raw) : null,
        notes: '',
      }});
      localStorage.setItem('cc_manual_setups', JSON.stringify(setups));
      var stars = getStars();
      if (stars.indexOf(sym) < 0) {{ stars.push(sym); saveStars(stars); }}
      showToast('💾 Manual setup saved + added to watchlist');
    }}
    function sizeTrade(sym, entry, stop) {{
      var acctRaw = prompt('Account $:', '10000');
      var pctRaw = prompt('Risk %:', '0.5');
      var acct = parseFloat(acctRaw) || 0;
      var pct = parseFloat(pctRaw) || 0;
      var riskDollars = acct * (pct / 100.0);
      var perShare = Math.abs(entry - stop);
      var shares = perShare > 0 ? Math.floor(riskDollars / perShare) : 0;
      var notional = shares * entry;
      alert(sym + ' sizing:\\n\\nAccount: $' + acct.toFixed(2)
        + '\\nRisk: $' + riskDollars.toFixed(2)
        + '\\nShares: ' + shares
        + '\\nNotional: $' + notional.toFixed(2));
    }}
    function takeTrade(sym, name, dir, entry, stop, t1, t2) {{
      var j;
      try {{ j = JSON.parse(localStorage.getItem('cc_journal') || '[]'); }} catch(_) {{ j = []; }}
      j.unshift({{
        id: Date.now(), date: new Date().toISOString().slice(0,10),
        symbol: sym, name: name, direction: dir,
        entry: entry, stop: stop, t1: t1, t2: t2,
        shares: 0, risk_dollars: 0,
        status: 'open', exit: null, r_outcome: null, notes: '',
      }});
      localStorage.setItem('cc_journal', JSON.stringify(j));
      showToast('▶ Trade logged: ' + sym + ' ' + dir);
    }}

    function loadTradingViewWidget(targetId, symbol) {{
      if (window.cc_tv_loaded[targetId]) return;
      var hostEl = document.getElementById('tv_host_' + targetId);
      if (!hostEl) return;
      if (typeof TradingView === 'undefined') {{
        hostEl.innerHTML = '<div style="padding:30px;color:#64748b">TradingView library not loaded.</div>';
        return;
      }}
      hostEl.innerHTML = '<div id="tv_inner_' + targetId + '"></div>';
      try {{
        new TradingView.widget({{
          container_id: 'tv_inner_' + targetId,
          autosize: true, symbol: symbol, interval: 'D',
          timezone: 'America/New_York', theme: 'dark',
          style: '1', locale: 'en', toolbar_bg: '#0a0f1c',
          enable_publishing: false, hide_top_toolbar: false,
          hide_legend: false, save_image: false,
          allow_symbol_change: false, withdateranges: true,
          studies: ['MAExp@tv-basicstudies', 'MAExp@tv-basicstudies', 'MAExp@tv-basicstudies',
                    'RSI@tv-basicstudies', 'Volume@tv-basicstudies'],
        }});
        window.cc_tv_loaded[targetId] = true;
      }} catch(e) {{
        hostEl.innerHTML = '<div style="padding:30px;color:#ef4444">TV widget error: ' + e.message + '</div>';
      }}
    }}

    function initChart() {{
      if (typeof LightweightCharts === 'undefined') return;
      var div = document.getElementById('lwc_chart_solo');
      if (!div) return;
      var sym = div.getAttribute('data-symbol');
      var rawData = window.cc_charts_data[sym];
      var avail = _availableTfs(rawData);
      if (!avail.length) {{
        div.innerHTML = '<div class="lwc-fallback">No chart data for ' + sym + '</div>';
        return;
      }}
      var defaultTf = (rawData.default_tf && avail.indexOf(rawData.default_tf) >= 0) ? rawData.default_tf : avail[0];
      var initial = _getTfData(rawData, defaultTf);

      var chart = LightweightCharts.createChart(div, {{
        layout: {{ background: {{ type: 'solid', color: '#0a0f1c' }}, textColor: '#94a3b8' }},
        grid: {{ vertLines: {{ color: '#1e293b' }}, horzLines: {{ color: '#1e293b' }} }},
        rightPriceScale: {{ borderColor: '#1e293b' }},
        timeScale: {{ borderColor: '#1e293b', timeVisible: (defaultTf === '1H') }},
        crosshair: {{ mode: 1 }}, autoSize: true,
      }});
      // Wave 16 — Build the main series per the persisted chart style (default
      // = candles). The style selector swaps this series at runtime.
      var initialStyle = getChartStyle();
      var styleSel = document.getElementById('chart-style-select');
      if (styleSel) styleSel.value = initialStyle;
      var built = _buildMainSeries(chart, initialStyle, initial.candles || [], initial.volume || []);
      var candleSeries = built.main;
      var auxSeries = built.aux;
      var volSeries = chart.addHistogramSeries({{
        priceFormat: {{ type:'volume' }}, priceScaleId: '', color:'#22c55e55',
      }});
      volSeries.priceScale().applyOptions({{ scaleMargins: {{ top:0.85, bottom:0 }} }});
      if (initial.volume && initial.volume.length) volSeries.setData(initial.volume);

      var emaSeries = {{}};
      function addEMA(key, series, color, title) {{
        if (!series || !series.length) {{ emaSeries[key] = null; return; }}
        var s = chart.addLineSeries({{ color: color, lineWidth: 1, title: title, lastValueVisible: false, priceLineVisible: false }});
        s.setData(series); emaSeries[key] = s;
      }}
      addEMA('ema_8',   initial.ema_8,   '#fbbf24', 'EMA 8');
      addEMA('ema_21',  initial.ema_21,  '#f59e0b', 'EMA 21');
      addEMA('ema_55',  initial.ema_55,  '#94a3b8', 'EMA 55');
      addEMA('ema_100', initial.ema_100, '#cbd5e1', 'EMA 100');
      addEMA('ema_200', initial.ema_200, '#64748b', 'EMA 200');

      var rawLines = div.getAttribute('data-lines') || '[]';
      var lines;
      try {{ lines = JSON.parse(rawLines); }} catch(_) {{ lines = []; }}
      _reapplyPriceLines(candleSeries, lines);
      var legend = document.getElementById('lg_chart_solo');
      if (legend) {{
        legend.innerHTML =
          '<div class="lg-row"><span class="lg-dot" style="background:#22c55e"></span> Bull candle</div>'
        + '<div class="lg-row"><span class="lg-dot" style="background:#fbbf24"></span> EMA 8</div>'
        + '<div class="lg-row"><span class="lg-dot" style="background:#f59e0b"></span> EMA 21</div>'
        + '<div class="lg-row"><span class="lg-dot" style="background:#94a3b8"></span> EMA 55</div>'
        + '<div class="lg-row"><span class="lg-dot" style="background:#cbd5e1"></span> EMA 100</div>'
        + '<div class="lg-row"><span class="lg-dot" style="background:#64748b"></span> EMA 200</div>'
        + '<div class="lg-row"><span class="lg-px">' + sym + '</span></div>';
      }}
      // Wave 14 hotfix + Wave 21 + Wave 22 hardening — show ONLY the most
      // recent N bars on initial load with a verified retry loop so the
      // zoom always lands even when LWC's time-scale state lags.
      (function() {{
        var defaultBars = {{
          '1m': 60,  '3m': 60,  '5m': 78,  '15m': 52, '30m': 52, '45m': 40,
          '1h': 50,  '2h': 40,  '3h': 30,  '4h': 30,
          '1D': 120, '1W': 104, '1M': 60,
          '3M': 40,  '6M': 20,  '12M': 15, 'ALL': 240,
        }}[defaultTf] || 120;
        var n = initial.candles ? initial.candles.length : 0;
        function doInitZoom(attempt) {{
          try {{
            if (n <= defaultBars) {{
              chart.timeScale().fitContent();
              return;
            }}
            chart.timeScale().setVisibleLogicalRange({{ from: n - defaultBars, to: n - 1 }});
            var actual = chart.timeScale().getVisibleLogicalRange();
            var width  = actual ? (actual.to - actual.from) : null;
            if (width !== null && width > defaultBars * 1.5 && attempt < 5) {{
              setTimeout(function() {{ doInitZoom(attempt + 1); }}, 80);
              return;
            }}
            console.log('[CC] init zoom OK: tf=' + defaultTf + ', last '
                      + defaultBars + ' of ' + n + ' (width='
                      + (width ? width.toFixed(1) : '?') + ', attempt=' + attempt + ')');
          }} catch (e) {{
            console.warn('[CC] init zoom failed at attempt ' + attempt + ':', e);
            if (attempt < 5) setTimeout(function() {{ doInitZoom(attempt + 1); }}, 80);
            else {{ try {{ chart.timeScale().fitContent(); }} catch(_) {{}} }}
          }}
        }}
        setTimeout(function() {{ doInitZoom(1); }}, 50);
      }})();
      new ResizeObserver(function() {{ chart.applyOptions({{ width: div.clientWidth, height: div.clientHeight }}); }}).observe(div);

      // -------- Hover tooltips on chart lines (Wave 13) ----------------
      // When mouse moves over the chart, find any price lines near the
      // crosshair price and show a floating tooltip with their titles.
      var tooltipEl = document.getElementById('chart-tooltip');
      if (!tooltipEl) {{
        tooltipEl = document.createElement('div');
        tooltipEl.id = 'chart-tooltip';
        tooltipEl.className = 'hover-tooltip';
        tooltipEl.style.display = 'none';
        document.body.appendChild(tooltipEl);
      }}
      chart.subscribeCrosshairMove(function(param) {{
        // Wave 13 fix: don't require seriesPrices/seriesData — LWC v4 renamed
        // this to seriesData (a Map) and it may be absent if the cursor is
        // not directly over data points. We still want the tooltip to show
        // when hovering near any horizontal price-line.
        if (!param.point) {{
          tooltipEl.style.display = 'none';
          return;
        }}
        // Get the price at crosshair Y position
        var px = candleSeries.coordinateToPrice(param.point.y);
        if (px === null || isNaN(px)) {{
          tooltipEl.style.display = 'none';
          return;
        }}
        // Tolerance: 0.4% of the price
        var tol = Math.abs(px) * 0.004;
        var matches = lines.filter(function(l) {{ return Math.abs(l.price - px) <= tol; }});
        // Also include EMA values near crosshair via param.seriesData (LWC v4
        // Map) — falls back gracefully if not present.
        var seriesAt = param.seriesData || param.seriesPrices;
        if (seriesAt && typeof seriesAt.forEach === 'function') {{
          seriesAt.forEach(function(val, series) {{
            try {{
              // val can be a number OR a candle/line object {{value, time, ...}}
              var v = (typeof val === 'number') ? val
                    : (val && typeof val.value === 'number' ? val.value : null);
              if (v !== null && Math.abs(v - px) <= tol) {{
                Object.keys(emaSeries).forEach(function(k) {{
                  if (emaSeries[k] === series) {{
                    matches.push({{title: k.replace('_', ' ').toUpperCase() + ' $' + v.toFixed(2), color: '#94a3b8', price: v}});
                  }}
                }});
              }}
            }} catch(_) {{}}
          }});
        }}
        if (matches.length === 0) {{
          tooltipEl.style.display = 'none';
          return;
        }}
        // Build tooltip content
        tooltipEl.innerHTML = matches.map(function(m) {{
          var dist = ((m.price - px) / px * 100);
          var distStr = isNaN(dist) ? '' : ' <span style="color:#64748b">(' + (dist >= 0 ? '+' : '') + dist.toFixed(2) + '%)</span>';
          return '<div style="border-left:3px solid ' + (m.color || '#fbbf24') + ';padding-left:6px">'
               + (m.title || '') + distStr + '</div>';
        }}).join('');
        tooltipEl.style.display = 'block';
        // Position tooltip near the cursor but inside viewport
        var rect = div.getBoundingClientRect();
        var x = rect.left + param.point.x + 14;
        var y = rect.top + param.point.y + 14;
        var tw = tooltipEl.offsetWidth;
        var th = tooltipEl.offsetHeight;
        if (x + tw > window.innerWidth - 10) x = window.innerWidth - tw - 10;
        if (y + th > window.innerHeight - 10) y = window.innerHeight - th - 10;
        tooltipEl.style.left = x + 'px';
        tooltipEl.style.top = (y + window.scrollY) + 'px';
      }});

      window.cc_chart_handles['lwc_chart_solo'] = {{
        chart: chart, candleSeries: candleSeries, volSeries: volSeries,
        emaSeries: emaSeries, currentTf: defaultTf, rawData: rawData,
        // Wave 16 — chart-style state
        currentStyle: initialStyle, auxSeries: auxSeries,
        _priceLines: lines, _lastTfData: initial,
      }};

      // Wave 14 — TF buttons cover ALL 17 intervals. The page is BAKED with
      // 1D / 1W / 1M only (cheap from daily_df). Every other TF (1m, 3m, 5m,
      // 15m, 30m, 45m, 1h, 2h, 3h, 4h, 3M, 6M, 12M, ALL) is LAZY-FETCHED from
      // /chart-tf on first click, then cached client-side in window.cc_tf_cache
      // so repeat clicks are instant.
      window.cc_tf_cache = window.cc_tf_cache || {{}};
      document.querySelectorAll('.tf-btn').forEach(function(btn) {{
        var tf = btn.getAttribute('data-tf');
        if (tf === defaultTf) btn.classList.add('active');
        btn.addEventListener('click', function() {{
          if (btn.disabled) return;
          switchSoloTf(tf, btn);
        }});
      }});
      // Wire view toggle
      document.querySelectorAll('.view-btn').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
          var view = btn.dataset.view;
          document.querySelectorAll('.view-btn').forEach(function(b) {{ b.classList.remove('active'); }});
          btn.classList.add('active');
          document.querySelectorAll('.view-cc, .view-tv').forEach(function(v) {{ v.style.display = 'none'; }});
          if (view === 'cc') {{
            document.querySelector('.view-cc[data-view-id="chart_solo"]').style.display = '';
          }} else {{
            var tv = document.querySelector('.view-tv[data-view-id="chart_solo"]');
            tv.style.display = '';
            loadTradingViewWidget('chart_solo', tv.dataset.tvSymbol);
          }}
        }});
      }});
      applyAnnotations(sym, 'chart_solo');
    }}

    // Wave 14 — Intraday TF set (must render with timeVisible=true).
    var INTRADAY_TFS = ['1m','3m','5m','15m','30m','45m','1h','2h','3h','4h'];

    // Wave 16 — Chart-style selector (12 TradingView-parity styles).
    // Stored in localStorage so the operator's preference persists across
    // page loads + chart navigation.
    function getChartStyle() {{
      try {{ return localStorage.getItem('cc_chart_style') || 'candles'; }}
      catch(_) {{ return 'candles'; }}
    }}
    function saveChartStyle(s) {{
      try {{ localStorage.setItem('cc_chart_style', s); }} catch(_) {{}}
    }}

    // Compute Heikin Ashi candles from regular OHLC.
    // HA formula:
    //   HA_Close[i] = (O+H+L+C) / 4
    //   HA_Open[i]  = (HA_Open[i-1] + HA_Close[i-1]) / 2
    //   HA_High[i]  = max(H, HA_Open, HA_Close)
    //   HA_Low[i]   = min(L, HA_Open, HA_Close)
    function _heikinAshi(candles) {{
      if (!candles || !candles.length) return [];
      var out = [];
      var prevOpen = candles[0].open, prevClose = candles[0].close;
      for (var i = 0; i < candles.length; i++) {{
        var c = candles[i];
        var haClose = (c.open + c.high + c.low + c.close) / 4;
        var haOpen  = (i === 0) ? (c.open + c.close) / 2 : (prevOpen + prevClose) / 2;
        var haHigh  = Math.max(c.high, haOpen, haClose);
        var haLow   = Math.min(c.low,  haOpen, haClose);
        out.push({{ time: c.time, open: haOpen, high: haHigh, low: haLow, close: haClose }});
        prevOpen = haOpen; prevClose = haClose;
      }}
      return out;
    }}

    // Build the main series for a given chart style. Returns an object with
    // - main: the primary series (where we attach price lines + EMAs)
    // - aux:  extra series array (e.g. HLC area uses 3 line series)
    // Caller treats aux[] as auxiliary handles to remove on the next swap.
    function _buildMainSeries(chart, style, candles, volume) {{
      var LC = LightweightCharts;
      var closeData = candles.map(function(c) {{ return {{ time: c.time, value: c.close }}; }});
      var highData  = candles.map(function(c) {{ return {{ time: c.time, value: c.high }}; }});
      var lowData   = candles.map(function(c) {{ return {{ time: c.time, value: c.low  }}; }});
      var s, aux = [];
      switch (style) {{
        case 'hollow_candles':
          s = chart.addCandlestickSeries({{
            upColor: 'rgba(0,0,0,0)', downColor: '#ef4444',
            borderUpColor: '#22c55e', borderDownColor: '#ef4444',
            wickUpColor: '#22c55e', wickDownColor: '#ef4444',
          }});
          s.setData(candles); break;
        case 'volume_candles':
          // Map each bar's volume to opacity (higher volume = more saturated)
          var maxV = 1; volume.forEach(function(v) {{ if (v.value > maxV) maxV = v.value; }});
          var coloredCandles = candles.map(function(c, i) {{
            var v = (volume[i] && volume[i].value) || 0;
            var op = Math.max(0.25, Math.min(1, v / maxV));
            var up = c.close >= c.open;
            return Object.assign({{}}, c, {{ color: up ? 'rgba(34,197,94,' + op + ')' : 'rgba(239,68,68,' + op + ')' }});
          }});
          s = chart.addCandlestickSeries({{
            upColor:'#22c55e', downColor:'#ef4444',
            borderUpColor:'#22c55e', borderDownColor:'#ef4444',
            wickUpColor:'#22c55e', wickDownColor:'#ef4444',
          }});
          s.setData(coloredCandles); break;
        case 'line':
          s = chart.addLineSeries({{ color: '#fbbf24', lineWidth: 2 }});
          s.setData(closeData); break;
        case 'line_markers':
          s = chart.addLineSeries({{ color: '#fbbf24', lineWidth: 2 }});
          s.setData(closeData);
          // Mark every 10th bar so the chart doesn't get spammy
          if (closeData.length > 0) {{
            var step = Math.max(1, Math.floor(closeData.length / 25));
            var markers = [];
            for (var i = step; i < closeData.length; i += step) {{
              markers.push({{ time: closeData[i].time, position: 'inBar', shape: 'circle', color: '#22c55e', size: 1 }});
            }}
            s.setMarkers(markers);
          }}
          break;
        case 'step_line':
          s = chart.addLineSeries({{ color: '#fbbf24', lineWidth: 2, lineType: LC.LineType ? LC.LineType.WithSteps : 1 }});
          s.setData(closeData); break;
        case 'area':
          s = chart.addAreaSeries({{ topColor: 'rgba(251,191,36,0.35)', bottomColor: 'rgba(251,191,36,0.02)', lineColor: '#fbbf24', lineWidth: 2 }});
          s.setData(closeData); break;
        case 'hlc_area':
          // High line (green tint) + Low line (red tint) + Close area in middle
          var hi = chart.addLineSeries({{ color: 'rgba(34,197,94,0.6)', lineWidth: 1 }});
          hi.setData(highData);
          var lo = chart.addLineSeries({{ color: 'rgba(239,68,68,0.6)', lineWidth: 1 }});
          lo.setData(lowData);
          s = chart.addAreaSeries({{ topColor: 'rgba(251,191,36,0.20)', bottomColor: 'rgba(251,191,36,0.02)', lineColor: '#fbbf24', lineWidth: 2 }});
          s.setData(closeData);
          aux = [hi, lo];
          break;
        case 'baseline':
          var base = closeData.length ? closeData[0].value : 0;
          s = chart.addBaselineSeries({{
            baseValue: {{ type: 'price', price: base }},
            topLineColor: '#22c55e', topFillColor1: 'rgba(34,197,94,0.28)', topFillColor2: 'rgba(34,197,94,0.04)',
            bottomLineColor: '#ef4444', bottomFillColor1: 'rgba(239,68,68,0.28)', bottomFillColor2: 'rgba(239,68,68,0.04)',
            lineWidth: 2,
          }});
          s.setData(closeData); break;
        case 'columns':
          var colData = candles.map(function(c) {{
            return {{ time: c.time, value: c.close, color: c.close >= c.open ? '#22c55e' : '#ef4444' }};
          }});
          s = chart.addHistogramSeries({{ color: '#fbbf24', priceFormat: {{ type: 'price', precision: 2, minMove: 0.01 }} }});
          s.setData(colData); break;
        case 'high_low':
          // Bar series (OHLC bars) — LWC's bar series.
          s = chart.addBarSeries({{ upColor: '#22c55e', downColor: '#ef4444', thinBars: false }});
          s.setData(candles); break;
        case 'heikin_ashi':
          var ha = _heikinAshi(candles);
          s = chart.addCandlestickSeries({{
            upColor:'#22c55e', downColor:'#ef4444',
            borderUpColor:'#22c55e', borderDownColor:'#ef4444',
            wickUpColor:'#22c55e', wickDownColor:'#ef4444',
          }});
          s.setData(ha); break;
        case 'candles':
        default:
          s = chart.addCandlestickSeries({{
            upColor:'#22c55e', downColor:'#ef4444',
            borderUpColor:'#22c55e', borderDownColor:'#ef4444',
            wickUpColor:'#22c55e', wickDownColor:'#ef4444',
          }});
          s.setData(candles); break;
      }}
      return {{ main: s, aux: aux }};
    }}

    function _reapplyPriceLines(series, lines) {{
      if (!series || !lines) return;
      lines.forEach(function(l) {{
        try {{
          series.createPriceLine({{
            price: l.price, color: l.color, lineWidth: l.lineWidth || 2,
            lineStyle: l.lineStyle === 2 ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true, title: l.title || '',
          }});
        }} catch (e) {{}}
      }});
    }}

    function setChartStyle(style) {{
      var h = window.cc_chart_handles['lwc_chart_solo'];
      if (!h) return;
      saveChartStyle(style);
      h.currentStyle = style;
      // Re-render with the current TF data (use whatever's already loaded)
      var data = h._lastTfData;
      if (!data) return;
      // Remove the old main + aux series (price lines go with them)
      try {{ if (h.candleSeries) h.chart.removeSeries(h.candleSeries); }} catch(_) {{}}
      (h.auxSeries || []).forEach(function(s) {{ try {{ h.chart.removeSeries(s); }} catch(_) {{}} }});
      // Build new
      var built = _buildMainSeries(h.chart, style, data.candles, data.volume || []);
      h.candleSeries = built.main;
      h.auxSeries = built.aux;
      _reapplyPriceLines(h.candleSeries, h._priceLines);
      // Re-apply user annotations (they live on candleSeries)
      applyAnnotations(document.getElementById('lwc_chart_solo').getAttribute('data-symbol'), 'chart_solo');
    }}

    // Wave 14 (hotfix) — Default visible bars per TF.
    // When you switch to 1m, we DON'T want to show 7 days of 1-minute data
    // crushed onto the screen — that's unreadable. We show ONLY the most
    // recent N bars by default. The user can scroll back manually if they
    // want older history. Each entry = "how many recent bars to show".
    // Reasoning per TF (so the default view is actually useful):
    //   1m  → last ~60 bars   = ~1 hour
    //   3m  → last 60 bars    = ~3 hours
    //   5m  → last 78 bars    = ~1 RTH trading day
    //   15m → last 52 bars    = ~2 RTH days
    //   30m → last 52 bars    = ~4 RTH days
    //   45m → last 40 bars    = ~5 RTH days
    //   1h  → last 50 bars    = ~1 week of RTH
    //   2h  → last 40 bars    = ~1.5 weeks
    //   3h  → last 30 bars    = ~2 weeks
    //   4h  → last 30 bars    = ~3 weeks
    //   1D  → last 120 bars   = ~6 months
    //   1W  → last 104 bars   = ~2 years
    //   1M  → last 60 bars    = ~5 years
    //   3M  → last 40 bars    = ~10 years (quarterly)
    //   6M  → last 20 bars    = ~10 years (semi-annual)
    //   12M → last 15 bars    = ~15 years (annual)
    //   ALL → last 240 bars   = ~20 years monthly (full picture)
    var DEFAULT_VISIBLE_BARS = {{
      '1m': 60,  '3m': 60,  '5m': 78,  '15m': 52, '30m': 52, '45m': 40,
      '1h': 50,  '2h': 40,  '3h': 30,  '4h': 30,
      '1D': 120, '1W': 104, '1M': 60,
      '3M': 40,  '6M': 20,  '12M': 15, 'ALL': 240,
    }};

    function _setDefaultVisibleRange(h, tf, dataLen) {{
      var n = DEFAULT_VISIBLE_BARS[tf] || 120;
      // Wave 22 — harden the zoom with a retry loop. The previous double-
      // requestAnimationFrame (~33ms) wasn't enough on slow renders. We
      // try the zoom every 80ms for up to 5 attempts, verifying via the
      // chart's actual logical range that the zoom landed. If the zoom
      // succeeded, we stop. If after 5 tries the chart is still showing
      // the full dataset, we surrender to fitContent + log so the
      // operator can see what happened.
      function doZoom(attempt) {{
        try {{
          if (dataLen <= n) {{
            h.chart.timeScale().fitContent();
            console.log('[CC] zoom: tf=' + tf + ', fitContent (dataLen=' + dataLen + ' ≤ ' + n + ')');
            return;
          }}
          var from = dataLen - n;
          var to   = dataLen - 1;
          h.chart.timeScale().setVisibleLogicalRange({{ from: from, to: to }});
          // Verify the zoom actually applied — read back the visible range.
          var actual = h.chart.timeScale().getVisibleLogicalRange();
          var width  = actual ? (actual.to - actual.from) : null;
          if (width !== null && width > n * 1.5 && attempt < 5) {{
            // Zoom did NOT take effect (chart still shows >>n bars).
            // Retry after 80ms — LWC's internal time-scale state needs
            // more time after a series rebuild.
            console.log('[CC] zoom attempt ' + attempt + ' did not stick (width='
                      + width.toFixed(1) + '), retrying…');
            setTimeout(function() {{ doZoom(attempt + 1); }}, 80);
            return;
          }}
          console.log('[CC] zoom OK: tf=' + tf + ', visible bars=' + from + '..' + to
                    + ' (width=' + (width ? width.toFixed(1) : '?') + ', attempt=' + attempt + ')');
        }} catch (e) {{
          console.warn('[CC] zoom failed at attempt ' + attempt + ':', e);
          if (attempt < 5) {{
            setTimeout(function() {{ doZoom(attempt + 1); }}, 80);
          }} else {{
            try {{ h.chart.timeScale().fitContent(); }} catch(_) {{}}
          }}
        }}
      }}
      // Initial 50ms delay, then retry up to 5 times every 80ms.
      setTimeout(function() {{ doZoom(1); }}, 50);
    }}

    function _applyTfData(h, tf, tfData) {{
      if (!tfData || !tfData.candles || !tfData.candles.length) return false;
      // Wave 16 — Remember the latest data so the chart-style selector can
      // re-render without re-fetching.
      h._lastTfData = tfData;
      var style = h.currentStyle || getChartStyle();
      // Always rebuild the main series so style + data stay in sync.
      try {{ if (h.candleSeries) h.chart.removeSeries(h.candleSeries); }} catch(_) {{}}
      (h.auxSeries || []).forEach(function(s) {{ try {{ h.chart.removeSeries(s); }} catch(_) {{}} }});
      var built = _buildMainSeries(h.chart, style, tfData.candles, tfData.volume || []);
      h.candleSeries = built.main;
      h.auxSeries = built.aux;
      h.currentStyle = style;
      _reapplyPriceLines(h.candleSeries, h._priceLines);
      if (h.volSeries && tfData.volume) h.volSeries.setData(tfData.volume);
      ['ema_8','ema_21','ema_55','ema_100','ema_200'].forEach(function(k) {{
        if (h.emaSeries[k]) h.emaSeries[k].setData(tfData[k] || []);
      }});
      h.chart.applyOptions({{ timeScale: {{ timeVisible: INTRADAY_TFS.indexOf(tf) >= 0 }} }});
      // Show only the most recent N bars by default — user can scroll back
      // manually for older history. fitContent() (which crushed 7 days of 1m
      // bars onto the screen) is no longer the default behavior.
      _setDefaultVisibleRange(h, tf, tfData.candles.length);
      h.currentTf = tf;
      return true;
    }}

    function switchSoloTf(tf, btn) {{
      var h = window.cc_chart_handles['lwc_chart_solo'];
      if (!h) return;
      var sym = h.candleSeries ? document.getElementById('lwc_chart_solo').getAttribute('data-symbol') : '{symbol}';

      // Mark active button (visual feedback before fetch resolves).
      document.querySelectorAll('.tf-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      if (btn) btn.classList.add('active');

      // 1) Bundle-baked TFs (1D / 1W / 1M) — already in window.cc_charts_data.
      var bakedData = _getTfData(h.rawData, tf);
      if (bakedData && bakedData.candles && bakedData.candles.length) {{
        _applyTfData(h, tf, bakedData);
        return;
      }}

      // 2) Client-side cache (already fetched this session)
      if (window.cc_tf_cache[tf]) {{
        _applyTfData(h, tf, window.cc_tf_cache[tf]);
        return;
      }}

      // 3) Lazy-fetch from /chart-tf
      var loader = document.getElementById('tf_loading_chart_solo');
      if (loader) {{
        loader.style.display = 'flex';
        loader.querySelector('.tf-loading-text').textContent =
          'Loading ' + tf + (tf === 'ALL' ? ' (monthly bars, full history)…' : ' …');
      }}
      if (btn) btn.classList.add('tf-loading');

      // Wave 21 — cache-buster timestamp so the browser never serves a
      // stale response that might have been cached pre-fix.
      var url = '/chart-tf?symbol=' + encodeURIComponent(sym) + '&tf=' + encodeURIComponent(tf) + '&t=' + Date.now();
      console.log('[CC] TF switch — fetching', url);
      fetch(url).then(function(r) {{ return r.json(); }}).then(function(j) {{
        var n = (j && j.candles) ? j.candles.length : 0;
        console.log('[CC] /chart-tf response: tf=' + tf + ', candles=' + n
                  + ', first_time=' + (n ? j.candles[0].time : 'N/A')
                  + ', last_time=' + (n ? j.candles[n-1].time : 'N/A'));
        if (n > 0) {{
          window.cc_tf_cache[tf] = j;
          _applyTfData(h, tf, j);
        }} else {{
          showToast('⚠ No data for ' + tf
            + (tf === '1m' ? ' — yfinance gives 1m only for the last 7 days. If markets are closed or low-liquidity, try 5m or 15m.' : ' — try another TF.'));
          if (btn) btn.classList.remove('active');
        }}
      }}).catch(function(err) {{
        console.error('[CC] /chart-tf fetch failed:', err);
        showToast('⚠ ' + tf + ' fetch failed — check console');
        if (btn) btn.classList.remove('active');
      }}).finally(function() {{
        if (loader) loader.style.display = 'none';
        if (btn) btn.classList.remove('tf-loading');
      }});
    }}

    // Wave 14 — opt-in All-Time Analysis (~15 min). Triggered ONLY by user
    // clicking the button + accepting the confirm prompt.
    function runAllTimeAnalysis(sym) {{
      var ok = confirm(
        '🔬 All-Time Analysis for ' + sym + '\\n\\n' +
        'This will fetch the FULL price history (since the stock first listed) ' +
        'and re-run all 38 detectors + compute Fibonacci & support/resistance ' +
        'over the entire dataset.\\n\\n' +
        'Estimated time: ~15 minutes. Continue?'
      );
      if (!ok) return;
      var btn = document.getElementById('all-time-btn');
      var out = document.getElementById('all-time-result');
      if (btn) {{ btn.disabled = true; btn.textContent = '⏳ Analyzing… (~15 min)'; }}
      if (out) out.innerHTML = '<div class="all-time-result"><h4>⏳ Working on ' + sym + '…</h4><div>This page is safe to leave open. The browser will not show the result until the server finishes.</div></div>';
      fetch('/chart-allhist?symbol=' + encodeURIComponent(sym)).then(function(r) {{ return r.json(); }}).then(function(j) {{
        if (j.error) {{
          out.innerHTML = '<div class="all-time-result"><h4>✗ Error</h4><div>' + j.error + '</div></div>';
          return;
        }}
        var ath = j.all_time_high || {{}}; var atl = j.all_time_low || {{}};
        var fib = j.fib_full || {{}};
        var fibRows = '';
        if (fib.retracements) {{
          fibRows = Object.keys(fib.retracements).sort().map(function(p) {{
            return '<div class="at-row"><span>Fib ' + p + '</span><span>$' + fib.retracements[p].toFixed(2) + '</span></div>';
          }}).join('');
        }}
        var setupRows = (j.setups || []).map(function(s) {{
          return '<div class="at-setup-row"><b>' + s.name + '</b> · ' + s.direction.toUpperCase()
               + ' · entry $' + s.entry.toFixed(2) + ' · stop $' + s.stop.toFixed(2)
               + ' · conv ' + Math.round(s.conviction * 100) + '%</div>';
        }}).join('');
        var supports = (j.sr_full && j.sr_full.support || []).slice(-5)
          .map(function(v) {{ return '$' + v.toFixed(2); }}).join(', ') || '—';
        var resistances = (j.sr_full && j.sr_full.resistance || []).slice(0,5)
          .map(function(v) {{ return '$' + v.toFixed(2); }}).join(', ') || '—';
        out.innerHTML =
            '<div class="all-time-result">'
          + '<h4>🔬 All-Time Analysis · ' + sym + ' · ' + j.bars_count + ' bars (' + j.first_date + ' → ' + j.last_date + ') · ' + j.duration_s + 's</h4>'
          + '<div class="at-grid">'
          +   '<div class="at-row"><span>All-Time High</span><span>$' + (ath.price ? ath.price.toFixed(2) : '—') + ' on ' + (ath.date || '—') + '</span></div>'
          +   '<div class="at-row"><span>All-Time Low</span><span>$' + (atl.price ? atl.price.toFixed(2) : '—') + ' on ' + (atl.date || '—') + '</span></div>'
          +   '<div class="at-row"><span>Fib direction</span><span>' + (fib.direction || '—') + '</span></div>'
          +   '<div class="at-row"><span>Fib high / low</span><span>$' + (fib.high ? fib.high.toFixed(2) : '—') + ' / $' + (fib.low ? fib.low.toFixed(2) : '—') + '</span></div>'
          + '</div>'
          + (fibRows ? ('<h4 style="margin-top:8px">📐 Full-History Fibonacci ladder</h4>' + fibRows) : '')
          + '<h4 style="margin-top:8px">🧭 Full-History S/R clusters</h4>'
          + '<div class="at-row"><span>Support (below px)</span><span>' + supports + '</span></div>'
          + '<div class="at-row"><span>Resistance (above px)</span><span>' + resistances + '</span></div>'
          + (setupRows
              ? ('<div class="at-setups"><h4>🎯 Setups firing on full history (' + j.setups.length + ')</h4>' + setupRows + '</div>')
              : '<div class="at-setups"><h4>No setups fire on the full-history series.</h4></div>')
          + '</div>';
      }}).catch(function(err) {{
        out.innerHTML = '<div class="all-time-result"><h4>✗ Fetch failed</h4><div>' + err + '</div></div>';
      }}).finally(function() {{
        if (btn) {{ btn.disabled = false; btn.textContent = '🔬 Run All-Time Analysis (~15 min)'; }}
      }});
    }}

    function updateCountdown() {{
      var badge = document.getElementById('cd_chart_solo');
      if (!badge) return;
      var now = new Date();
      var sym = '{symbol}';
      var isCrypto = sym.indexOf('-USD') >= 0;
      var target = new Date(now);
      if (isCrypto) target.setUTCHours(24, 0, 0, 0);
      else {{
        target.setUTCHours(20, 0, 0, 0);
        if (target <= now) target.setUTCDate(target.getUTCDate() + 1);
        while (target.getUTCDay() === 6 || target.getUTCDay() === 0) target.setUTCDate(target.getUTCDate() + 1);
      }}
      var diffMs = Math.max(0, target - now);
      var totalMin = Math.floor(diffMs / 60000);
      var h = Math.floor(totalMin / 60);
      var m = totalMin % 60;
      badge.textContent = '⏱ ' + (isCrypto ? 'next UTC' : 'next NYSE') + ': ' + h + 'h ' + (m < 10 ? '0' : '') + m + 'm';
    }}

    window.addEventListener('load', function() {{
      initChart();
      updateCountdown();
      setInterval(updateCountdown, 60000);
      var stars = getStars();
      var btn = document.getElementById('star-solo-btn');
      if (btn) btn.textContent = (stars.indexOf('{symbol}') >= 0 ? '⭐' : '☆') + ' Toggle Watchlist';
      renderDrawingsList('{symbol}', 'chart_solo');
    }});
  </script>
</body></html>"""


# ---------------------------------------------------------------------------
# Wave 15 + Wave 23 — Persisted watchlists (MULTIPLE named lists).
#
# Wave 23 upgrades the single-flat-list to a dict of up to 10 user-named
# watchlists, like typical trading platforms:
#     {
#       "lists": {
#         "Future Buys":      ["LULU", "AAPL"],
#         "Current Holdings": ["MSFT"],
#         "Potential":        ["BTC-USD"],
#       },
#       "active": "Future Buys",
#       "updated_at": "...",
#     }
#
# The scan-universe used by the background loop is CC_2026 ∪ (union of
# every list's tickers), so a ticker in ANY list gets full CC analysis.
#
# Backward compatibility: if the file is in the OLD flat shape
# ({"tickers": [...]}) we transparently migrate it into a default list
# called 'My Watchlist' so existing users don't lose anything.
# ---------------------------------------------------------------------------
WATCHLIST_FILE = Path(__file__).resolve().parent / "watchlist_persisted.json"
MAX_WATCHLISTS = 10
MAX_TICKERS_PER_LIST = 50
DEFAULT_LIST_NAME = "My Watchlist"


def _sanitize_list_name(name: str) -> str:
    """Strip + truncate watchlist name. Allowed chars only."""
    if not isinstance(name, str):
        return ""
    n = name.strip()[:40]
    return n


def load_watchlists() -> dict:
    """Read the user's persisted watchlists. Always returns a dict with
    'lists', 'active', and 'updated_at' keys. Migrates legacy single-list
    files automatically."""
    default = {"lists": {DEFAULT_LIST_NAME: []}, "active": DEFAULT_LIST_NAME,
               "updated_at": datetime.now().isoformat()}
    try:
        if not WATCHLIST_FILE.exists():
            return default
        import json as _json
        data = _json.loads(WATCHLIST_FILE.read_text())
        # Legacy flat format → migrate
        if isinstance(data, dict) and "tickers" in data and "lists" not in data:
            tickers = data.get("tickers") or []
            return {"lists": {DEFAULT_LIST_NAME: [str(t).upper().strip()
                              for t in tickers if str(t).strip()]},
                    "active": DEFAULT_LIST_NAME,
                    "updated_at": data.get("updated_at",
                                            datetime.now().isoformat())}
        if isinstance(data, list):
            return {"lists": {DEFAULT_LIST_NAME: [str(t).upper().strip()
                              for t in data if str(t).strip()]},
                    "active": DEFAULT_LIST_NAME,
                    "updated_at": datetime.now().isoformat()}
        # New Wave 23 format
        if isinstance(data, dict) and "lists" in data and isinstance(data["lists"], dict):
            lists_out: dict[str, list[str]] = {}
            for name, tickers in data["lists"].items():
                clean_name = _sanitize_list_name(name)
                if not clean_name:
                    continue
                lists_out[clean_name] = [str(t).upper().strip()
                                          for t in (tickers or []) if str(t).strip()]
            if not lists_out:
                return default
            active = data.get("active")
            if active not in lists_out:
                active = next(iter(lists_out))
            return {"lists": lists_out, "active": active,
                    "updated_at": data.get("updated_at",
                                            datetime.now().isoformat())}
        return default
    except Exception:
        return default


def save_watchlists(payload: dict) -> bool:
    """Persist the multi-list watchlist payload. Validates names, dedupes
    tickers per-list, resolves aliases, caps to MAX_WATCHLISTS lists and
    MAX_TICKERS_PER_LIST per list."""
    try:
        import json as _json
        if not isinstance(payload, dict):
            return False
        lists_in = payload.get("lists") or {}
        if not isinstance(lists_in, dict):
            return False
        clean_lists: dict[str, list[str]] = {}
        for name, tickers in list(lists_in.items())[:MAX_WATCHLISTS]:
            clean_name = _sanitize_list_name(name)
            if not clean_name:
                continue
            seen: set[str] = set()
            clean: list[str] = []
            for raw in tickers or []:
                sym = resolve_ticker(str(raw)) if raw else None
                if not sym or not _VALID_TICKER.match(sym):
                    continue
                if sym in seen:
                    continue
                seen.add(sym)
                clean.append(sym)
            clean_lists[clean_name] = clean[:MAX_TICKERS_PER_LIST]
        if not clean_lists:
            clean_lists[DEFAULT_LIST_NAME] = []
        active = _sanitize_list_name(payload.get("active") or "")
        if active not in clean_lists:
            active = next(iter(clean_lists))
        out = {"lists": clean_lists, "active": active,
               "updated_at": datetime.now().isoformat()}
        WATCHLIST_FILE.write_text(_json.dumps(out, indent=2))
        return True
    except Exception:
        return False


def all_watchlist_tickers() -> list[str]:
    """Union of every ticker across every persisted list — used by the
    background scan to know what to analyze beyond CC_2026."""
    data = load_watchlists()
    seen: set[str] = set()
    out: list[str] = []
    for tickers in data["lists"].values():
        for t in tickers:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


# Backwards-compatibility helpers — keep the old API surface working so
# any existing code path (the search-bar /api/watchlist POST flow from
# Wave 15-22) still functions. They now operate on the ACTIVE list.
def load_persisted_watchlist() -> list[str]:
    """Returns tickers in the currently-active list."""
    data = load_watchlists()
    return data["lists"].get(data["active"], [])


def save_persisted_watchlist(tickers: list[str]) -> bool:
    """Replaces the currently-active list's tickers (legacy single-list
    flow used by syncWatchlistToBackend before Wave 23 UI)."""
    data = load_watchlists()
    active = data["active"]
    data["lists"][active] = tickers or []
    return save_watchlists(data)


def scan_one_full_response(symbol_raw: str) -> dict:
    """Wave 15 — On-demand full scan for ONE ticker (used by /api/scan-now).
    Returns JSON-ready dict with the fired Setup (if any), Snapshot details,
    Key Levels summary, and a one-line verdict. Triggered when the operator
    adds a new ticker to their watchlist — they see the analysis immediately
    instead of waiting for the next background scan cycle."""
    sym = resolve_ticker(symbol_raw)
    if not sym or not _VALID_TICKER.match(sym):
        return {"error": "invalid_symbol", "input": symbol_raw}
    daily_df, setups, weekly_df = scan_one(sym)
    if daily_df is None or daily_df.empty:
        return {"error": "no_data", "symbol": sym}
    snap = build_snapshot_for_symbol(sym, daily_df, weekly_df=weekly_df)
    out: dict = {
        "symbol": sym,
        "current_price": float(snap.current_price),
        "ema_55": float(snap.ema_55) if snap.ema_55 is not None else None,
        "ema_100": float(snap.ema_100) if snap.ema_100 is not None else None,
        "ema_200": float(snap.ema_200) if snap.ema_200 is not None else None,
        "rsi_14": float(snap.rsi_14) if snap.rsi_14 is not None else None,
        "support_levels": [float(x) for x in (snap.support_levels or [])][-3:],
        "resistance_levels": [float(x) for x in (snap.resistance_levels or [])][-3:],
        "setups_count": len(setups),
        "setups": [
            {
                "name": s.name, "direction": s.direction,
                "entry": float(s.entry), "stop": float(s.stop_loss),
                "targets": [float(t) for t in (s.targets or [])],
                "conviction": float(s.conviction),
                "reasoning": s.reasoning, "citation": s.citation,
            } for s in setups
        ],
        "has_fib":   bool(snap.fib),
        "has_pivots": bool(snap.pivots),
        "has_camarilla": bool(snap.camarilla),
        "chart_url": f"/chart?symbol={sym}",
    }
    return out


def build_all_time_analysis(symbol_raw: str) -> dict:
    """Wave 14 — Run the full detector suite over the ENTIRE price history of
    a ticker (period='max') and compute Fib/SR using the full lookback. This
    is the opt-in /chart-allhist endpoint behind the '🔬 Run All-Time Analysis
    (~15 min)' button. NOT triggered automatically.

    Returns a JSON-serializable dict with:
      - bars_count:        how many daily bars were analyzed
      - first_date / last_date
      - setups:            list of fired Setup dicts (any detector that fired
                           on the full-history series)
      - fib_full:          Fibonacci ladder anchored to all-time high/low
      - sr_full:           support/resistance from the full series
      - all_time_high / all_time_low (with dates)
      - duration_s:        analysis wall time
    """
    import time
    started = time.time()
    sym = resolve_ticker(symbol_raw)
    if not sym:
        return {"error": "invalid_symbol", "input": symbol_raw}
    full_df = fetch_max_history(sym)
    if full_df is None or full_df.empty:
        return {"error": "no_data", "symbol": sym}

    # Detectors — fire over the FULL series.
    setups_fired: list[dict] = []
    for fn in DETECTORS:
        try:
            s = fn(sym, full_df)
            if s is not None:
                setups_fired.append({
                    "name": s.name, "direction": s.direction,
                    "entry": float(s.entry), "stop": float(s.stop_loss),
                    "targets": [float(t) for t in (s.targets or [])],
                    "conviction": float(s.conviction),
                    "reasoning": s.reasoning,
                    "citation": s.citation,
                })
        except Exception:
            continue

    # Full-history Fib (lookback = entire df).
    try:
        fib_full = compute_fib_levels(full_df, lookback_bars=len(full_df))
    except Exception:
        fib_full = {}

    # Full-history S/R.
    try:
        sr_full = support_resistance(full_df)
    except Exception:
        sr_full = {"support": [], "resistance": []}

    # All-time high / low with dates.
    try:
        ath_idx = full_df["high"].idxmax()
        atl_idx = full_df["low"].idxmin()
        ath = {"price": float(full_df.loc[ath_idx, "high"]),
               "date":  ath_idx.strftime("%Y-%m-%d") if hasattr(ath_idx, "strftime") else str(ath_idx)}
        atl = {"price": float(full_df.loc[atl_idx, "low"]),
               "date":  atl_idx.strftime("%Y-%m-%d") if hasattr(atl_idx, "strftime") else str(atl_idx)}
    except Exception:
        ath = atl = None

    first_date = full_df.index[0].strftime("%Y-%m-%d") if hasattr(full_df.index[0], "strftime") else str(full_df.index[0])
    last_date  = full_df.index[-1].strftime("%Y-%m-%d") if hasattr(full_df.index[-1], "strftime") else str(full_df.index[-1])

    return {
        "symbol": sym,
        "bars_count": int(len(full_df)),
        "first_date": first_date, "last_date": last_date,
        "all_time_high": ath, "all_time_low": atl,
        "setups": setups_fired,
        "fib_full": fib_full,
        "sr_full": {
            "support": [float(x) for x in (sr_full.get("support") or [])],
            "resistance": [float(x) for x in (sr_full.get("resistance") or [])],
        },
        "duration_s": round(time.time() - started, 2),
    }


def build_single_chart_response(symbol_raw: str) -> str:
    """Top-level builder called by the /chart route. Resolves symbol, fetches
    data, builds Snapshot + chart data + watches + cached equity analysis,
    renders standalone HTML. Returns full HTML string."""
    sym = resolve_ticker(symbol_raw)
    if not sym:
        return "<html><body><h1>Invalid symbol</h1><a href='/'>← Back</a></body></html>"
    daily_df, setups, weekly_df = scan_one(sym)
    if daily_df is None or daily_df.empty:
        return f"<html><body><h1>No data for {sym}</h1><p>yfinance returned no daily bars. Try a different ticker.</p><a href='/'>← Back</a></body></html>"
    snap = build_snapshot_for_symbol(sym, daily_df, weekly_df=weekly_df)
    # Wave 14 — page initially bakes only 1D / 1W / 1M (cheap from daily_df).
    # Every other TF (1m, 3m, 5m, 15m, 30m, 45m, 1h, 2h, 3h, 4h, 3M, 6M, 12M,
    # ALL) is lazy-fetched from /chart-tf on click. Saves a yfinance call per
    # page load and avoids OOM under load.
    chart_data = build_multi_tf_chart_data(sym, daily_df, fetch_hourly=False)
    try:    watches = find_watches(sym, daily_df)
    except Exception: watches = []
    # Equity Model — cached, won't fetch fresh if recent
    api_key, model = _load_groq_config()
    equity_analysis = None
    if api_key:
        try:    equity_analysis = get_equity_analysis(sym, api_key, model, max_age_hours=24)
        except Exception: equity_analysis = None
    # AI senior trader if there's a fired setup
    if setups and api_key:
        for s in setups[:1]:
            try:    s.ai_analysis = ai_enhance_setup(s, api_key, model)
            except Exception: s.ai_analysis = ""
    # Apply regime haircut if we have it from a recent scan
    market_regime = fetch_market_regime()
    if setups:
        for s in setups:
            s.conviction = regime_adjusts_conviction(s.conviction, market_regime)
    return render_single_chart_html(
        symbol=sym, snap=snap, chart_data=chart_data,
        setups=setups, watches=watches,
        equity_analysis=equity_analysis,
        market_regime=market_regime,
    )


def serve_live(tickers: list[str], port: int, refresh_seconds: int, cache_seconds: int) -> int:
    """Start a tiny HTTP server that re-scans on a schedule and auto-refreshes
    the browser. The page reloads itself; the server returns cached HTML if
    the cache is still fresh."""
    import http.server
    import threading
    import time
    import urllib.parse

    state = {
        "html": "<html><body style='background:#0a0f1c;color:#e2e8f0;font-family:system-ui;padding:40px'>"
                "<h1>📈 Loading first scan...</h1>"
                f"<meta http-equiv='refresh' content='3'>"
                "<p style='color:#94a3b8'>This takes ~60–90 seconds the first time. The page will reload itself.</p>"
                "</body></html>",
        "last_run": 0.0,
        "running": False,
    }
    state_lock = threading.Lock()

    def _merged_tickers() -> list[str]:
        """Wave 15 + Wave 23 — base CC_2026 universe ∪ union of EVERY
        named watchlist. Read fresh on every scan so adding/removing
        from any list takes effect on the very next cycle."""
        extra = all_watchlist_tickers()
        if not extra:
            return list(tickers)
        seen = {t.upper() for t in tickers}
        merged = list(tickers)
        for x in extra:
            if x not in seen:
                merged.append(x)
                seen.add(x)
        return merged

    def background_loop():
        while True:
            now = time.time()
            if now - state["last_run"] >= cache_seconds:
                with state_lock:
                    if state["running"]:
                        time.sleep(2)
                        continue
                    state["running"] = True
                try:
                    # always_show=True so every watchlist ticker gets a
                    # snapshot card visible by default, not just the ones
                    # where a setup fired today.
                    # Wave 15 — merge persisted watchlist on every cycle so
                    # the user's tickers get the full 38-detector + Key
                    # Levels + Fib + AI analysis automatically.
                    active_tickers = _merged_tickers()
                    _, _, html = run_full_scan(active_tickers, always_show=True)
                    with state_lock:
                        state["html"] = inject_meta_refresh(html, refresh_seconds)
                        state["last_run"] = time.time()
                finally:
                    with state_lock:
                        state["running"] = False
            time.sleep(5)

    threading.Thread(target=background_loop, daemon=True).start()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_POST(self):
            # Wave 15 — POST /api/watchlist (legacy single-list flow).
            # Wave 23 — POST /api/watchlists (multi-list save).
            import json as _json
            parsed = urllib.parse.urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = _json.loads(body)
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json.dumps({"ok": False, "error": str(e)}).encode())
                return
            if parsed.path == "/api/watchlist":
                # Legacy: replaces tickers in the ACTIVE list.
                tickers_in = data.get("tickers", []) if isinstance(data, dict) else []
                ok = save_persisted_watchlist(tickers_in)
                saved = load_persisted_watchlist() if ok else []
                self.send_response(200 if ok else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_json.dumps({"ok": ok, "tickers": saved}).encode())
                return
            if parsed.path == "/api/watchlists":
                # Wave 23: replaces the FULL multi-list state.
                ok = save_watchlists(data)
                saved = load_watchlists()
                self.send_response(200 if ok else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_json.dumps({"ok": ok, **saved}).encode())
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            if parsed.path in ("/", "/index.html"):
                # Ad-hoc scan: ?symbols=AAPL,TSLA,BTC-USD
                adhoc = qs.get("symbols", [""])[0].strip()
                if adhoc:
                    # Resolve common names (bitcoin → BTC-USD, apple → AAPL, gold → GLD)
                    custom = [resolve_ticker(s) for s in adhoc.split(",") if s.strip()]
                    custom = [s for s in custom if s]
                    if custom:
                        print(f"  → Ad-hoc scan of {len(custom)} ticker(s): {custom}")
                        # always_show=True so the user sees a chart even when
                        # no CC setup fires on the searched ticker.
                        _, _, adhoc_html = run_full_scan(custom, always_show=True)
                        html = inject_meta_refresh(adhoc_html, refresh_seconds)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(html.encode("utf-8"))
                        return

                # Default — serve the cached background scan
                with state_lock:
                    html = state["html"]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif parsed.path in ("/icon.svg", "/favicon.svg", "/favicon.ico", "/apple-touch-icon.png"):
                # Wave 13 — serve the CC logo as favicon + apple-touch-icon
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(LOGO_SVG.encode("utf-8"))
            elif parsed.path == "/manifest.webmanifest":
                # PWA manifest — lets phones add the app to home screen
                self.send_response(200)
                self.send_header("Content-Type", "application/manifest+json")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(_build_manifest_json().encode("utf-8"))
            elif parsed.path == "/chart":
                # Wave 12 — standalone single-ticker chart page
                sym_q = qs.get("symbol", [""])[0].strip()
                if not sym_q:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h1>Missing ?symbol= parameter</h1><a href='/'>Back</a>")
                    return
                try:
                    html_page = build_single_chart_response(sym_q)
                except Exception as e:
                    html_page = (f"<html><body style='font-family:system-ui;padding:30px;background:#0a0f1c;color:#e2e8f0'>"
                                 f"<h1>Chart error for {sym_q}</h1><pre>{type(e).__name__}: {e}</pre>"
                                 f"<a href='/' style='color:#22c55e'>← Back to scanner</a></body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(html_page.encode("utf-8"))
            elif parsed.path == "/chart-tf":
                # Wave 14 — lazy-load ONE timeframe's bars for the chart page.
                # Called by the chart JS when the user clicks a TF button.
                import json as _json
                sym_q = qs.get("symbol", [""])[0].strip()
                tf    = qs.get("tf", [""])[0].strip()
                sym   = resolve_ticker(sym_q) if sym_q else None
                if not sym or tf not in VALID_TFS:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(_json.dumps({"error": "bad_params",
                                                   "symbol": sym_q, "tf": tf,
                                                   "valid_tfs": VALID_TFS}).encode())
                    return
                try:
                    payload = serialize_tf_for_chart(sym, tf)
                    payload["symbol"] = sym
                    payload["tf"] = tf
                except Exception as e:
                    payload = {"error": str(e), "symbol": sym, "tf": tf,
                               "candles": [], "volume": []}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                # Wave 21 — no caching so the browser always gets fresh
                # bars from yfinance on every TF click.
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_json.dumps(payload, default=float).encode())
            elif parsed.path == "/chart-allhist":
                # Wave 14 — opt-in all-time analysis (~15 min for big tickers).
                # Triggered ONLY by the user clicking the "🔬 Run All-Time
                # Analysis" button, NEVER automatically.
                import json as _json
                sym_q = qs.get("symbol", [""])[0].strip()
                try:
                    result = build_all_time_analysis(sym_q)
                except Exception as e:
                    result = {"error": str(e), "symbol": sym_q}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(_json.dumps(result, default=float).encode())
            elif parsed.path == "/api/watchlist":
                # Wave 15 — Read the persisted watchlist (active list only,
                # for backwards-compat with the search-bar sync flow).
                import json as _json
                tickers_out = load_persisted_watchlist()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_json.dumps({"tickers": tickers_out}).encode())
            elif parsed.path == "/api/watchlists":
                # Wave 23 — Read ALL persisted watchlists + active selection.
                import json as _json
                data = load_watchlists()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_json.dumps(data).encode())
            elif parsed.path == "/api/scan-now":
                # Wave 15 — On-demand full scan of one ticker. Called by the
                # frontend right after the user adds a new ticker to the
                # watchlist so they see CC analysis in seconds, not minutes.
                import json as _json
                sym_q = qs.get("symbol", [""])[0].strip()
                try:
                    result = scan_one_full_response(sym_q)
                except Exception as e:
                    result = {"error": str(e), "symbol": sym_q}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_json.dumps(result, default=float).encode())
            elif parsed.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    f'{{"ok":true,"last_run_seconds_ago":{time.time()-state["last_run"]:.1f}}}'.encode()
                )
            else:
                self.send_response(404)
                self.end_headers()

    url = f"http://localhost:{port}"
    print()
    print("═" * 60)
    print(f"  📈 CC Trader live server")
    print(f"  URL:           {url}")
    print(f"  Auto-refresh:  page reloads every {refresh_seconds}s")
    print(f"  Re-scan:       every {cache_seconds}s in background")
    print(f"  Tickers:       {len(tickers)} symbols")
    print(f"  Stop:          Ctrl+C")
    print("═" * 60)
    print()
    print("Opening browser in 3 seconds...")
    threading.Timer(3.0, lambda: webbrowser.open(url)).start()

    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n✓ shutting down")
    return 0


def inject_meta_refresh(html: str, seconds: int) -> str:
    """Insert <meta http-equiv='refresh'> into <head>."""
    tag = f'<meta http-equiv="refresh" content="{seconds}">'
    if "<head>" in html:
        return html.replace("<head>", f"<head>{tag}", 1)
    return tag + html


def main() -> int:
    args = sys.argv[1:]
    serve_mode = "--serve" in args or "--live" in args
    backtest_mode = "--backtest" in args
    args = [a for a in args if a not in ("--serve", "--live", "--backtest")]

    # parse --port and --refresh
    # Honor PORT injected by hosting platforms (Render, Fly, Heroku, etc.)
    port = int(os.environ.get("PORT", "8080"))
    refresh_seconds = 60
    cache_seconds = 300
    cleaned: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif a == "--refresh" and i + 1 < len(args):
            refresh_seconds = int(args[i + 1])
            i += 2
        elif a == "--cache" and i + 1 < len(args):
            cache_seconds = int(args[i + 1])
            i += 2
        else:
            cleaned.append(a)
            i += 1
    args = cleaned

    tickers = args if args else CC_2026

    if backtest_mode:
        run_backtest(tickers)
        return 0

    if serve_mode:
        return serve_live(tickers, port=port, refresh_seconds=refresh_seconds, cache_seconds=cache_seconds)

    # one-shot mode — also show every watchlist ticker by default so the
    # operator sees the full universe, not just fired setups.
    _, _, html = run_full_scan(tickers, always_show=True)
    out = Path("cc_setups_report.html").resolve()
    out.write_text(html, encoding="utf-8")
    print(f"✓ Report saved to:  {out}")
    print("Opening in your browser...")
    webbrowser.open(f"file://{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
