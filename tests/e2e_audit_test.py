"""E2E test for the "senior trader audit" round of improvements.

Verifies, hermetically:
  - auto_adjust=True on yfinance calls
  - MIN_RISK_REWARD raised to 2.0
  - volume_confirmed() filter present and works
  - nearest_swing_below/above helpers find the right pivots
  - smart_targets_long/short use real S/R
  - bar_pattern classifier returns the expected labels
  - MACRO_EVENTS_2026 + upcoming_macro_within() work
  - fetch_market_regime + regime_adjusts_conviction() haircut high-VIX
  - correlation_warning() flags concentration
  - backtest engine simulates a setup correctly
  - BACKTESTED_CONVICTION is populated and used
  - sizer / journal UI is rendered in HTML
  - bid/ask + avg_volume shown when available
  - regime strip + macro banner + correlation warning appear in HTML
"""

from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import warnings
warnings.filterwarnings("ignore")

import scan_setups as cc
import pandas as pd
import numpy as np

results: list[tuple[str, bool, str]] = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. Data quality fixes
# ---------------------------------------------------------------------------
print("\n[1] Data-quality fixes")
src = (REPO / "scan_setups.py").read_text()
check("auto_adjust=True everywhere",
      "auto_adjust=False" not in src and "auto_adjust=True" in src)
check("MIN_RISK_REWARD = 2.0",
      "MIN_RISK_REWARD = 2.0" in src)


# ---------------------------------------------------------------------------
# 2. Detector helpers
# ---------------------------------------------------------------------------
print("\n[2] Detector helpers (vol confirm, smart stops, smart targets, bar pattern)")

# Volume confirmed: today's vol = 1.0× avg should pass; 0.5× should fail
n = 30
df_vol_ok = pd.DataFrame({
    "open":   [100.0]*n, "high":   [101.0]*n, "low":    [99.0]*n,
    "close":  [100.5]*n,
    "volume": [1_000_000]*29 + [900_000],     # today is 0.9× avg → passes 0.8 threshold
})
df_vol_no = df_vol_ok.copy()
df_vol_no.loc[df_vol_no.index[-1], "volume"] = 200_000
check("volume_confirmed True at 0.9×", cc.volume_confirmed(df_vol_ok))
check("volume_confirmed False at 0.2×", not cc.volume_confirmed(df_vol_no))

# Smart stops + targets — build a df with clear swing pivots
prices = ([100.0, 98.0, 96.0, 94.0, 92.0, 90.0]   # decline
        + [92.0, 94.0, 96.0]                       # bounce (swing low ~90)
        + [98.0, 100.0, 102.0, 104.0, 106.0]
        + [104.0, 102.0, 100.0]                    # swing high ~106
        + [102.0, 104.0, 106.0, 108.0])
df_pivots = pd.DataFrame({
    "open":   prices, "high": [p + 0.5 for p in prices],
    "low":    [p - 0.5 for p in prices],
    "close":  prices, "volume": [1_000_000]*len(prices),
})

sl = cc.nearest_swing_below(df_pivots, price=108.0, lookback=30, n=2)
check("nearest_swing_below finds pivot below 108", sl is not None and sl < 108,
      f"got {sl}")
sh = cc.nearest_swing_above(df_pivots, price=89.0, lookback=30, n=2)
check("nearest_swing_above finds pivot above 89", sh is not None and sh > 89,
      f"got {sh}")

t_long = cc.smart_targets_long(df_pivots, entry=108.0, stop=104.0)
check("smart_targets_long returns 2 targets above entry",
      len(t_long) == 2 and t_long[0] > 108 and t_long[1] > t_long[0],
      f"got {t_long}")
t_short = cc.smart_targets_short(df_pivots, entry=89.0, stop=93.0)
check("smart_targets_short returns 2 targets below entry",
      len(t_short) == 2 and t_short[0] < 89 and t_short[1] < t_short[0],
      f"got {t_short}")

# Bar patterns
def _df(o,h,l,c,prev_o=None,prev_h=None,prev_l=None,prev_c=None):
    rows = []
    if prev_o is not None:
        rows.append({"open":prev_o,"high":prev_h,"low":prev_l,"close":prev_c,"volume":1000})
    rows.append({"open":o,"high":h,"low":l,"close":c,"volume":1000})
    return pd.DataFrame(rows)

# Hammer: long lower wick, small body near top (body must be > 10% of range
# to avoid getting classified as doji first).
# o=99, h=100.2, l=95, c=100 → body=1.0, upper_wick=0.2, lower_wick=4.0
check("bar_pattern detects hammer",
      cc.bar_pattern(_df(99, 100.2, 95, 100,    # current: body 1, lower wick 4
                          98, 102, 97.5, 101))  # prev big bull bar (not engulfed)
      == "hammer")
