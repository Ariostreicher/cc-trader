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
import webbrowser
from dataclasses import dataclass, field, asdict
from datetime import datetime
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
# Detectors — Chart Champions rules
# ---------------------------------------------------------------------------
def detect_ema_pullback(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """First 18.pdf p.67 — EMA 55/100/200 alignment + pullback."""
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

    if e55_ > e100_ > e200_ and px > e55_ and (px - e55_) <= atrv:
        pivots = swing_pivots(df.tail(120), n=5)
        lows = [p.price for p in pivots if p.kind == "low"]
        highs = [p.price for p in pivots if p.kind == "high"]
        if not lows or not highs:
            return None
        # CC tight stop: just below EMA55 (soft) — invalidates the pullback thesis.
        # If a more recent micro-swing low is BETWEEN EMA55 and EMA100, use that.
        micro_low = lows[-1] if e100_ <= lows[-1] <= e55_ else (e55_ - 0.5 * atrv)
        stop = min(micro_low, e55_ - 0.3 * atrv)
        target1 = highs[-1]
        target2 = fibonacci_levels(highs[-1], lows[-1])["1.272"]
        return Setup(
            symbol, "EMA 55/100/200 Pullback (long)", "long",
            entry=px, stop_loss=stop, targets=[target1, target2],
            current_price=px, conviction=0.78,
            reasoning=f"Bull alignment 55>100>200 (EMA55 ${e55_:.2f} > 100 ${e100_:.2f} > 200 ${e200_:.2f}). Price ${px:.2f} pulling back to EMA55 = high-probability long.",
            citation="First 18.pdf p.67",
        )

    if e55_ < e100_ < e200_ and px < e55_ and (e55_ - px) <= atrv:
        pivots = swing_pivots(df.tail(120), n=5)
        lows = [p.price for p in pivots if p.kind == "low"]
        highs = [p.price for p in pivots if p.kind == "high"]
        if not lows or not highs:
            return None
        micro_high = highs[-1] if e55_ <= highs[-1] <= e100_ else (e55_ + 0.5 * atrv)
        stop = max(micro_high, e55_ + 0.3 * atrv)
        target1 = lows[-1]
        target2 = lows[-1] - (highs[-1] - lows[-1]) * 0.272
        return Setup(
            symbol, "EMA 55/100/200 Pullback (short)", "short",
            entry=px, stop_loss=stop, targets=[target1, target2],
            current_price=px, conviction=0.72,
            reasoning=f"Bear alignment 55<100<200 (EMA55 ${e55_:.2f} < 100 ${e100_:.2f} < 200 ${e200_:.2f}). Price ${px:.2f} pulling up to EMA55.",
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

    if a.kind == "low" and b.kind == "high" and b.price > a.price:
        lo, hi = cc_region(b.price, a.price)
        if float(last["low"]) <= hi and px > lo:
            fib = fibonacci_levels(b.price, a.price)
            return Setup(
                symbol, "CC Region Pullback (long)", "long",
                entry=px, stop_loss=lo - 0.3 * atrv,
                targets=[b.price, fib["1.272"]],
                current_price=px, conviction=0.78,
                reasoning=f"Price wicked into CC region ${lo:.2f}–${hi:.2f} (0.618–0.66 retracement) and closed above.",
                citation="First 18.pdf p.1, p.63",
            )
    if a.kind == "high" and b.kind == "low" and b.price < a.price:
        lo, hi = cc_region(a.price, b.price)
        if float(last["high"]) >= lo and px < hi:
            fib = fibonacci_levels(a.price, b.price)
            return Setup(
                symbol, "CC Region Pullback (short)", "short",
                entry=px, stop_loss=hi + 0.3 * atrv,
                targets=[b.price, b.price - (fib["1.272"] - a.price)],
                current_price=px, conviction=0.72,
                reasoning=f"Bearish CC region rejection at ${lo:.2f}–${hi:.2f}.",
                citation="First 18.pdf p.1 (inverted)",
            )
    return None


def detect_sr_flip(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """First 18.pdf p.61 — broken level retested in the opposite role."""
    sr = support_resistance(df.tail(200))
    last = df.iloc[-1]
    px = float(last["close"])
    lo = float(last["low"])
    hi = float(last["high"])
    atrv = float(atr(df, 14).iloc[-1])

    for level in sr["resistance"]:
        if lo <= level <= px and (px - level) <= 0.5 * atrv:
            higher = [r for r in sr["resistance"] if r > px]
            t1 = higher[0] if higher else px + 2 * (px - level)
            return Setup(
                symbol, "Resistance Flip to Support (long)", "long",
                entry=px, stop_loss=level - 0.5 * atrv,
                targets=[t1, px + 3 * (px - level)],
                current_price=px, conviction=0.72,
                reasoning=f"Former resistance ${level:.2f} broken and retested as support.",
                citation="First 18.pdf p.61",
            )
    for level in sr["support"]:
        if px <= level <= hi and (level - px) <= 0.5 * atrv:
            lower = [s for s in sr["support"] if s < px]
            t1 = lower[-1] if lower else px - 2 * (level - px)
            return Setup(
                symbol, "Support Flip to Resistance (short)", "short",
                entry=px, stop_loss=level + 0.5 * atrv,
                targets=[t1, px - 3 * (level - px)],
                current_price=px, conviction=0.68,
                reasoning=f"Former support ${level:.2f} broken and retested as resistance.",
                citation="First 18.pdf p.61 (inverted)",
            )
    return None


def detect_volume_spike(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Second 18.pdf p.18 — new 20-bar high/low on 2x avg volume."""
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

    if px > high20:
        return Setup(
            symbol, "Volume Spike Breakout (long)", "long",
            entry=px, stop_loss=high20 - 0.5 * atrv,
            targets=[px + 1.5 * (px - high20 + 0.5 * atrv), px + 3 * (px - high20 + 0.5 * atrv)],
            current_price=px, conviction=0.72,
            reasoning=f"New 20-bar high on {float(last['volume'])/vol_avg:.1f}× average volume.",
            citation="Second 18.pdf p.18",
        )
    if px < low20:
        return Setup(
            symbol, "Volume Spike Breakdown (short)", "short",
            entry=px, stop_loss=low20 + 0.5 * atrv,
            targets=[px - 1.5 * (low20 + 0.5 * atrv - px), px - 3 * (low20 + 0.5 * atrv - px)],
            current_price=px, conviction=0.70,
            reasoning=f"New 20-bar low on {float(last['volume'])/vol_avg:.1f}× volume.",
            citation="Second 18.pdf p.18 (inverted)",
        )
    return None


def detect_inside_day(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """First 18.pdf p.43 — inside day breakout."""
    if len(df) < 4:
        return None
    d2, d1, d0 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if not (d1["high"] <= d2["high"] and d1["low"] >= d2["low"]):
        return None
    atrv = float(atr(df, 14).iloc[-1])
    px = float(d0["close"])
    range_ = float(d1["high"]) - float(d1["low"])

    if px > d1["high"]:
        return Setup(
            symbol, "Inside Day Breakout (long)", "long",
            entry=px, stop_loss=float(d1["low"]) - 0.2 * atrv,
            targets=[px + range_, px + 2 * range_],
            current_price=px, conviction=0.66,
            reasoning=f"Inside-day breakout above ${d1['high']:.2f}.",
            citation="First 18.pdf p.43",
        )
    if px < d1["low"]:
        return Setup(
            symbol, "Inside Day Breakdown (short)", "short",
            entry=px, stop_loss=float(d1["high"]) + 0.2 * atrv,
            targets=[px - range_, px - 2 * range_],
            current_price=px, conviction=0.64,
            reasoning=f"Inside-day breakdown below ${d1['low']:.2f}.",
            citation="First 18.pdf p.43 (inverted)",
        )
    return None


def detect_rsi_reversal(symbol: str, df: pd.DataFrame) -> Optional[Setup]:
    """Second 18.pdf p.1 — Entry Triggers / RSI extremes."""
    r = rsi(df["close"], 14)
    if len(r.dropna()) < 3:
        return None
    r_now, r_prev = float(r.iloc[-1]), float(r.iloc[-2])
    last = df.iloc[-1]
    px = float(last["close"])
    atrv = float(atr(df, 14).iloc[-1])
    win = df.tail(20)

    if r_prev < 30 and r_now >= 30:
        lo = float(win["low"].min())
        hi = float(win["high"].max())
        stop = lo - 0.3 * atrv
        return Setup(
            symbol, "RSI Oversold Reversal (long)", "long",
            entry=px, stop_loss=stop,
            targets=[hi, px + 2 * (px - stop)],
            current_price=px, conviction=0.55,
            reasoning=f"RSI exiting oversold ({r_prev:.1f}→{r_now:.1f}).",
            citation="Second 18.pdf p.1",
        )
    if r_prev > 70 and r_now <= 70:
        lo = float(win["low"].min())
        hi = float(win["high"].max())
        stop = hi + 0.3 * atrv
        return Setup(
            symbol, "RSI Overbought Reversal (short)", "short",
            entry=px, stop_loss=stop,
            targets=[lo, px - 2 * (stop - px)],
            current_price=px, conviction=0.55,
            reasoning=f"RSI exiting overbought ({r_prev:.1f}→{r_now:.1f}).",
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
                auto_adjust=False, progress=False, threads=False,
            )
            weekly = yf.download(
                sym, period="3y", interval="1wk",
                auto_adjust=False, progress=False, threads=False,
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
) -> str:
    snapshots = snapshots or []
    levels_by_symbol = levels_by_symbol or {}
    watches = watches or []
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

    # Autocomplete suggestions: every alias key + the watchlist itself.
    # Browser shows these as a dropdown when the user types in the search box.
    _suggestion_set: set[str] = set(TICKER_ALIASES.keys()) | set(TICKER_ALIASES.values())
    _suggestion_set |= {s.symbol for s in setups_sorted}
    _suggestion_set |= {s.symbol for s in snapshots}
    ticker_suggestions_html = "".join(
        f'<option value="{sym}"></option>' for sym in sorted(_suggestion_set) if sym
    )

    # --- One TradingView widget per ticker with a setup
    charts = []
    seen_symbols: set[str] = set()
    for i, s in enumerate(setups_sorted):
        if s.symbol in seen_symbols:
            continue
        seen_symbols.add(s.symbol)
        tv = _tv_symbol(s.symbol)
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
            </div>
            """
        charts.append(f"""
        <div class="ticker-block" id="chart-{i}">
          <h2>{s.symbol} <span class="tv-link">·
            <a href="https://www.tradingview.com/chart/?symbol={tv}" target="_blank">open on TradingView →</a>
          </span></h2>
          <div class="chart-row">
            <div class="tv-widget-wrap">
              <div class="tradingview-widget-container">
                <div id="tv_{i}"></div>
                <script type="text/javascript">
                  new TradingView.widget({{
                    "container_id": "tv_{i}",
                    "autosize": true,
                    "symbol": "{tv}",
                    "interval": "D",
                    "timezone": "America/New_York",
                    "theme": "dark",
                    "style": "1",
                    "locale": "en",
                    "toolbar_bg": "#0a0f1c",
                    "enable_publishing": false,
                    "hide_top_toolbar": false,
                    "hide_legend": false,
                    "save_image": false,
                    "studies": [
                      "MAExp@tv-basicstudies",
                      "MAExp@tv-basicstudies",
                      "MAExp@tv-basicstudies",
                      "RSI@tv-basicstudies"
                    ],
                    "studies_overrides": {{
                      "moving average exponential.length": 55,
                      "moving average exponential.color": "#94a3b8"
                    }}
                  }});
                </script>
              </div>
            </div>
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
            <div class="tv-widget-wrap">
              <div class="tradingview-widget-container">
                <div id="tv_snap_{snap_idx}"></div>
                <script type="text/javascript">
                  new TradingView.widget({{
                    "container_id": "tv_snap_{snap_idx}",
                    "autosize": true,
                    "symbol": "{tv}",
                    "interval": "D", "timezone": "America/New_York",
                    "theme": "dark", "style": "1", "locale": "en",
                    "toolbar_bg": "#0a0f1c", "enable_publishing": false,
                    "studies": ["MAExp@tv-basicstudies","MAExp@tv-basicstudies","MAExp@tv-basicstudies","RSI@tv-basicstudies"]
                  }});
                </script>
              </div>
            </div>
            <div class="setups-side">
              <div class="setup-card">
                <div class="setup-head" style="color:#94a3b8">📊 Live snapshot · CC context</div>
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

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>CC Trader — Live Setups</title>
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
  .tv-widget-wrap {{ background:#0a0f1c; border-radius:8px; overflow:hidden; min-height:720px; }}
  .tradingview-widget-container {{ height:720px; width:100%; }}
  .tradingview-widget-container > div {{ height:720px !important; width:100% !important; }}
  .tradingview-widget-container iframe {{ height:720px !important; width:100% !important; border:0 !important; }}

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

  {watching_html}

  {snapshots_html}

  <div class="footer">
    Methodology source: Chart Champions PDFs uploaded by operator. Run the
    script again any time — chart data refreshes live via TradingView,
    setups recompute against Yahoo Finance daily bars.
  </div>

  <script>
    // Map of current prices from this scan, exposed for client-side alarm checks.
    window.cc_prices = {price_map_json};

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

    window.addEventListener('load', () => {{
      applyStarUI();
      applyBellUI();
      applyFilter();
      checkAlarms();
      renderMyListBar();
      if (Notification.permission === 'default') Notification.requestPermission();
    }});
  </script>
</body></html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
MIN_RISK_REWARD = 1.0  # CC discipline — skip anything below


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
                             auto_adjust=False, progress=False, threads=False)
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
    print("  Fetching market regime (SPY)...")
    spy_trend = _scan_index_trend("SPY")
    print(f"    SPY trend: {spy_trend}")

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
    all_watches: list[WatchItem] = []

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
        return Snapshot(
            symbol=sym_u,
            current_price=px,
            ema_55=e55, ema_100=e100, ema_200=e200, rsi_14=rsi_v,
            support_levels=sr.get("support", [])[-3:],
            resistance_levels=sr.get("resistance", [])[-3:],
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
        all_setups.extend(setups)

        # Always-on: build a key-levels Snapshot for every ticker.
        # This powers the "Key Levels" panel on every setup card, plus the
        # standalone snapshot card when no setup fired.
        if daily_df is not None and not daily_df.empty:
            snap_levels = _build_snapshot(sym_u, daily_df, weekly_df, etf)
            levels_by_symbol[sym_u] = snap_levels
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
    html = render_html(
        all_setups, len(tickers), duration,
        snapshots=snapshots,
        levels_by_symbol=levels_by_symbol,
        watches=all_watches,
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
                    _, _, html = run_full_scan(tickers)
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
    args = [a for a in args if a not in ("--serve", "--live")]

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

    if serve_mode:
        return serve_live(tickers, port=port, refresh_seconds=refresh_seconds, cache_seconds=cache_seconds)

    # one-shot mode (the original behavior)
    _, _, html = run_full_scan(tickers)
    out = Path("cc_setups_report.html").resolve()
    out.write_text(html, encoding="utf-8")
    print(f"✓ Report saved to:  {out}")
    print("Opening in your browser...")
    webbrowser.open(f"file://{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
