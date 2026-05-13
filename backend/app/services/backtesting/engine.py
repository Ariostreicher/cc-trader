"""Minimal vectorized backtester.

Strategies are functions that take an OHLCV DataFrame and return a Series of
position sizes (-1, 0, 1) aligned on the DataFrame index. The engine then
simulates trades, compounding the equity curve.

This is the production-ready core for Phase 2; specific strategies (EMA, ORB,
etc.) are added under :mod:`app.services.backtesting.strategies`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

StrategyFn = Callable[[pd.DataFrame], pd.Series]


@dataclass(slots=True)
class BacktestResult:
    equity_curve: pd.Series
    trades: list[dict]
    total_return: float
    win_rate: float | None
    sharpe: float | None
    max_drawdown: float
    n_trades: int


def _sharpe(returns: pd.Series, periods_per_year: int = 252) -> float | None:
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return None
    return float(np.sqrt(periods_per_year) * r.mean() / r.std())


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def run_backtest(
    df: pd.DataFrame,
    strategy: StrategyFn,
    *,
    initial_equity: float = 100_000.0,
    fee_bps: float = 5.0,
) -> BacktestResult:
    if df.empty:
        return BacktestResult(
            equity_curve=pd.Series(dtype=float),
            trades=[],
            total_return=0.0,
            win_rate=None,
            sharpe=None,
            max_drawdown=0.0,
            n_trades=0,
        )

    positions = strategy(df).reindex(df.index).ffill().fillna(0).clip(-1, 1)
    price = df["close"]
    returns = price.pct_change().fillna(0)

    # Trading P&L = previous position × today's return
    strat_returns = positions.shift(1).fillna(0) * returns

    # Fee on position change.
    position_changes = positions.diff().abs().fillna(0)
    fees = position_changes * (fee_bps / 10_000)
    net_returns = strat_returns - fees

    equity = (1 + net_returns).cumprod() * initial_equity

    # Trade ledger.
    trades: list[dict] = []
    entry_idx = None
    entry_price = None
    side = 0
    for i, (ts, pos) in enumerate(positions.items()):
        if entry_idx is None and pos != 0:
            entry_idx, entry_price, side = ts, float(price.iloc[i]), int(pos)
        elif entry_idx is not None and (pos == 0 or int(pos) != side):
            exit_price = float(price.iloc[i])
            pnl = (exit_price - entry_price) * side
            trades.append(
                {
                    "entry_ts": entry_idx.isoformat(),
                    "exit_ts": ts.isoformat(),
                    "side": "long" if side > 0 else "short",
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_per_share": pnl,
                }
            )
            entry_idx, entry_price, side = (None, None, 0) if pos == 0 else (ts, exit_price, int(pos))

    wins = sum(1 for t in trades if t["pnl_per_share"] > 0)
    win_rate = (wins / len(trades)) if trades else None

    return BacktestResult(
        equity_curve=equity,
        trades=trades,
        total_return=float(equity.iloc[-1] / initial_equity - 1) if not equity.empty else 0.0,
        win_rate=win_rate,
        sharpe=_sharpe(net_returns),
        max_drawdown=_max_drawdown(equity) if not equity.empty else 0.0,
        n_trades=len(trades),
    )