# Inside bar — body inside prev range, body large enough to not be doji
# o=99.7, h=100.5, l=99.5, c=100.3 inside prev o=99, h=101, l=99, c=100
check("bar_pattern detects inside",
      cc.bar_pattern(_df(99.7, 100.5, 99.5, 100.3, 99, 101, 99, 100)) == "inside")
# Doji: body ≈ 0 vs range
check("bar_pattern detects doji",
      cc.bar_pattern(_df(100, 102, 98, 100.05, 100, 101, 99, 100)) == "doji")


# ---------------------------------------------------------------------------
# 3. Macro calendar
# ---------------------------------------------------------------------------
print("\n[3] Macro event calendar")
check("MACRO_EVENTS_2026 contains FOMC", any("FOMC" in n for _, n in cc.MACRO_EVENTS_2026))
check("MACRO_EVENTS_2026 contains CPI",  any("CPI"  in n for _, n in cc.MACRO_EVENTS_2026))
check("MACRO_EVENTS_2026 contains NFP",  any("Non-Farm" in n for _, n in cc.MACRO_EVENTS_2026))
# upcoming_macro_within with a wide window should hit something — today might
# not be near an event so we use the function itself, not its output
ev = cc.upcoming_macro_within(days_ahead=400)
check("upcoming_macro_within(400) finds at least one 2026 event", ev is not None)


# ---------------------------------------------------------------------------
# 4. Market regime + correlation
# ---------------------------------------------------------------------------
print("\n[4] Market regime + correlation")
# regime_adjusts_conviction
check("regime extreme cuts conviction",  cc.regime_adjusts_conviction(0.70, {"vix_regime":"extreme"}) < 0.70)
check("regime normal unchanged",         abs(cc.regime_adjusts_conviction(0.70, {"vix_regime":"normal"}) - 0.70) < 1e-6)
check("regime low-vol bumps conviction", cc.regime_adjusts_conviction(0.70, {"vix_regime":"low-vol"}) > 0.70)
check("regime extreme floors at 0.10",   cc.regime_adjusts_conviction(0.05, {"vix_regime":"extreme"}) >= 0.10)

# correlation_warning
fake_setups = [
    cc.Setup(symbol="GOOGL", name="x", direction="long", entry=1, stop_loss=0.9,
             targets=[1.2,1.4], current_price=1, conviction=0.7, reasoning="", citation=""),
    cc.Setup(symbol="META",  name="x", direction="long", entry=1, stop_loss=0.9,
             targets=[1.2,1.4], current_price=1, conviction=0.7, reasoning="", citation=""),
    cc.Setup(symbol="DIS",   name="x", direction="long", entry=1, stop_loss=0.9,
             targets=[1.2,1.4], current_price=1, conviction=0.7, reasoning="", citation=""),
]
counts = cc.correlation_warning(fake_setups)
check("correlation_warning detects 3 in XLC", counts.get("XLC", 0) == 3)


# ---------------------------------------------------------------------------
# 5. Backtest engine
# ---------------------------------------------------------------------------
print("\n[5] Backtest simulator")
# Build a future_df that hits target1 before stop for a long
fut_win = pd.DataFrame({
    "open":   [100, 101, 102, 103, 105],
    "high":   [100.5, 101.5, 102.5, 105.5, 106],  # T1=105 hit on bar 4
    "low":    [99.5, 100.5, 101.5, 102.5, 104.5],
    "close":  [100.2, 101.2, 102.2, 105.0, 105.5],
    "volume": [1_000_000] * 5,
})
setup_long = cc.Setup(
    symbol="X", name="t", direction="long",
    entry=100.0, stop_loss=98.0, targets=[105.0, 110.0],
    current_price=100, conviction=0.7, reasoning="", citation="",
)
r_win, held = cc._simulate_setup(setup_long, fut_win, max_bars=10)
check("backtest simulator: target hit returns positive R",
      r_win > 0, f"got R={r_win:.2f}")
# Now a loser: stop hit on bar 2
fut_loss = pd.DataFrame({
    "open":   [100, 99,  97],
    "high":   [100.5, 99.5, 98],
    "low":    [99,    97,   97.5],
    "close":  [99.5, 97.5, 97.8],
    "volume": [1_000_000]*3,
})
r_loss, _ = cc._simulate_setup(setup_long, fut_loss, max_bars=10)
check("backtest simulator: stop hit returns -1.0R", r_loss == -1.0,
      f"got R={r_loss}")

