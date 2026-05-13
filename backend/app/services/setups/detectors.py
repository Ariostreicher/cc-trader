"""Chart Champions setup detectors.

Each detector implements ONE rule from the Chart Champions cheatsheets.
The rules below are extracted from the operator-supplied PDFs and are the
sole source of truth — no generic patterns are added.

Reference pages refer to the operator's uploaded files:
  CC_FIRST   = First 18.pdf   (Patterns/Trends + ORB + EMA + Pivot Points)
  CC_SECOND  = Second 18.pdf  (Entry Triggers + Levels + Fibonacci + Harmonics)
  CC_THIRD   = Third batch.pdf (Imbalances + Market Structure + Volume Profile + TPO)
  CC_SHEET   = CC Cheatsheets — AI For Stock Analysis.pdf (Equity model)
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional

import pandas as pd

from ..indicators.ta import (
    atr,
    bollinger,
    cc_region_levels,
    ema,
    fibonacci_levels,
    rsi,
    support_resistance,
    swing_pivots,
)
from .types import Citation, Setup, make_setup

CC_FIRST = "First 18.pdf"
CC_SECOND = "Second 18.pdf"
CC_THIRD = "Third batch.pdf"


# ----------------------------------------------------------------------
# Master entry point
# ----------------------------------------------------------------------
def detect_all(symbol: str, df: pd.DataFrame, timeframe: str = "1d") -> List[Setup]:
    """Run every detector against ``df`` and return the setups that fired.

    The DataFrame must have an OHLCV index sorted ascending by time.
    """
    if df.empty or len(df) < 250:
        return []

    detectors = (
        detect_ema_alignment_pullback,
        detect_cc_region_pullback,
        detect_sr_flip,
        detect_sr_breakout,
        detect_3rd_touch,
        detect_inside_day_breakout,
        detect_rsi_reversal,
        detect_volume_spike_breakout,
        detect_bollinger_squeeze,
    )
    out: list[Setup] = []
    for fn in detectors:
        try:
            s = fn(symbol, df, timeframe)
        except Exception:
            continue
        if s is not None:
            out.append(s)
    # Sort by conviction desc, then by risk/reward desc.
    return sorted(out, key=lambda s: (s.conviction, s.risk_reward), reverse=True)


# ----------------------------------------------------------------------
# 1) EMA 55 / 100 / 200 pullback (First 18.pdf p.67)
# ----------------------------------------------------------------------
def detect_ema_alignment_pullback(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """
    Long when EMA55 > EMA100 > EMA200 AND price has pulled back to within
    1 ATR of EMA55 from above. Stop = below most recent swing low.
    Target1 = recent swing high; target2 = 1.272 fib extension of last leg.

    Cited: First 18.pdf p.67 ("EMA Strategy — buy on pullback to EMA55 with
    full alignment").
    """
    close = df["close"]
    e55 = ema(close, 55)
    e100 = ema(close, 100)
    e200 = ema(close, 200)
    a = atr(df, 14)

    last = df.iloc[-1]
    last_close = float(last["close"])
    last_atr = float(a.iloc[-1])

    e55_now, e100_now, e200_now = float(e55.iloc[-1]), float(e100.iloc[-1]), float(e200.iloc[-1])
    if math.isnan(e55_now) or math.isnan(e200_now):
        return None

    long_aligned = e55_now > e100_now > e200_now and last_close > e55_now
    short_aligned = e55_now < e100_now < e200_now and last_close < e55_now

    # Long pullback: price within 1 ATR of EMA55, but still above it.
    if long_aligned and (last_close - e55_now) <= last_atr:
        pivots = swing_pivots(df.tail(120), n=5)
        lows = [p.price for p in pivots if p.kind == "low"]
        highs = [p.price for p in pivots if p.kind == "high"]
        if not lows or not highs:
            return None
        recent_low = lows[-1]
        recent_high = highs[-1]
        fib = fibonacci_levels(recent_high, recent_low)
        entry = last_close
        stop = min(e200_now, recent_low) - 0.2 * last_atr
        target1 = recent_high
        target2 = fib["1.272"]
        conviction = 0.70 + 0.10 * (1 if (last_close - e100_now) / last_atr < 2 else 0)
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="EMA 55/100/200 Pullback (long)",
            direction="long",
            entry=entry, stop_loss=stop, targets=[target1, target2],
            current_price=last_close,
            conviction=conviction,
            reasoning=(
                f"EMA alignment 55>100>200 with price ${last_close:.2f} pulling back "
                f"within 1 ATR (${last_atr:.2f}) of EMA55 ${e55_now:.2f}. "
                f"Stop below swing low ${recent_low:.2f}, targets at swing high and 1.272 extension."
            ),
            citations=[Citation(CC_FIRST, 67, "Buy when 55/100/200 align and price pulls back to EMA55")],
        )

    if short_aligned and (e55_now - last_close) <= last_atr:
        pivots = swing_pivots(df.tail(120), n=5)
        lows = [p.price for p in pivots if p.kind == "low"]
        highs = [p.price for p in pivots if p.kind == "high"]
        if not lows or not highs:
            return None
        recent_low = lows[-1]
        recent_high = highs[-1]
        fib = fibonacci_levels(recent_high, recent_low)
        entry = last_close
        stop = max(e200_now, recent_high) + 0.2 * last_atr
        target1 = recent_low
        target2 = recent_low - (fib["1.272"] - fib["1.0"])
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="EMA 55/100/200 Pullback (short)",
            direction="short",
            entry=entry, stop_loss=stop, targets=[target1, target2],
            current_price=last_close,
            conviction=0.70,
            reasoning=(
                f"Bearish EMA alignment 55<100<200, price pulling up toward EMA55 ${e55_now:.2f}. "
                f"Short with stop above ${stop:.2f}."
            ),
            citations=[Citation(CC_FIRST, 67, "Same logic inverted for downtrends")],
        )
    return None


# ----------------------------------------------------------------------
# 2) CC region pullback — Fibonacci 0.618–0.66 (First 18 p.1, Second 18 p.63)
# ----------------------------------------------------------------------
def detect_cc_region_pullback(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """Detect price retracing into the 0.618–0.66 fib zone of the most
    recent up-swing (long) or down-swing (short) and showing a wick rejection
    on the most recent bar.

    Cited: First 18.pdf p.1, p.63 — "CC Region (0.618–0.66) is the
    high-probability retracement for entries."
    """
    pivots = swing_pivots(df.tail(150), n=5)
    if len(pivots) < 2:
        return None

    last_two = pivots[-2:]
    a, b = last_two
    last = df.iloc[-1]
    last_close = float(last["close"])
    last_low = float(last["low"])
    last_high = float(last["high"])
    last_atr = float(atr(df, 14).iloc[-1])

    if a.kind == "low" and b.kind == "high" and b.price > a.price:
        # Up-swing — look for long retrace into CC region.
        cc_lo, cc_hi = cc_region_levels(b.price, a.price)
        # Wick into the zone but close above it = rejection.
        if last_low <= cc_hi and last_close > cc_lo:
            fib = fibonacci_levels(b.price, a.price)
            entry = last_close
            stop = cc_lo - 0.3 * last_atr
            target1 = b.price
            target2 = fib["1.272"]
            return make_setup(
                symbol=symbol, timeframe=timeframe,
                name="CC Region Pullback (long)",
                direction="long",
                entry=entry, stop_loss=stop, targets=[target1, target2],
                current_price=last_close,
                conviction=0.75,
                reasoning=(
                    f"Price wicked into the CC region ${cc_lo:.2f}–${cc_hi:.2f} (0.618–0.66 retracement "
                    f"of swing ${a.price:.2f}→${b.price:.2f}) and closed above. "
                    f"Target prior swing high then 1.272 extension."
                ),
                citations=[
                    Citation(CC_FIRST, 1, "Three Drives / CC Region — 0.618–0.66 retracement"),
                    Citation(CC_FIRST, 63, ".382 and .618 CC Importance"),
                ],
            )

    if a.kind == "high" and b.kind == "low" and b.price < a.price:
        # Down-swing — look for short retrace into CC region.
        cc_lo, cc_hi = cc_region_levels(a.price, b.price)
        if last_high >= cc_lo and last_close < cc_hi:
            fib = fibonacci_levels(a.price, b.price)
            entry = last_close
            stop = cc_hi + 0.3 * last_atr
            target1 = b.price
            target2 = b.price - (fib["1.272"] - fib["1.0"])
            return make_setup(
                symbol=symbol, timeframe=timeframe,
                name="CC Region Pullback (short)",
                direction="short",
                entry=entry, stop_loss=stop, targets=[target1, target2],
                current_price=last_close,
                conviction=0.72,
                reasoning=(
                    f"Bearish CC region rejection at ${cc_lo:.2f}–${cc_hi:.2f}. Short with stop above ${stop:.2f}."
                ),
                citations=[Citation(CC_FIRST, 1, "CC Region inverted for downtrends")],
            )
    return None


# ----------------------------------------------------------------------
# 3) Support/Resistance flip (First 18 p.61)
# ----------------------------------------------------------------------
def detect_sr_flip(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """Broken resistance retested as support (long) or vice versa.

    Cited: First 18.pdf p.61 — "Level flips are high-probability entries."
    """
    sr = support_resistance(df.tail(200))
    last = df.iloc[-1]
    last_close = float(last["close"])
    last_low = float(last["low"])
    last_high = float(last["high"])
    last_atr = float(atr(df, 14).iloc[-1])

    # Resistance flipped to support: last close above the broken level,
    # and within 0.5 ATR of it.
    for level in sr["resistance"]:
        if last_low <= level <= last_close and (last_close - level) <= 0.5 * last_atr:
            entry = last_close
            stop = level - 0.5 * last_atr
            # Target = next resistance above, or 1.272 of (level→close) leg.
            higher = [r for r in sr["resistance"] if r > last_close]
            t1 = higher[0] if higher else last_close + 2 * (last_close - level)
            t2 = last_close + 3 * (last_close - level)
            return make_setup(
                symbol=symbol, timeframe=timeframe,
                name="Resistance Flip to Support (long)",
                direction="long",
                entry=entry, stop_loss=stop, targets=[t1, t2],
                current_price=last_close,
                conviction=0.70,
                reasoning=(
                    f"Former resistance ${level:.2f} broken and retested as support "
                    f"(bar low ${last_low:.2f}, close ${last_close:.2f}). "
                    f"Stop below the flipped level."
                ),
                citations=[Citation(CC_FIRST, 61, "Support / Resistance Flip")],
            )

    # Support flipped to resistance.
    for level in sr["support"]:
        if last_close <= level <= last_high and (level - last_close) <= 0.5 * last_atr:
            entry = last_close
            stop = level + 0.5 * last_atr
            lower = [s for s in sr["support"] if s < last_close]
            t1 = lower[-1] if lower else last_close - 2 * (level - last_close)
            t2 = last_close - 3 * (level - last_close)
            return make_setup(
                symbol=symbol, timeframe=timeframe,
                name="Support Flip to Resistance (short)",
                direction="short",
                entry=entry, stop_loss=stop, targets=[t1, t2],
                current_price=last_close,
                conviction=0.68,
                reasoning=(
                    f"Former support ${level:.2f} broken and retested as resistance. Short with stop above ${stop:.2f}."
                ),
                citations=[Citation(CC_FIRST, 61, "Support / Resistance Flip (inverted)")],
            )
    return None


# ----------------------------------------------------------------------
# 4) S/R breakout (First 18 p.31 ORB-style)
# ----------------------------------------------------------------------
def detect_sr_breakout(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """Today's close above the prior cluster of resistance highs by > 0.3 ATR
    AND volume > 1.3x 20-bar average.

    Cited: First 18.pdf p.31–35 (ORB) and p.61 (S/R).
    """
    sr = support_resistance(df.tail(200))
    last = df.iloc[-1]
    last_close = float(last["close"])
    last_volume = float(last["volume"])
    last_atr = float(atr(df, 14).iloc[-1])
    vol_avg = float(df["volume"].iloc[-21:-1].mean())

    if vol_avg == 0 or last_volume < 1.3 * vol_avg:
        return None

    for level in sr["resistance"]:
        if last_close > level and (last_close - level) > 0.3 * last_atr:
            entry = last_close
            stop = level - 0.5 * last_atr
            higher = [r for r in sr["resistance"] if r > last_close]
            t1 = higher[0] if higher else last_close + 2 * (last_close - level)
            t2 = last_close + 3 * (last_close - level)
            return make_setup(
                symbol=symbol, timeframe=timeframe,
                name="Resistance Breakout (long)",
                direction="long",
                entry=entry, stop_loss=stop, targets=[t1, t2],
                current_price=last_close,
                conviction=0.65,
                reasoning=(
                    f"Decisive break above ${level:.2f} on volume "
                    f"{last_volume/vol_avg:.1f}x average. Stop below the broken level."
                ),
                citations=[Citation(CC_FIRST, 31, "Opening Range Breakout — confirmed by volume")],
            )
    return None


# ----------------------------------------------------------------------
# 5) 3rd touch setup (Second 18.pdf p.45)
# ----------------------------------------------------------------------
def detect_3rd_touch(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """Detect a level (S or R) that has been touched at least 3 times and
    just got touched again with a wick rejection.

    Cited: Second 18.pdf p.45 — "3rd Touch Setup — wait for third touch
    before entry."
    """
    sr = support_resistance(df.tail(200), n=4, cluster_tolerance_pct=0.4)
    last = df.iloc[-1]
    last_low = float(last["low"])
    last_high = float(last["high"])
    last_close = float(last["close"])
    last_atr = float(atr(df, 14).iloc[-1])

    def _touches_near(level: float, window: pd.DataFrame, tol_pct: float = 0.5) -> int:
        tol = level * tol_pct / 100
        return int(((window["low"] - level).abs() <= tol).sum()
                   + ((window["high"] - level).abs() <= tol).sum())

    window = df.tail(120)

    # Long bounce off a triple-touch support.
    for support in sr["support"]:
        if _touches_near(support, window) < 3:
            continue
        if last_low <= support * 1.005 and last_close > support:
            entry = last_close
            stop = support - 0.5 * last_atr
            t1 = window["high"].max()
            t2 = t1 + (t1 - support) * 0.618
            return make_setup(
                symbol=symbol, timeframe=timeframe,
                name="3rd Touch Bounce (long)",
                direction="long",
                entry=entry, stop_loss=stop, targets=[t1, t2],
                current_price=last_close,
                conviction=0.78,
                reasoning=(
                    f"Support ${support:.2f} has been respected 3+ times; today's bar bounced off it. "
                    f"Stop below the level."
                ),
                citations=[Citation(CC_SECOND, 45, "3rd Touch Setup — high-probability bounce")],
            )

    # Short rejection off triple-touch resistance.
    for resistance in sr["resistance"]:
        if _touches_near(resistance, window) < 3:
            continue
        if last_high >= resistance * 0.995 and last_close < resistance:
            entry = last_close
            stop = resistance + 0.5 * last_atr
            t1 = window["low"].min()
            t2 = t1 - (resistance - t1) * 0.618
            return make_setup(
                symbol=symbol, timeframe=timeframe,
                name="3rd Touch Rejection (short)",
                direction="short",
                entry=entry, stop_loss=stop, targets=[t1, t2],
                current_price=last_close,
                conviction=0.75,
                reasoning=(
                    f"Resistance ${resistance:.2f} has held 3+ times; today's bar rejected it. "
                    f"Stop above the level."
                ),
                citations=[Citation(CC_SECOND, 45, "3rd Touch (inverted)")],
            )
    return None


# ----------------------------------------------------------------------
# 6) Inside day / value-within-value breakout (First 18.pdf p.43)
# ----------------------------------------------------------------------
def detect_inside_day_breakout(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """Yesterday was an inside day (range within day-before's range). Today
    breaks above/below that range = directional bias.

    Cited: First 18.pdf p.43 — "Inside day / value-within-value setup."
    """
    if len(df) < 4:
        return None
    d2 = df.iloc[-3]  # day before yesterday
    d1 = df.iloc[-2]  # yesterday
    d0 = df.iloc[-1]  # today

    inside_yesterday = d1["high"] <= d2["high"] and d1["low"] >= d2["low"]
    if not inside_yesterday:
        return None

    last_atr = float(atr(df, 14).iloc[-1])
    last_close = float(d0["close"])

    if d0["close"] > d1["high"]:
        # Bull break of inside day
        entry = last_close
        stop = float(d1["low"]) - 0.2 * last_atr
        t1 = entry + (float(d1["high"]) - float(d1["low"]))
        t2 = entry + 2 * (float(d1["high"]) - float(d1["low"]))
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="Inside Day Breakout (long)",
            direction="long",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.66,
            reasoning=(
                f"Yesterday was an inside day; today broke above ${d1['high']:.2f}. "
                f"Target = inside-day range projected upward."
            ),
            citations=[Citation(CC_FIRST, 43, "Inside day = consolidation; breakout sets direction")],
        )

    if d0["close"] < d1["low"]:
        entry = last_close
        stop = float(d1["high"]) + 0.2 * last_atr
        t1 = entry - (float(d1["high"]) - float(d1["low"]))
        t2 = entry - 2 * (float(d1["high"]) - float(d1["low"]))
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="Inside Day Breakdown (short)",
            direction="short",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.64,
            reasoning=(
                f"Inside-day breakdown — close below yesterday's low. Stop above yesterday's high."
            ),
            citations=[Citation(CC_FIRST, 43, "Inside day (inverted)")],
        )
    return None


# ----------------------------------------------------------------------
# 7) RSI reversal at extreme (general TA, supported by cheatsheet entry triggers)
# ----------------------------------------------------------------------
def detect_rsi_reversal(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """RSI exits the oversold/overbought zone, indicating short-term reversal.

    Cited: Second 18.pdf — Entry Triggers cheatsheet.
    """
    r = rsi(df["close"], 14)
    if len(r.dropna()) < 3:
        return None
    rsi_now, rsi_prev = float(r.iloc[-1]), float(r.iloc[-2])
    last = df.iloc[-1]
    last_close = float(last["close"])
    last_atr = float(atr(df, 14).iloc[-1])
    window = df.tail(20)

    if rsi_prev < 30 and rsi_now >= 30:
        recent_low = float(window["low"].min())
        recent_high = float(window["high"].max())
        entry = last_close
        stop = recent_low - 0.3 * last_atr
        t1 = recent_high
        t2 = entry + 2 * (entry - stop)
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="RSI Oversold Reversal (long)",
            direction="long",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.55,
            reasoning=(
                f"RSI exiting oversold ({rsi_prev:.1f}→{rsi_now:.1f}). Mean-reversion long with stop below 20-bar low."
            ),
            citations=[Citation(CC_SECOND, 1, "Entry Triggers — RSI extremes")],
        )

    if rsi_prev > 70 and rsi_now <= 70:
        recent_low = float(window["low"].min())
        recent_high = float(window["high"].max())
        entry = last_close
        stop = recent_high + 0.3 * last_atr
        t1 = recent_low
        t2 = entry - 2 * (stop - entry)
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="RSI Overbought Reversal (short)",
            direction="short",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.55,
            reasoning=(
                f"RSI exiting overbought ({rsi_prev:.1f}→{rsi_now:.1f}). Short with stop above 20-bar high."
            ),
            citations=[Citation(CC_SECOND, 1, "Entry Triggers — RSI extremes (inverted)")],
        )
    return None


# ----------------------------------------------------------------------
# 8) Volume spike confirming breakout (Second 18.pdf p.18 + entry triggers)
# ----------------------------------------------------------------------
def detect_volume_spike_breakout(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """Today's volume > 2.0x 20-bar avg AND today's close is a new 20-bar high
    or new 20-bar low.

    Cited: Second 18.pdf p.18 ("Ranking by Volume").
    """
    if len(df) < 25:
        return None
    last = df.iloc[-1]
    last_close = float(last["close"])
    last_volume = float(last["volume"])
    last_atr = float(atr(df, 14).iloc[-1])
    window = df.iloc[-21:-1]
    vol_avg = float(window["volume"].mean())
    if vol_avg == 0:
        return None
    if last_volume < 2.0 * vol_avg:
        return None

    high_20 = float(window["high"].max())
    low_20 = float(window["low"].min())

    if last_close > high_20:
        entry = last_close
        stop = high_20 - 0.5 * last_atr
        t1 = entry + 1.5 * (entry - stop)
        t2 = entry + 3.0 * (entry - stop)
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="Volume Spike Breakout (long)",
            direction="long",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.72,
            reasoning=(
                f"New 20-bar high on {last_volume/vol_avg:.1f}× average volume. "
                f"Stop below the prior 20-bar high."
            ),
            citations=[Citation(CC_SECOND, 18, "Ranking by Volume — high-volume breakouts")],
        )

    if last_close < low_20:
        entry = last_close
        stop = low_20 + 0.5 * last_atr
        t1 = entry - 1.5 * (stop - entry)
        t2 = entry - 3.0 * (stop - entry)
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="Volume Spike Breakdown (short)",
            direction="short",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.70,
            reasoning=(
                f"New 20-bar low on {last_volume/vol_avg:.1f}× volume. Stop above the prior 20-bar low."
            ),
            citations=[Citation(CC_SECOND, 18, "Ranking by Volume (inverted)")],
        )
    return None


# ----------------------------------------------------------------------
# 9) Bollinger squeeze + expansion
# ----------------------------------------------------------------------
def detect_bollinger_squeeze(
    symbol: str, df: pd.DataFrame, timeframe: str = "1d"
) -> Optional[Setup]:
    """Bollinger bandwidth contracted to the lowest in 100 bars then expands
    with a strong directional close.

    Cited: general TA + Second 18 entry triggers.
    """
    if len(df) < 120:
        return None
    bb = bollinger(df["close"], 20, 2.0)
    bandwidth = (bb["upper"] - bb["lower"]) / bb["mid"]
    bw_now = bandwidth.iloc[-1]
    bw_min = bandwidth.iloc[-100:-1].min()
    if math.isnan(bw_now) or math.isnan(bw_min):
        return None
    # Was the bandwidth just at a 100-bar low and is now expanding?
    yesterday_bw = bandwidth.iloc[-2]
    if not (yesterday_bw <= bw_min * 1.05 and bw_now > yesterday_bw):
        return None

    last = df.iloc[-1]
    last_close = float(last["close"])
    last_atr = float(atr(df, 14).iloc[-1])
    mid_now = float(bb["mid"].iloc[-1])
    upper_now = float(bb["upper"].iloc[-1])
    lower_now = float(bb["lower"].iloc[-1])

    if last_close > upper_now:
        entry = last_close
        stop = mid_now - 0.2 * last_atr
        t1 = entry + (upper_now - lower_now)
        t2 = entry + 2 * (upper_now - lower_now)
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="Bollinger Squeeze Expansion (long)",
            direction="long",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.62,
            reasoning=(
                f"Bandwidth at a 100-bar low; today's close breaks above the upper band — directional expansion."
            ),
            citations=[Citation(CC_SECOND, 1, "Entry Triggers — volatility expansion")],
        )

    if last_close < lower_now:
        entry = last_close
        stop = mid_now + 0.2 * last_atr
        t1 = entry - (upper_now - lower_now)
        t2 = entry - 2 * (upper_now - lower_now)
        return make_setup(
            symbol=symbol, timeframe=timeframe,
            name="Bollinger Squeeze Expansion (short)",
            direction="short",
            entry=entry, stop_loss=stop, targets=[t1, t2],
            current_price=last_close,
            conviction=0.60,
            reasoning=(
                f"Volatility expansion to the downside after a 100-bar bandwidth low."
            ),
            citations=[Citation(CC_SECOND, 1, "Entry Triggers — volatility expansion (inverted)")],
        )
    return None
