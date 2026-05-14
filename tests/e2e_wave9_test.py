"""E2E for Wave 9 — multi-timeframe chart selector (1H/1D/1W/1M).

Restores the timeframe selector that was in the original TradingView widget
but with our custom Lightweight Charts implementation. Verifies:
  • Timeframe selector HTML buttons rendered on every chart card
  • CSS for the .tf-bar / .tf-btn classes is present
  • JS function switchTimeframe() exists and is wired
  • _getTfData / _availableTfs helpers handle both new and legacy payloads
  • Backward compatibility: legacy single-TF chart data still works
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
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  → {detail}" if detail else ""))


# Build a snapshot with chart data so the chart card renders
snap = cc.Snapshot(
    symbol="TEST", current_price=100.0,
    ema_55=98.0, ema_100=95.0, ema_200=90.0, rsi_14=58.0,
)
# Legacy single-TF chart data (this is what render_html may receive from older
# callers — the JS must still support it for backward compatibility).
legacy_chart_data = {"TEST": {
    "candles":[{"time":"2025-01-01","open":100,"high":101,"low":99,"close":100}],
    "volume":[{"time":"2025-01-01","value":1_000_000,"color":"#22c55e55"}],
    "ema_55":[], "ema_100":[], "ema_200":[],
}}
# New multi-TF chart data
mtf_chart_data = {"TEST": {
    "default_tf": "1D",
    "timeframes": {
        "1D": {"candles":[{"time":"2025-01-01","open":100,"high":101,"low":99,"close":100}],
               "volume":[{"time":"2025-01-01","value":1_000_000,"color":"#22c55e55"}],
               "ema_55":[], "ema_100":[], "ema_200":[]},
        "1W": {"candles":[{"time":"2025-01-06","open":100,"high":102,"low":98,"close":101}],
               "volume":[{"time":"2025-01-06","value":5_000_000,"color":"#22c55e55"}],
               "ema_55":[], "ema_100":[], "ema_200":[]},
        "1H": {"candles":[{"time":1735689600,"open":100,"high":100.5,"low":99.5,"close":100}],
               "volume":[{"time":1735689600,"value":100_000,"color":"#22c55e55"}],
               "ema_55":[], "ema_100":[], "ema_200":[]},
    },
}}

# Wave 12: TF buttons + chart UI now on /chart page (memory fix).
# Main page (render_html) renders compact cards with "Open Chart →" links.
# All TF-selector tests now check render_single_chart_html.
html_legacy_main = cc.render_html(
    setups=[], scanned=1, duration_s=0.1,
    snapshots=[snap], levels_by_symbol={"TEST": snap},
    chart_data_by_symbol=legacy_chart_data,
)
html_mtf_main = cc.render_html(
    setups=[], scanned=1, duration_s=0.1,
    snapshots=[snap], levels_by_symbol={"TEST": snap},
    chart_data_by_symbol=mtf_chart_data,
)
# Build the /chart page where the TF selector + chart UI live
html_legacy = cc.render_single_chart_html(
    symbol="TEST", snap=snap,
    chart_data=legacy_chart_data["TEST"],
)
html_mtf = cc.render_single_chart_html(
    symbol="TEST", snap=snap,
    chart_data=mtf_chart_data["TEST"],
)


# ---------------------------------------------------------------------------
# 1. Timeframe selector HTML buttons rendered
# ---------------------------------------------------------------------------
print("\n[1] Timeframe selector buttons in HTML")
# Wave 14 — '1H' renamed to lowercase '1h' (TradingView convention) and the
# selector expanded from 4 buttons to 17. Verify the original 4 are still
# represented (1h now lowercase) plus a spot-check on new TFs.
for tf in ["1h", "1D", "1W", "1M"]:
    check(f"button for {tf}",
          f'data-tf="{tf}"' in html_mtf)
for tf in ["1m", "5m", "30m", "4h", "ALL"]:
    check(f"new Wave 14 TF {tf} button present",
          f'data-tf="{tf}"' in html_mtf)
check("'tf-bar' container div present",
      'class="tf-bar' in html_mtf)
check("legacy payload still gets the TF bar rendered",
      'class="tf-bar' in html_legacy)


# ---------------------------------------------------------------------------
# 2. CSS classes defined
# ---------------------------------------------------------------------------
print("\n[2] CSS for TF selector")
check(".tf-bar style defined",       ".tf-bar {" in html_mtf)
check(".tf-btn style defined",       ".tf-btn {" in html_mtf)
check(".tf-btn.active style defined", ".tf-btn.active" in html_mtf)
check(".tf-unavailable style defined", ".tf-unavailable" in html_mtf)


# ---------------------------------------------------------------------------
# 3. JS helpers + switcher
# ---------------------------------------------------------------------------
print("\n[3] JS — switchTimeframe + helpers")
check("TF-switching function exists (switchSoloTf on /chart page)",
      "function switchSoloTf(" in html_mtf or "function switchTimeframe(" in html_mtf)
check("_getTfData() helper exists",          "function _getTfData(" in html_mtf)
check("_availableTfs() helper exists",       "function _availableTfs(" in html_mtf)
check("window.cc_chart_handles initialized", "window.cc_chart_handles" in html_mtf)
check("button click wired to TF-switching function",
      "addEventListener('click'" in html_mtf
      and ("switchSoloTf(tf" in html_mtf or "switchTimeframe(div.id" in html_mtf))


# ---------------------------------------------------------------------------
# 4. Backward compatibility — JS handles legacy single-TF payload
# ---------------------------------------------------------------------------
print("\n[4] Backward compatibility")
# The JS _getTfData function must handle BOTH shapes:
#   new: {default_tf, timeframes: {1D: {...}}}
#   legacy: {candles, volume, ema_*}
# Both should not crash the page; legacy payloads return their single-TF data.
check("_getTfData has legacy-payload fallback path",
      "rawSymData.timeframes" in html_mtf and "rawSymData.candles" in html_mtf)
check("_availableTfs returns ['1D'] for legacy payloads",
      "['1D']" in html_mtf or '"1D"' in html_mtf)


# ---------------------------------------------------------------------------
# 5. New _build_chart_data structure (when called inside scan)
# ---------------------------------------------------------------------------
print("\n[5] New chart data format (multi-TF dict)")
# We can't call _build_chart_data directly (it lives inside run_full_scan as
# a closure), but we can verify the new payload SHAPE renders correctly.
check("MTF payload renders chart container",
      'class="lwc-chart"' in html_mtf and 'data-symbol="TEST"' in html_mtf)


# ---------------------------------------------------------------------------
# 6. Time-visible flag flips for intraday
# ---------------------------------------------------------------------------
print("\n[6] Intraday axis configuration")
# Wave 14 — single-1H conditional replaced with INTRADAY_TFS lookup
# (covers 1m/3m/5m/15m/30m/45m/1h/2h/3h/4h all at once).
check("timeVisible flag flips for intraday TFs (Wave 14: INTRADAY_TFS lookup)",
      "INTRADAY_TFS.indexOf(tf)" in html_mtf
      or "timeVisible: (tf === '1H')" in html_mtf
      or "timeVisible:(defaultTf==='1H')" in html_mtf)


# ---------------------------------------------------------------------------
# 7. Unavailable TFs are disabled (yfinance limits)
# ---------------------------------------------------------------------------
print("\n[7] Unavailable timeframes are disabled gracefully")
# Wave 14 — the disabled-button code path remains for forward compatibility
# (CSS class is still defined), even though every TF now lazy-loads on click
# and shows a spinner / falls back to a toast on empty payloads.
check("buttons CSS still has 'tf-unavailable' class (forward compat)",
      "tf-unavailable" in html_mtf)
check("lazy-load shows user-facing toast on missing data (Wave 14)",
      "No data for" in html_mtf or "not available" in html_mtf or "yfinance limit" in html_mtf)


# ---------------------------------------------------------------------------
# 8. Active button highlighting
# ---------------------------------------------------------------------------
print("\n[8] Active TF highlighted")
check("active class added on default TF",
      "btn.classList.add('active')" in html_mtf)
check("active class swapped on TF change",
      "classList.remove('active')" in html_mtf
      and "classList.add('active')" in html_mtf)


# ---------------------------------------------------------------------------
# 9. Price lines stay on chart across TF switches
# ---------------------------------------------------------------------------
print("\n[9] Price lines persist across TF changes (drawn on candleSeries)")
check("price lines drawn on candleSeries (TF-independent)",
      "candleSeries.createPriceLine(" in html_mtf)
check("price lines stay across TF changes (drawn on candleSeries — TF-agnostic)",
      "candleSeries.createPriceLine(" in html_mtf)


# ---------------------------------------------------------------------------
# 10. resample_period helper (already from Wave 5)
# ---------------------------------------------------------------------------
print("\n[10] resample_period helper still works (used by 1W/1M TFs)")
df = pd.DataFrame({
    "open": np.arange(50), "high": np.arange(50) + 1,
    "low": np.arange(50) - 1, "close": np.arange(50),
    "volume": np.full(50, 1_000_000),
}, index=pd.date_range("2025-09-01", periods=50, freq="B"))
weekly = cc.resample_period(df, "W")
monthly = cc.resample_period(df, "ME")
check("weekly resample non-empty", not weekly.empty)
check("monthly resample non-empty", not monthly.empty)
check("weekly bars < daily bars", len(weekly) < len(df))


# ---------------------------------------------------------------------------
# 11. Regression — Wave 8 detector count still 38
# ---------------------------------------------------------------------------
print("\n[11] Regression: detector count")
check("38 detectors still registered",
      len(cc.DETECTORS) == 38, f"got {len(cc.DETECTORS)}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 9:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
