"""E2E for Wave 5 — multi-timeframe levels matching the CC TradingView style.

Verifies:
  • resample_period(W/M) aggregates daily bars correctly
  • compute_multi_timeframe_pivots returns daily/weekly/monthly pivots
  • compute_multi_timeframe_volume_profile returns weekly+monthly POCs
  • recent_period_extremes returns last N highs/lows
  • find_naked_pocs detects unretraced POCs from prior periods
  • Snapshot dataclass carries all new fields
  • Chart price-lines emit "DAILY"/"WEEKLY"/"MONTHLY" labeled lines + "nPOC"
"""

from __future__ import annotations
import sys, json as _json, re
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
# Build a synthetic 200-day daily df with a clear trend so weekly/monthly
# aggregation produces sensible bars and volume profiles.
# ---------------------------------------------------------------------------
n = 220
np.random.seed(11)
prices = np.cumsum(np.random.normal(0.05, 1.0, n)) + 100
df = pd.DataFrame({
    "open":   prices + np.random.normal(0, 0.2, n),
    "high":   prices + 1.5,
    "low":    prices - 1.5,
    "close":  prices,
    "volume": np.random.randint(500_000, 5_000_000, n),
}, index=pd.date_range("2025-09-01", periods=n, freq="B"))


# ---------------------------------------------------------------------------
# 1. resample_period
# ---------------------------------------------------------------------------
print("\n[1] Resample daily → weekly + monthly")
weekly = cc.resample_period(df, "W")
monthly = cc.resample_period(df, "ME")
check("weekly resample returns DataFrame", isinstance(weekly, pd.DataFrame))
check("weekly bars are fewer than daily", len(weekly) < len(df))
check("weekly has OHLCV columns",
      set(weekly.columns) >= {"open","high","low","close","volume"})
check("monthly resample returns DataFrame", isinstance(monthly, pd.DataFrame))
check("monthly has at least 5 bars", len(monthly) >= 5)
# Sanity: weekly high should equal max of the contributing days
first_week_high = weekly["high"].iloc[0]
first_week_idx = weekly.index[0]
days_in_first_week = df[df.index <= first_week_idx].tail(5)
check("weekly aggregation: high = max of contributing days",
      abs(first_week_high - days_in_first_week["high"].max()) < 0.01)


# ---------------------------------------------------------------------------
# 2. Multi-timeframe pivots
# ---------------------------------------------------------------------------
print("\n[2] Multi-timeframe pivots (daily / weekly / monthly)")
mtf = cc.compute_multi_timeframe_pivots(df)
check("returns daily pivots",   "daily" in mtf and "pp" in mtf.get("daily", {}))
check("returns weekly pivots",  "weekly" in mtf and "pp" in mtf.get("weekly", {}))
check("returns monthly pivots", "monthly" in mtf and "pp" in mtf.get("monthly", {}))
# Sanity: weekly PP should be in a similar range as monthly PP (same instrument)
if "weekly" in mtf and "monthly" in mtf:
    diff_pct = abs(mtf["weekly"]["pp"] - mtf["monthly"]["pp"]) / max(mtf["monthly"]["pp"], 1) * 100
    check("weekly vs monthly PP within 50% of each other", diff_pct < 50,
          f"weekly={mtf['weekly']['pp']:.2f}, monthly={mtf['monthly']['pp']:.2f}")


# ---------------------------------------------------------------------------
# 3. Multi-timeframe volume profile
# ---------------------------------------------------------------------------
print("\n[3] Multi-timeframe volume profile")
mtf_vp = cc.compute_multi_timeframe_volume_profile(df)
check("returns weekly VP",     "weekly" in mtf_vp and "poc" in mtf_vp.get("weekly", {}))
check("returns monthly VP",    "monthly" in mtf_vp and "poc" in mtf_vp.get("monthly", {}))
check("returns quarterly VP",  "quarterly" in mtf_vp and "poc" in mtf_vp.get("quarterly", {}))
# VP must satisfy VAL ≤ POC ≤ VAH
for tf in ["weekly", "monthly", "quarterly"]:
    if tf in mtf_vp and "val" in mtf_vp[tf]:
        vp = mtf_vp[tf]
        check(f"{tf} VP: VAL ≤ POC ≤ VAH",
              vp["val"] <= vp["poc"] <= vp["vah"],
              f"val={vp['val']:.2f} poc={vp['poc']:.2f} vah={vp['vah']:.2f}")


# ---------------------------------------------------------------------------
# 4. Recent period extremes (last N weekly/monthly H+L)
# ---------------------------------------------------------------------------
print("\n[4] recent_period_extremes(count=3)")
recent_w = cc.recent_period_extremes(weekly, count=3).get("periods", [])
recent_m = cc.recent_period_extremes(monthly, count=3).get("periods", [])
check("recent weekly returns up to 3 periods", 1 <= len(recent_w) <= 3,
      f"got {len(recent_w)}")
check("recent monthly returns up to 3 periods", 1 <= len(recent_m) <= 3,
      f"got {len(recent_m)}")
if recent_w:
    check("each weekly period has high + low + period_end",
          all("high" in p and "low" in p and "period_end" in p for p in recent_w))


# ---------------------------------------------------------------------------
# 5. Naked POC detection
# ---------------------------------------------------------------------------
print("\n[5] find_naked_pocs — POCs not yet retested")
npocs = cc.find_naked_pocs(df, periods=8)
check("returns a list", isinstance(npocs, list))
# nPOCs should each have poc + naked + distance_pct
for n_p in npocs:
    check("each nPOC has poc + naked + distance_pct",
          "poc" in n_p and "naked" in n_p and "distance_pct" in n_p)
    if "naked" in n_p:
        check("naked flag is True (these are filtered to naked only)",
              n_p["naked"] is True)
        break


