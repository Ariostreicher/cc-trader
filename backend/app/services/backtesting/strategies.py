"""Built-in strategies extracted from Chart Champions cheatsheets.

Each strategy is a pure function ``df -> Series[int]`` returning a position
of -1, 0 or 1 for every bar.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import ema, rsi


def ema_55_100_200_long_only(df: pd.DataFrame) -> pd.Series:
    """First18.pdf p.67: long when EMA55 > EMA100 > EMA200 and price > EMA55."""
    e55 = ema(df["close"], 55)
    e100 = ema(df["close"], 100)
    e200 = ema(df["close"], 200)
    aligned = (e55 > e100) & (e100 > e200) & (df["close"] > e55)
    return aligned.astype(int)


def rsi_meanrev_long_only(df: pd.DataFrame, *, oversold: int = 30, exit_at: int = 50) -> pd.Series:
    """RSI mean reversion: enter long when RSI crosses below oversold, exit
    when RSI crosses back above exit_at. Defensive long-only baseline."""
    r = rsi(df["close"], 14)
    in_pos = False
    out = []
    for v in r:
        if pd.isna(v):
            out.append(0)
            continue
        if not in_pos and v < oversold:
            in_pos = True
        elif in_pos and v > exit_at:
            in_pos = False
        out.append(1 if in_pos else 0)
    return pd.Series(out, index=df.index)


STRATEGIES = {
    "ema_55_100_200_long_only": ema_55_100_200_long_only,
    "rsi_meanrev_long_only": rsi_meanrev_long_only,
}