# BACKTESTED_CONVICTION populated with priors
check("BACKTESTED_CONVICTION has EMA Pullback prior", "EMA Pullback" in cc.BACKTESTED_CONVICTION)


# ---------------------------------------------------------------------------
# 6. UI: Sizer + Journal + regime strip
# ---------------------------------------------------------------------------
print("\n[6] UI panels in HTML")
html = cc.render_html(
    setups=[], scanned=0, duration_s=0.0,
    market_regime={"vix_regime": "extreme", "vix_level": 38.5},
    macro_event=("2026-06-17", "FOMC rate decision"),
    sector_counts={"XLC": 3, "XLK": 4},
)
check("regime strip rendered",          'class="regime-strip"' in html)
check("VIX level shown",                'VIX 38.5' in html)
check("VIX regime classification shown", 'EXTREME' in html)
check("macro event banner shown",       'FOMC rate decision on 2026-06-17' in html)
check("correlation warning shown (XLC=3)", '3 setups in XLC' in html)
check("correlation warning shown (XLK=4)", '4 setups in XLK' in html)
check("Sizer bar present",              'id="acct-size"' in html and 'id="risk-pct"' in html)
check("Sizer JS function (onSizerChange)", 'function onSizerChange()' in html)
check("sizeTrade() JS function present",  'function sizeTrade(' in html)
check("Trade journal panel present",     'id="journal-panel"' in html)
check("Trade journal JS (takeTrade)",    'function takeTrade(' in html)
check("Trade journal JS (closeTrade)",   'function closeTrade(' in html)
check("Trade journal JS (exportJournal)", 'function exportJournal(' in html)
check("Trade journal renderer wired",    'function renderJournal()' in html)


# ---------------------------------------------------------------------------
# 7. Bid/ask in Key Levels panel when available
# ---------------------------------------------------------------------------
print("\n[7] Bid/ask + avg-volume display")
snap_with_quote = cc.Snapshot(
    symbol="AAPL", current_price=200.0,
    ema_55=190.0, ema_100=180.0, ema_200=170.0, rsi_14=58.0,
    support_levels=[180.0], resistance_levels=[210.0],
    bid=199.95, ask=200.05, spread_pct=0.05,
    avg_volume=50_000_000,
    context_flags=[],
)
panel = cc._render_key_levels_panel(snap_with_quote)
check("Key Levels panel shows Bid/Ask",      '$199.95' in panel and '$200.05' in panel)
check("Key Levels panel shows spread %",     '0.05%' in panel)
check("Key Levels panel shows Avg vol (20d)", 'Avg vol' in panel and '50.0M' in panel)

# Without bid/ask, panel still works (graceful)
snap_no_quote = cc.Snapshot(
    symbol="TSLA", current_price=200.0, ema_55=195.0, ema_100=180.0, ema_200=170.0,
)
panel2 = cc._render_key_levels_panel(snap_no_quote)
check("Key Levels panel works without bid/ask", 'Current' in panel2)


# ---------------------------------------------------------------------------
# 8. Per-setup-card actions (size / take / pass)
# ---------------------------------------------------------------------------
print("\n[8] Setup-card action buttons")
setup = cc.Setup(
    symbol="NVDA", name="EMA Pullback", direction="long",
    entry=500.0, stop_loss=490.0, targets=[520.0, 540.0],
    current_price=500.0, conviction=0.78, reasoning="x", citation="x",
    context_flags=[],
)
html_card = cc.render_html(setups=[setup], scanned=1, duration_s=0.1)
check("setup card has 'Size this' button",   '📐 Size this' in html_card)
check("setup card has 'Take' button",        'class="take-btn"' in html_card and '▶ Take' in html_card)
check("setup card has 'Pass' button",        '⏭ Pass' in html_card)


# ---------------------------------------------------------------------------
# 9. CI workflow + tests folder
# ---------------------------------------------------------------------------
print("\n[9] CI workflow + tests organization")
ci_path = REPO / ".github" / "workflows" / "test.yml"
check("CI workflow file exists at .github/workflows/test.yml", ci_path.exists())
if ci_path.exists():
    ci_text = ci_path.read_text()
    check("CI installs pandas + yfinance",   'yfinance' in ci_text and 'pandas' in ci_text)
    check("CI runs compile-check",           'import scan_setups' in ci_text)
    check("CI iterates tests/ folder",       'tests/' in ci_text)
tests_dir = REPO / "tests"
check("tests/ folder exists with at least 3 test files",
      tests_dir.exists() and len(list(tests_dir.glob("e2e_*.py"))) >= 3)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Audit:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
