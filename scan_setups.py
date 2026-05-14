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
# Backtested conviction values — populated by `--backtest` at startup or by
# the persisted JSON file on disk. Without a backtest the constants below act
# as the "prior" — calibrated against typical CC-style win-rates (45-65%).
# After running `python scan_setups.py --backtest`, real numbers replace these.
# ---------------------------------------------------------------------------
BACKTESTED_CONVICTION: dict[str, float] = {
    "EMA Pullback":  0.62,    # prior — backtest will refine
    "CC Region":     0.64,
    "S/R Flip":      0.60,
    "Volume Spike":  0.58,
    "Inside Day":    0.55,
    "RSI Reversal":  0.48,
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


def bar_pattern(df: pd.DataFrame) -> str:
    """Classify the most recent bar relative to the prior bar. CC flags
    hammer / engulfing / inside bars as 'confirmation' patterns on the
    trigger bar. Returns one of: 'hammer', 'inverted-hammer', 'engulfing',
    'inside', 'doji', 'neutral'."""
    if len(df) < 2:
        return "neutral"
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last["close"] - last["open"])
    full = max(last["high"] - last["low"], 1e-9)
    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]
    # Doji — open ≈ close
    if body / full < 0.1:
        return "doji"
    # Inside bar — entirely within previous bar's range
    if last["high"] <= prev["high"] and last["low"] >= prev["low"]:
        return "inside"
    # Engulfing — body fully contains prior bar's body
    prev_body_hi = max(prev["open"], prev["close"])
    prev_body_lo = min(prev["open"], prev["close"])
    cur_body_hi  = max(last["open"], last["close"])
    cur_body_lo  = min(last["open"], last["close"])
    if cur_body_hi >= prev_body_hi and cur_body_lo <= prev_body_lo and body > abs(prev["close"] - prev["open"]):
        return "engulfing"
    # Hammer — long lower wick, small body near top
    if lower_wick >= 2 * body and upper_wick < body:
        return "hammer"
    if upper_wick >= 2 * body and lower_wick < body:
        return "inverted-hammer"
    return "neutral"


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
        conv = base_conv + (0.10 if pat in ("hammer", "engulfing") else 0.0)
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
        conv = base_conv + (0.10 if pat in ("inverted-hammer", "engulfing") else 0.0)
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
            conv = base + (0.10 if pat in ("hammer", "engulfing") else 0.0)
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
            conv = base + (0.10 if pat in ("inverted-hammer", "engulfing") else 0.0)
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
    sr = support_resistance(df.tail(200))
    last = df.iloc[-1]
    px = float(last["close"])
    lo = float(last["low"])
    hi = float(last["high"])
    atrv = float(atr(df, 14).iloc[-1])
    if not volume_confirmed(df):
        return None
    pat = bar_pattern(df)
    base = BACKTESTED_CONVICTION.get("S/R Flip", 0.60)

    for level in sr["resistance"]:
        if lo <= level <= px and (px - level) <= 0.5 * atrv:
            stop = level - 0.5 * atrv
            targets = smart_targets_long(df, px, stop)
            conv = base + (0.10 if pat in ("hammer", "engulfing") else 0.0)
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
            conv = base + (0.10 if pat in ("inverted-hammer", "engulfing") else 0.0)
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
        conv = base + (0.10 if pat == "engulfing" else 0.0)
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
        conv = base + (0.10 if pat == "engulfing" else 0.0)
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
        conv = base + (0.10 if pat in ("hammer", "engulfing") else 0.0)
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
        conv = base + (0.10 if pat in ("inverted-hammer", "engulfing") else 0.0)
        return Setup(
            symbol, "RSI Overbought Reversal (short)", "short",
            entry=px, stop_loss=stop, targets=targets,
            current_price=px, conviction=min(0.88, conv - 0.05),
            reasoning=f"RSI exiting overbought ({r_prev:.1f}→{r_now:.1f}). Vol confirmed, bar: {pat}.",
            citation="Second 18.pdf p.1 (inverted)",
        )
    return None


