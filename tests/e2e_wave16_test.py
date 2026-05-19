"""E2E for Wave 16 — 12-style chart selector (TradingView parity on LWC).

Verifies:
  • Dropdown selector renders all 12 styles
  • Each style has its corresponding LWC builder branch in JS
  • Heikin Ashi computation helper present
  • Style choice persisted to localStorage (cc_chart_style)
  • initChart respects persisted style on load
  • _applyTfData rebuilds main series + re-applies price lines on every
    TF / style change (so EMAs, volume, and price-lines stay in place)
  • setChartStyle() switches series without re-fetching data
  • Regression — Wave 14 + 15 features intact
"""

from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import warnings
warnings.filterwarnings("ignore")

import scan_setups as cc

results: list[tuple[str, bool, str]] = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  → {detail}" if detail else ""))


snap = cc.Snapshot(
    symbol="AAPL", current_price=200.0,
    ema_55=195.0, ema_100=185.0, ema_200=170.0, rsi_14=58.0,
    support_levels=[195.0], resistance_levels=[210.0],
    context_flags=[],
)
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="AAPL", snap=snap, chart_data=chart_data)


# ---------------------------------------------------------------------------
# 1. Dropdown markup — 12 options present
# ---------------------------------------------------------------------------
print("\n[1] Chart-style dropdown markup")
check("<select> chart-style-select rendered",
      'id="chart-style-select"' in html_chart)
check("CSS .chart-style-select rule defined",
      '.chart-style-select {' in html_chart)
check("onchange wired to setChartStyle",
      'onchange="setChartStyle(this.value)"' in html_chart)
for val in ["candles","hollow_candles","volume_candles","line","line_markers",
            "step_line","area","hlc_area","baseline","columns","high_low",
            "heikin_ashi"]:
    check(f"option value=\"{val}\" present",
          f'value="{val}"' in html_chart)


# ---------------------------------------------------------------------------
# 2. JS builder branches — every style has its construction code
# ---------------------------------------------------------------------------
print("\n[2] JS branches for every style")
check("_buildMainSeries() function defined",
      "function _buildMainSeries(" in html_chart)
check("getChartStyle / saveChartStyle defined",
      "function getChartStyle()" in html_chart and "function saveChartStyle(" in html_chart)
check("setChartStyle() function defined",
      "function setChartStyle(" in html_chart)
check("hollow_candles uses transparent upColor",
      "case 'hollow_candles':" in html_chart
      and "rgba(0,0,0,0)" in html_chart)
check("volume_candles maps volume → opacity",
      "case 'volume_candles':" in html_chart and "/ maxV" in html_chart)
check("line uses addLineSeries",
      "case 'line':" in html_chart and "addLineSeries" in html_chart)
check("line_markers uses setMarkers",
      "case 'line_markers':" in html_chart and "setMarkers(" in html_chart)
check("step_line uses LineType.WithSteps",
      "case 'step_line':" in html_chart and "WithSteps" in html_chart)
check("area uses addAreaSeries",
      "case 'area':" in html_chart and "addAreaSeries" in html_chart)
check("hlc_area uses 3 series (high+low+area)",
      "case 'hlc_area':" in html_chart
      and "addLineSeries" in html_chart.split("case 'hlc_area':")[1].split("case ")[0])
check("baseline uses addBaselineSeries",
      "case 'baseline':" in html_chart and "addBaselineSeries" in html_chart)
check("columns uses addHistogramSeries",
      "case 'columns':" in html_chart and "addHistogramSeries" in html_chart)
check("high_low uses addBarSeries",
      "case 'high_low':" in html_chart and "addBarSeries" in html_chart)
check("heikin_ashi calls _heikinAshi() helper",
      "case 'heikin_ashi':" in html_chart and "_heikinAshi(" in html_chart)
check("candles is the default branch",
      "default:" in html_chart and "addCandlestickSeries" in html_chart)


# ---------------------------------------------------------------------------
# 3. Heikin Ashi helper — formula correctness reflected in JS
# ---------------------------------------------------------------------------
print("\n[3] Heikin Ashi helper")
check("_heikinAshi function defined",       "function _heikinAshi(" in html_chart)
check("HA Close = (O+H+L+C)/4 formula present",
      "c.open + c.high + c.low + c.close" in html_chart)
check("HA Open recursion (prevOpen + prevClose) / 2",
      "(prevOpen + prevClose) / 2" in html_chart)
check("HA High = max(H, HA_Open, HA_Close)",
      "Math.max(c.high, haOpen, haClose)" in html_chart)
check("HA Low = min(L, HA_Open, HA_Close)",
      "Math.min(c.low,  haOpen, haClose)" in html_chart
      or "Math.min(c.low, haOpen, haClose)" in html_chart)


# ---------------------------------------------------------------------------
# 4. Persistence — localStorage key + sync to selector
# ---------------------------------------------------------------------------
print("\n[4] Style persistence")
check("localStorage key is 'cc_chart_style'",
      "'cc_chart_style'" in html_chart)
check("initChart applies persisted style on load",
      "var initialStyle = getChartStyle()" in html_chart)
check("initChart pre-fills the <select> with current value",
      "styleSel.value = initialStyle" in html_chart)


# ---------------------------------------------------------------------------
# 5. _applyTfData rebuilds main series + re-applies price lines on TF change
# ---------------------------------------------------------------------------
print("\n[5] Series + price-line preservation across TF / style swaps")
check("_reapplyPriceLines() helper defined",
      "function _reapplyPriceLines(" in html_chart)
check("_applyTfData removes old main series before rebuild",
      "h.chart.removeSeries(h.candleSeries)" in html_chart)
check("_applyTfData re-applies price lines after rebuild",
      "_reapplyPriceLines(h.candleSeries" in html_chart)
check("chart handle stashes _priceLines for replay",
      "_priceLines: lines" in html_chart)
check("chart handle stashes _lastTfData (so style swap doesn't refetch)",
      "_lastTfData" in html_chart)
check("setChartStyle reuses _lastTfData (no network call)",
      "data = h._lastTfData" in html_chart)
check("user annotations re-applied after style swap",
      "applyAnnotations(" in html_chart)


# ---------------------------------------------------------------------------
# 6. Regression — Wave 14 + 15 still in place
# ---------------------------------------------------------------------------
print("\n[6] Regression — earlier waves intact")
check("17 TF buttons still in chart page",
      all(f'data-tf="{t}"' in html_chart for t in
          ["1m","3m","5m","15m","30m","45m","1h","2h","3h","4h",
           "1D","1W","1M","3M","6M","12M","ALL"]))
check("All-Time Analysis button still rendered",
      'id="all-time-btn"' in html_chart)
check("DEFAULT_VISIBLE_BARS map still present (Wave 14 hotfix)",
      "DEFAULT_VISIBLE_BARS" in html_chart)
check("Hover tooltip subscribeCrosshairMove still hooked up",
      "subscribeCrosshairMove" in html_chart)
check("Annotation tools still wired",
      "addAnnotation" in html_chart and "clearAnnotations" in html_chart)
check("View toggle CC View / TradingView still present",
      "📊 CC View" in html_chart and "📈 TradingView" in html_chart)

html_main = cc.render_html(setups=[], scanned=0, duration_s=0.0)
check("Wave 15 watchlist sync still in main page",
      "function syncWatchlistToBackend()" in html_main)
check("Wave 15 /api/scan-now trigger still wired",
      "/api/scan-now?symbol=" in html_main)
check("38 detectors still registered",          len(cc.DETECTORS) == 38)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 16:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