# ---------------------------------------------------------------------------
# 6. Snapshot dataclass carries the new fields
# ---------------------------------------------------------------------------
print("\n[6] Snapshot has all Wave-5 fields")
snap = cc.Snapshot(
    symbol="TEST", current_price=100.0,
    pivots=mtf.get("daily"),
    pivots_weekly=mtf.get("weekly"),
    pivots_monthly=mtf.get("monthly"),
    recent_weekly=recent_w,
    recent_monthly=recent_m,
    vp_weekly=mtf_vp.get("weekly"),
    vp_monthly=mtf_vp.get("monthly"),
    vp_quarterly=mtf_vp.get("quarterly"),
    naked_pocs=npocs,
)
check("snap.pivots_weekly present",   snap.pivots_weekly is not None)
check("snap.pivots_monthly present",  snap.pivots_monthly is not None)
check("snap.recent_weekly is a list", isinstance(snap.recent_weekly, list))
check("snap.recent_monthly is a list", isinstance(snap.recent_monthly, list))
check("snap.vp_weekly has poc",       snap.vp_weekly and "poc" in snap.vp_weekly)
check("snap.vp_monthly has poc",      snap.vp_monthly and "poc" in snap.vp_monthly)
check("snap.naked_pocs is a list",    isinstance(snap.naked_pocs, list))


# ---------------------------------------------------------------------------
# 7. Chart price-lines render with TIMEFRAME-tagged labels
# ---------------------------------------------------------------------------
print("\n[7] Chart price-lines emit TIMEFRAME-tagged labels")
chart_data = {"TEST": {
    "candles":[{"time":"2025-01-01","open":100,"high":101,"low":99,"close":100}],
    "volume":[{"time":"2025-01-01","value":1_000_000,"color":"#22c55e55"}],
    "ema_55":[], "ema_100":[], "ema_200":[],
}}
body = cc._snap_chart_body(snap, 0, chart_data)
m = re.search(r"data-lines='([^']+)'", body)
assert m, "data-lines attribute not found"
lines = _json.loads(m.group(1))
titles = [l["title"] for l in lines]

check("at least one DAILY-tagged line", any("DAILY " in t for t in titles))
check("at least one WEEKLY-tagged line", any("WEEKLY " in t for t in titles))
check("at least one MONTHLY-tagged line", any("MONTHLY " in t for t in titles))
check("WEEKLY POC line present",      any("WEEKLY POC" in t for t in titles))
check("MONTHLY POC line present",     any("MONTHLY POC" in t for t in titles))
check("WEEKLY VAH/VAL present",
      any("WEEKLY VAH" in t for t in titles) and any("WEEKLY VAL" in t for t in titles))
check("MONTHLY VAH/VAL present",
      any("MONTHLY VAH" in t for t in titles) and any("MONTHLY VAL" in t for t in titles))
check("WEEKLY high lines (last 3 weeks) present",
      sum(1 for t in titles if "WEEKLY high" in t) >= 1)
check("WEEKLY low lines (last 3 weeks) present",
      sum(1 for t in titles if "WEEKLY low" in t) >= 1)
check("MONTHLY high lines present",
      sum(1 for t in titles if "MONTHLY high" in t) >= 1)
check("MONTHLY low lines present",
      sum(1 for t in titles if "MONTHLY low" in t) >= 1)

# nPOC labels (naked POCs)
if npocs:
    check("nPOC lines present",  any("nPOC " in t for t in titles))


# ---------------------------------------------------------------------------
# 8. Setup-card chart also gets the multi-TF overlay
# ---------------------------------------------------------------------------
print("\n[8] Setup-card chart includes multi-TF overlay")
setup = cc.Setup(
    symbol="TEST", name="Test", direction="long",
    entry=100.0, stop_loss=98.0, targets=[104.0, 108.0],
    current_price=100.0, conviction=0.7, reasoning="x", citation="x",
    context_flags=[],
)
html_setup = cc.render_html(
    setups=[setup], scanned=1, duration_s=0.1,
    levels_by_symbol={"TEST": snap},
    chart_data_by_symbol=chart_data,
)
m2 = re.search(r'id="lwc_0" data-symbol="TEST" data-lines=\'([^\']+)\'', html_setup)
check("setup chart has data-lines",  m2 is not None)
if m2:
    s_lines = _json.loads(m2.group(1))
    s_titles = [l["title"] for l in s_lines]
    check("setup chart includes DAILY/WEEKLY/MONTHLY labels",
          any("DAILY " in t for t in s_titles)
          and any("WEEKLY " in t for t in s_titles)
          and any("MONTHLY " in t for t in s_titles))
    check("setup chart includes WEEKLY POC",
          any("WEEKLY POC" in t for t in s_titles))
    check("setup chart still shows Entry + Stop + targets",
          any("Entry" in t for t in s_titles)
          and any("Stop"  in t for t in s_titles)
          and any("T1"    in t for t in s_titles))


# ---------------------------------------------------------------------------
# 9. Graceful handling of insufficient data
# ---------------------------------------------------------------------------
print("\n[9] Graceful handling of empty/tiny data")
empty = pd.DataFrame()
check("resample_period(empty) returns empty df",
      cc.resample_period(empty, "W").empty)
check("compute_multi_timeframe_pivots(empty) returns empty dict",
      cc.compute_multi_timeframe_pivots(empty) == {})
check("find_naked_pocs(empty) returns []",
      cc.find_naked_pocs(empty, periods=4) == [])


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 5:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