DETECTORS = [
    detect_ema_pullback,
    detect_cc_region_pullback,
    detect_sr_flip,
    detect_volume_spike,
    detect_inside_day,
    detect_rsi_reversal,
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
            df = yf.download(
                sym, period="2y", interval="1d",
                auto_adjust=True, progress=False, threads=False,
            )
            weekly = yf.download(
                sym, period="3y", interval="1wk",
                auto_adjust=True, progress=False, threads=False,
            )
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
        return ("AVOID", "#ef4444", 4)
    if s.conviction >= 0.75 and s.risk_reward >= 2.0 and len(warn) == 0:
        return ("STRONG TAKE", "#22c55e", 1)
    if s.conviction >= 0.65 and s.risk_reward >= 1.5 and len(warn) <= 1:
        return ("TAKE", "#86efac", 2)
    return ("MARGINAL", "#f59e0b", 3)


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
    """Lightweight Charts container for a snapshot card (no setup), with
    only S/R + EMA overlays — no entry/stop/target lines."""
    import json as _json
    sym = snap.symbol
    if sym not in chart_data_by_symbol:
        return '<div class="lwc-fallback">📉 Chart data unavailable — try refreshing.</div>'
    lines: list[dict] = []
    for sup in (snap.support_levels or [])[-3:]:
        lines.append({"price": sup, "color": "#22c55e88", "lineStyle": 2, "lineWidth": 1, "title": f"S ${sup:.2f}"})
    for res in (snap.resistance_levels or [])[-3:]:
        lines.append({"price": res, "color": "#ef444488", "lineStyle": 2, "lineWidth": 1, "title": f"R ${res:.2f}"})
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
    """A consistent 'Key Levels' panel: price + EMAs + S/R + distance%.
    Used on every setup card AND every snapshot card so the operator always
    has the full picture next to a chart."""
    if snap is None:
        return ""
    px = snap.current_price
    def _row(label: str, value: Optional[float], color: str = "#e2e8f0") -> str:
        if value is None:
            return f'<div><span class="lbl">{label}</span><span class="val">—</span></div>'
        dist_pct = ((value - px) / px * 100.0) if px else 0.0
        arrow = "↑" if dist_pct > 0 else ("↓" if dist_pct < 0 else "•")
        sign = "+" if dist_pct > 0 else ""
        return (
            f'<div><span class="lbl">{label}</span>'
            f'<span class="val" style="color:{color}">${value:.2f} '
            f'<span class="lvl-dist">({arrow} {sign}{dist_pct:.1f}%)</span></span></div>'
        )
    rows = []
    rows.append(f'<div><span class="lbl">Current</span><span class="val" style="color:#fbbf24"><b>${px:.2f}</b></span></div>')
    # Bid / ask / spread — when available
    if getattr(snap, "bid", None) is not None and getattr(snap, "ask", None) is not None:
        spread_str = f" ({snap.spread_pct:.2f}%)" if snap.spread_pct else ""
        spread_color = "#22c55e" if snap.spread_pct and snap.spread_pct < 0.10 else (
                       "#f59e0b" if snap.spread_pct and snap.spread_pct < 0.50 else "#ef4444")
        rows.append(
            f'<div><span class="lbl">Bid/Ask</span>'
            f'<span class="val" style="color:{spread_color}">${snap.bid:.2f} / ${snap.ask:.2f}'
            f'<span class="lvl-dist">{spread_str}</span></span></div>'
        )
    if getattr(snap, "avg_volume", None):
        # Format volume compactly: 1.2M, 540K, etc.
        v = snap.avg_volume
        if v >= 1_000_000:  v_str = f"{v/1_000_000:.1f}M"
        elif v >= 1_000:    v_str = f"{v/1_000:.0f}K"
        else:               v_str = f"{v:.0f}"
        liq_color = "#22c55e" if v >= 1_000_000 else ("#f59e0b" if v >= 100_000 else "#ef4444")
        rows.append(f'<div><span class="lbl">Avg vol (20d)</span><span class="val" style="color:{liq_color}">{v_str}</span></div>')
    rows.append(_row("EMA 55",  snap.ema_55,  "#94a3b8"))
    rows.append(_row("EMA 100", snap.ema_100, "#94a3b8"))
    rows.append(_row("EMA 200", snap.ema_200, "#64748b"))
    if snap.rsi_14 is not None:
        rsi_color = "#ef4444" if snap.rsi_14 > 70 else ("#22c55e" if snap.rsi_14 < 30 else "#94a3b8")
        rows.append(f'<div><span class="lbl">RSI 14</span><span class="val" style="color:{rsi_color}">{snap.rsi_14:.1f}</span></div>')
    for sup in (snap.support_levels or [])[-3:]:
        rows.append(_row("Support", sup, "#22c55e"))
    for res in (snap.resistance_levels or [])[-3:]:
        rows.append(_row("Resistance", res, "#ef4444"))
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

    # --- Summary table
    rows = []
    for i, s in enumerate(setups_sorted):
        verdict, vcolor, _ = _compute_verdict(s)
        long = s.direction == "long"
        tone = "#22c55e" if long else "#ef4444"
        arrow = "▲" if long else "▼"
        targets_html = " · ".join(f"${t:.2f}" for t in s.targets)
        rows.append(f"""
          <tr class="setup-row row-{verdict.lower().replace(' ', '-')} dir-{s.direction}"
              data-symbol="{s.symbol}" data-verdict="{verdict.lower().replace(' ', '-')}" data-direction="{s.direction}">
            <td class="actions">
              <button class="star-btn" data-symbol="{s.symbol}" onclick="toggleStar(event,'{s.symbol}')">☆</button>
              <button class="bell-btn" data-symbol="{s.symbol}" data-price="{s.current_price:.2f}" onclick="setAlarm(event,'{s.symbol}',{s.current_price:.2f})">🔔</button>
            </td>
            <td><span class="verdict-pill" style="background:{vcolor};color:#000">{verdict}</span></td>
            <td><b><a class="sym-link" onclick="document.getElementById('chart-{i}').scrollIntoView({{behavior:'smooth'}})">{s.symbol}</a></b></td>
            <td style="color:{tone}">{arrow} {s.name}</td>
            <td style="text-align:right">${s.current_price:.2f}</td>
            <td style="text-align:right">${s.entry:.2f}</td>
            <td style="text-align:right;color:#ef4444">${s.stop_loss:.2f}</td>
            <td style="text-align:right;color:#22c55e">{targets_html}</td>
            <td style="text-align:right">{s.risk_reward:.2f}R<br><span style="color:#94a3b8">{s.move_pct:+.1f}%</span></td>
            <td style="text-align:right">{int(s.conviction*100)}%</td>
            <td style="font-size:11px;color:#94a3b8">{s.reasoning}<br><i>{s.citation}</i></td>
          </tr>
        """)
    table_rows = "\n".join(rows) if rows else "<tr><td colspan=11 style='text-align:center;padding:24px;color:#94a3b8'>No setups firing right now — try again later or add more tickers.</td></tr>"

    # Price map exposed to client-side JS for alarm checking
    import json as _json
    price_map_json = _json.dumps({s.symbol: s.current_price for s in setups_sorted})

    # Verdict summary chips above table
    legend_html = ""
    for label, color in [("STRONG TAKE", "#22c55e"), ("TAKE", "#86efac"), ("MARGINAL", "#f59e0b"), ("AVOID", "#ef4444")]:
        n = verdict_counts.get(label, 0)
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
        Combines all setups' entry/stop/targets PLUS support/resistance from snap.
        Each line: {price, color, lineStyle (0=solid,2=dashed), lineWidth, title}.
        """
        lines: list[dict] = []
        for s in s_list:
            tone = "#22c55e" if s.direction == "long" else "#ef4444"
            lines.append({"price": s.entry,     "color": "#fbbf24", "lineStyle": 0, "lineWidth": 2, "title": f"Entry ${s.entry:.2f}"})
            lines.append({"price": s.stop_loss, "color": "#ef4444", "lineStyle": 0, "lineWidth": 2, "title": f"Stop ${s.stop_loss:.2f}"})
            for ti, t in enumerate(s.targets[:2], 1):
                lines.append({"price": t,       "color": "#22c55e", "lineStyle": 2, "lineWidth": 2, "title": f"T{ti} ${t:.2f}"})
        if snap is not None:
            for sup in (snap.support_levels or [])[-3:]:
                lines.append({"price": sup, "color": "#22c55e88", "lineStyle": 2, "lineWidth": 1, "title": f"S ${sup:.2f}"})
            for res in (snap.resistance_levels or [])[-3:]:
                lines.append({"price": res, "color": "#ef444488", "lineStyle": 2, "lineWidth": 1, "title": f"R ${res:.2f}"})
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
        charts.append(f"""
        <div class="ticker-block" id="chart-{i}">
          <h2>{s.symbol} <span class="tv-link">·
            <a href="https://www.tradingview.com/chart/?symbol={tv}" target="_blank">open on TradingView →</a>
          </span></h2>
          <div class="chart-row">
            <div class="lwc-wrap">{chart_body}<div class="lwc-legend" id="lg_{i}"></div></div>
            <div class="setups-side">{levels_html}</div>
          </div>
        </div>
        """)
    charts_html = "\n".join(charts)

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
            f'<button class="star-btn" data-symbol="{sym}" onclick="toggleStar(event,\'{sym}\')">☆</button> '
            f'<b>{sym}</b> <span class="watch-price">${px:.2f}</span>'
            f'<button class="bell-btn" data-symbol="{sym}" data-price="{px:.2f}" '
            f'onclick="setAlarm(event,\'{sym}\',{px:.2f})">🔔</button>'
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
        snap_blocks.append(f"""
        <div class="ticker-block" id="chart-{snap_idx}">
          <h2>{snap.symbol} <span class="tv-link">· no CC setup right now — chart only ·
            <a href="https://www.tradingview.com/chart/?symbol={tv}" target="_blank">open on TradingView →</a>
          </span></h2>
          <div class="chart-row">
            <div class="lwc-wrap">{_snap_chart_body(snap, snap_idx, chart_data_by_symbol)}<div class="lwc-legend" id="lg_snap_{snap_idx}"></div></div>
            <div class="setups-side">
              <div class="setup-card">
                <div class="setup-head" style="color:#94a3b8;display:flex;justify-content:space-between;align-items:center">
                  <span>📊 {snap.symbol} · CC context</span>
                  <span class="snap-actions">
                    <button class="star-btn" data-symbol="{snap.symbol}" onclick="toggleStar(event,'{snap.symbol}')" title="Add to My Watchlist">☆</button>
                    <button class="bell-btn" data-symbol="{snap.symbol}" data-price="{snap.current_price:.2f}" onclick="setAlarm(event,'{snap.symbol}',{snap.current_price:.2f})" title="Set price alarm">🔔</button>
                    <button class="add-list-btn" onclick="addToMyListBySymbol(event,'{snap.symbol}')" title="Add to My Watchlist">+ List</button>
                    <button class="add-list-btn" onclick="openManualSetupModal('{snap.symbol}',{snap.current_price:.2f})" title="Add manual setup for this ticker">✎ Setup</button>
                  </span>
                </div>
                {_render_key_levels_panel(snap)}
                {_render_flags(snap.context_flags)}
                <div class="rationale" style="margin-top:10px">
                  No Chart Champions setup is firing on this ticker right now. Use the chart + values above to monitor it. When a CC pattern develops (EMA pullback, CC region retracement, S/R flip, etc.) it will appear in the table on the next scan.
                </div>
              </div>
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
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
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

  /* Lightweight Charts container — entry/stop/targets drawn directly */
  .lwc-wrap {{ background:#0a0f1c; border-radius:8px; padding:8px; position:relative; }}
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
    <form method="GET" action="/" class="search-form">
      <input name="symbols" id="search-input" list="ticker-suggestions"
             placeholder="🔍 Scan ad-hoc — type 'bitcoin', 'apple', 'GLD', 'AAPL', 'BTC-USD'..." autocomplete="off"/>
      <datalist id="ticker-suggestions">{ticker_suggestions_html}</datalist>
      <button type="submit">Scan</button>
      <a href="/" class="reset-link">↩ Default watchlist</a>
    </form>

    <div class="mylist-bar">
      <b>⭐ My Watchlist:</b>
      <span id="mylist-chips" class="mylist-chips"></span>
      <span id="mylist-empty" style="color:#64748b">empty — star any ticker (☆) to add, or click +Add</span>
      <button class="ml-btn" onclick="addToMyList()">+ Add ticker</button>
      <button class="ml-btn" id="scan-my-list" onclick="scanMyList()" style="display:none">🎯 Scan my list now</button>
      <button class="ml-btn danger" id="clear-my-list" onclick="clearMyList()" style="display:none">Clear all</button>
    </div>

    <div class="filter-bar">
      <button class="filter-btn active" data-filter="all">All</button>
      <button class="filter-btn" data-filter="starred">⭐ My list</button>
      <button class="filter-btn" data-filter="strong-take">🟢 STRONG TAKE</button>
      <button class="filter-btn" data-filter="take">🟢 TAKE</button>
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
      <th>⭐🔔</th>
      <th>Verdict</th>
      <th>Symbol</th><th>Setup</th>
      <th style="text-align:right">Price</th>
      <th style="text-align:right">Entry</th>
      <th style="text-align:right">Stop</th>
      <th style="text-align:right">Targets</th>
      <th style="text-align:right">R:R / Move</th>
      <th style="text-align:right">Conv</th>
      <th>Rationale (CC citation)</th>
    </tr></thead>
    <tbody>{table_rows}</tbody>
  </table>

  {charts_html}

  <div id="manual-section">
    <h2 style="margin-top:32px">📝 My Manual Setups <span class="sub" id="manual-count">(empty)</span></h2>
    <div id="manual-cards"></div>
  </div>

  {watching_html}

  {snapshots_html}

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

  <div class="journal-panel" id="journal-panel">
    <h3>📒 Trade Journal</h3>
    <div class="sub" style="margin:0 0 10px 0">Stored in your browser — survives reloads but not cache wipes. Use the buttons on each setup card to log a trade.</div>
    <div id="journal-rows"></div>
    <div style="margin-top:10px;color:#94a3b8;font-size:11px">
      Stats: <span id="journal-stats">no trades yet</span>
      <button class="tools-btn" style="margin-left:12px" onclick="exportJournal()">⬇ Export CSV</button>
    </div>
  </div>

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
    function initLightweightCharts() {{
      if (typeof LightweightCharts === 'undefined') {{
        console.warn('LightweightCharts library not loaded');
        return;
      }}
      document.querySelectorAll('.lwc-chart').forEach(function(div) {{
        var sym = div.getAttribute('data-symbol');
        var data = window.cc_charts_data[sym];
        if (!data || !data.candles || !data.candles.length) {{
          div.innerHTML = '<div style="padding:30px;color:#64748b">No chart data for ' + sym + '</div>';
          return;
        }}
        var chart = LightweightCharts.createChart(div, {{
          layout:        {{ background: {{ type:'solid', color:'#0a0f1c' }}, textColor:'#94a3b8' }},
          grid:          {{ vertLines: {{ color:'#1e293b' }}, horzLines: {{ color:'#1e293b' }} }},
          rightPriceScale: {{ borderColor:'#1e293b' }},
          timeScale:     {{ borderColor:'#1e293b', timeVisible:false }},
          crosshair:     {{ mode: 1 }},
          autoSize:      true,
        }});
        var candleSeries = chart.addCandlestickSeries({{
          upColor:'#22c55e', downColor:'#ef4444',
          borderUpColor:'#22c55e', borderDownColor:'#ef4444',
          wickUpColor:'#22c55e', wickDownColor:'#ef4444',
        }});
        candleSeries.setData(data.candles);

        // Volume — separate scale at bottom
        var volSeries = chart.addHistogramSeries({{
          priceFormat: {{ type:'volume' }},
          priceScaleId: '',
          color:'#22c55e55',
        }});
        volSeries.priceScale().applyOptions({{ scaleMargins: {{ top:0.85, bottom:0 }} }});
        if (data.volume && data.volume.length) volSeries.setData(data.volume);

        // EMA overlays
        function addEMA(series, color, title) {{
          if (!series || !series.length) return null;
          var s = chart.addLineSeries({{
            color: color, lineWidth: 1, title: title,
            lastValueVisible:false, priceLineVisible:false,
          }});
          s.setData(series);
          return s;
        }}
        addEMA(data.ema_55,  '#94a3b8', 'EMA 55');
        addEMA(data.ema_100, '#cbd5e1', 'EMA 100');
        addEMA(data.ema_200, '#64748b', 'EMA 200');

        // Horizontal price-lines: entry, stop, targets, S/R
        var rawLines = div.getAttribute('data-lines') || '[]';
        var lines;
        try {{ lines = JSON.parse(rawLines); }} catch(_) {{ lines = []; }}
        lines.forEach(function(l) {{
          candleSeries.createPriceLine({{
            price: l.price,
            color: l.color,
            lineWidth: l.lineWidth || 2,
            lineStyle: l.lineStyle === 2 ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: l.title || '',
          }});
        }});

        // Legend in the top-left of this chart
        var legendId = div.id.replace('lwc_', 'lg_');
        var legend = document.getElementById(legendId);
        if (legend) {{
          var legendRows = '<div class="lg-row"><span class="lg-dot" style="background:#22c55e"></span> Bull candle</div>'
                         + '<div class="lg-row"><span class="lg-dot" style="background:#94a3b8"></span> EMA 55</div>'
                         + '<div class="lg-row"><span class="lg-dot" style="background:#cbd5e1"></span> EMA 100</div>'
                         + '<div class="lg-row"><span class="lg-dot" style="background:#64748b"></span> EMA 200</div>';
          if (lines.length) {{
            legend.innerHTML = legendRows + '<div class="lg-row"><span class="lg-px">' + sym + '</span></div>';
          }} else {{
            legend.innerHTML = legendRows;
          }}
        }}

        chart.timeScale().fitContent();
        // Resize chart when window resizes
        new ResizeObserver(function() {{
          chart.applyOptions({{ width: div.clientWidth, height: div.clientHeight }});
        }}).observe(div);
      }});
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
    }}

    // --- Custom watchlist (= the user's stars) ----------------------------
    function renderMyListBar() {{
      const stars = getStars();
      const chips = document.getElementById('mylist-chips');
      const empty = document.getElementById('mylist-empty');
      const scanBtn = document.getElementById('scan-my-list');
      const clearBtn = document.getElementById('clear-my-list');
      if (!chips) return;
      if (stars.length === 0) {{
        chips.innerHTML = '';
        if (empty) empty.style.display = '';
        if (scanBtn) scanBtn.style.display = 'none';
        if (clearBtn) clearBtn.style.display = 'none';
      }} else {{
        chips.innerHTML = stars.map(s =>
          `<span class="mylist-chip">${{s}} <span class="x" onclick="removeFromMyList('${{s}}')">✕</span></span>`
        ).join('');
        if (empty) empty.style.display = 'none';
        if (scanBtn) scanBtn.style.display = '';
        if (clearBtn) clearBtn.style.display = '';
      }}
    }}
    function addToMyList() {{
      const raw = prompt("Add ticker(s) to your watchlist:\\n(comma-separated, e.g.  AAPL, MSFT, BTC-USD, bitcoin, apple)");
      if (!raw) return;
      const stars = getStars();
      raw.split(',').map(x => x.trim()).filter(Boolean).forEach(t => {{
        const sym = t.toUpperCase();
        if (sym && !stars.includes(sym)) stars.push(sym);
      }});
      saveStars(stars);
      applyStarUI();
      renderMyListBar();
    }}
    function removeFromMyList(sym) {{
      saveStars(getStars().filter(s => s !== sym));
      applyStarUI();
      renderMyListBar();
      applyFilter();
    }}
    function clearMyList() {{
      if (!confirm('Remove ALL tickers from your watchlist?')) return;
      saveStars([]);
      applyStarUI();
      renderMyListBar();
      applyFilter();
    }}
    function scanMyList() {{
      const stars = getStars();
      if (!stars.length) return;
      window.location.href = '/?symbols=' + encodeURIComponent(stars.join(','));
    }}

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
      const stars = new Set(getStars());
      document.querySelectorAll('.setup-row').forEach(tr => {{
        const sym = tr.dataset.symbol;
        const verdict = tr.dataset.verdict;
        const dir = tr.dataset.direction;
        let show = true;
        if (currentFilter === 'starred')      show = stars.has(sym);
        else if (currentFilter === 'strong-take') show = verdict === 'strong-take';
        else if (currentFilter === 'take')        show = verdict === 'take' || verdict === 'strong-take';
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
      if (stars.indexOf(sym) < 0) stars.push(sym);
      saveStars(stars);
      applyStarUI();
      renderMyListBar();
      showToast('⭐ ' + sym + ' added to your watchlist');
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

    window.addEventListener('load', () => {{
      applyStarUI();
      applyBellUI();
      applyFilter();
      checkAlarms();
      renderMyListBar();
      initLightweightCharts();
      loadSavedAccount();
      renderJournal();
      renderManualSetups();
      if (Notification.permission === 'default') Notification.requestPermission();
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
        detect_ema_pullback:      "EMA Pullback",
        detect_cc_region_pullback: "CC Region",
        detect_sr_flip:            "S/R Flip",
        detect_volume_spike:       "Volume Spike",
        detect_inside_day:         "Inside Day",
        detect_rsi_reversal:       "RSI Reversal",
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

    def _build_chart_data(sym_u: str, daily_df: pd.DataFrame) -> dict:
        """Serialize the last ~250 daily bars + EMA overlays for Lightweight Charts.
        Format follows lightweight-charts' time-and-value convention:
          candles: [{time, open, high, low, close}, ...]
          volume:  [{time, value, color}, ...]
          ema_55 / ema_100 / ema_200: [{time, value}, ...]   (NaN entries dropped)
        """
        d = daily_df.tail(260).copy()
        # Time is YYYY-MM-DD strings — lightweight-charts accepts those for daily bars.
        times = [t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t) for t in d.index]
        candles = []
        for ts, row in zip(times, d.itertuples(index=False)):
            o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
            candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        # Volume series with green/red coloring vs prior close.
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
            sr = support_resistance(daily_df.tail(200))
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
        return Snapshot(
            symbol=sym_u,
            current_price=px,
            ema_55=e55, ema_100=e100, ema_200=e200, rsi_14=rsi_v,
            support_levels=sr.get("support", [])[-3:],
            resistance_levels=sr.get("resistance", [])[-3:],
            bid=bid, ask=ask, spread_pct=spread_pct, avg_volume=avg_vol,
            context_flags=build_context(
                daily_df=daily_df, symbol=sym_u,
                setup_direction="long",
                spy_trend=spy_trend,
                sector_trend=sector_trends.get(etf_u, "side"),
                weekly_df=weekly_df,
            ),
        )

    for i, sym in enumerate(tickers, 1):
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
            # Serialize chart data for any ticker that will get a chart card
            # (i.e., has setups OR will be a snapshot card).
            if setups or always_show:
                try:
                    chart_data_by_symbol[sym_u] = _build_chart_data(sym_u, daily_df)
                except Exception as e:
                    print(f"    [warn] chart data build failed for {sym_u}: {e}")
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
                    _, _, html = run_full_scan(tickers, always_show=True)
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
