"""Alert evaluator.

Pulls indicator snapshots and decides whether each enabled alert should fire.
Designed to run on a tick (background worker) — pure logic, no I/O of its
own; the caller passes in the latest bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from ...models.alert import Alert, AlertTrigger
from ..indicators import ema, macd, rsi


@dataclass(slots=True)
class FireDecision:
    should_fire: bool
    reason: str | None = None
    payload: dict[str, Any] | None = None


def _cooldown_ok(alert: Alert, now: datetime | None = None) -> bool:
    if alert.last_triggered_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - alert.last_triggered_at) >= timedelta(seconds=alert.cooldown_seconds)


def evaluate(alert: Alert, df: pd.DataFrame) -> FireDecision:
    """``df`` is the latest OHLCV DataFrame; the most recent row is the
    current bar."""
    if not alert.is_enabled or df.empty:
        return FireDecision(False, "disabled or empty bars")
    if not _cooldown_ok(alert):
        return FireDecision(False, "cooldown")

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    p = alert.params or {}

    if alert.trigger == AlertTrigger.price_above:
        threshold = float(p.get("price", 0))
        if last["close"] >= threshold:
            return FireDecision(True, f"price >= {threshold}", {"price": float(last["close"])})

    elif alert.trigger == AlertTrigger.price_below:
        threshold = float(p.get("price", 0))
        if last["close"] <= threshold:
            return FireDecision(True, f"price <= {threshold}", {"price": float(last["close"])})

    elif alert.trigger == AlertTrigger.rsi_above:
        length = int(p.get("length", 14))
        threshold = float(p.get("level", 70))
        r = rsi(df["close"], length).iloc[-1]
        if pd.notna(r) and r >= threshold:
            return FireDecision(True, f"RSI({length})={r:.1f} >= {threshold}", {"rsi": float(r)})

    elif alert.trigger == AlertTrigger.rsi_below:
        length = int(p.get("length", 14))
        threshold = float(p.get("level", 30))
        r = rsi(df["close"], length).iloc[-1]
        if pd.notna(r) and r <= threshold:
            return FireDecision(True, f"RSI({length})={r:.1f} <= {threshold}", {"rsi": float(r)})

    elif alert.trigger in (AlertTrigger.macd_cross_up, AlertTrigger.macd_cross_down):
        m = macd(df["close"])
        if len(m) < 2:
            return FireDecision(False, "insufficient data")
        h_now, h_prev = m["hist"].iloc[-1], m["hist"].iloc[-2]
        if pd.notna(h_now) and pd.notna(h_prev):
            if alert.trigger == AlertTrigger.macd_cross_up and h_prev <= 0 < h_now:
                return FireDecision(True, "MACD crossed up", {"hist": float(h_now)})
            if alert.trigger == AlertTrigger.macd_cross_down and h_prev >= 0 > h_now:
                return FireDecision(True, "MACD crossed down", {"hist": float(h_now)})

    elif alert.trigger == AlertTrigger.volume_spike:
        mult = float(p.get("multiplier", 1.5))
        lookback = int(p.get("lookback", 20))
        avg = df["volume"].iloc[-(lookback + 1) : -1].mean()
        if avg and last["volume"] >= avg * mult:
            return FireDecision(
                True,
                f"volume {last['volume']:.0f} >= {mult}× avg ({avg:.0f})",
                {"volume": float(last["volume"]), "avg": float(avg)},
            )

    elif alert.trigger in (AlertTrigger.ema_cross_up, AlertTrigger.ema_cross_down):
        fast = int(p.get("fast", 55))
        slow = int(p.get("slow", 200))
        e_fast = ema(df["close"], fast)
        e_slow = ema(df["close"], slow)
        if len(e_fast) < 2 or len(e_slow) < 2:
            return FireDecision(False, "insufficient data")
        f_now, f_prev = e_fast.iloc[-1], e_fast.iloc[-2]
        s_now, s_prev = e_slow.iloc[-1], e_slow.iloc[-2]
        if alert.trigger == AlertTrigger.ema_cross_up and f_prev <= s_prev and f_now > s_now:
            return FireDecision(True, f"EMA{fast} crossed above EMA{slow}")
        if alert.trigger == AlertTrigger.ema_cross_down and f_prev >= s_prev and f_now < s_now:
            return FireDecision(True, f"EMA{fast} crossed below EMA{slow}")

    elif alert.trigger == AlertTrigger.sr_break_above:
        level = float(p.get("level", 0))
        if pd.notna(last["close"]) and prev is not None and prev["close"] < level <= last["close"]:
            return FireDecision(True, f"close broke above {level}")

    elif alert.trigger == AlertTrigger.sr_break_below:
        level = float(p.get("level", 0))
        if pd.notna(last["close"]) and prev is not None and prev["close"] > level >= last["close"]:
            return FireDecision(True, f"close broke below {level}")

    elif alert.trigger == AlertTrigger.sr_bounce:
        level = float(p.get("level", 0))
        tol_pct = float(p.get("tolerance_pct", 0.5)) / 100
        if abs(last["low"] - level) / max(level, 1e-9) <= tol_pct and last["close"] > level:
            return FireDecision(True, f"bounced off {level}")

    elif alert.trigger == AlertTrigger.ai_confidence_above:
        # The AI signal layer writes the latest confidence into params['_signal']
        # before evaluation; this lets us reuse the same evaluator path.
        sig_conf = (p.get("_signal") or {}).get("confidence")
        threshold = float(p.get("threshold", 0.7))
        if sig_conf is not None and sig_conf >= threshold:
            return FireDecision(True, f"AI confidence {sig_conf} >= {threshold}", {"signal": p["_signal"]})

    return FireDecision(False, "no trigger")
