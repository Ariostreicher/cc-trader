"""E2E for Wave 21 — fix TF zoom timing bug (1m showing 'daily candles').

Root cause: After _applyTfData called setData() with 1m candles (~2700
bars over 7 days), it immediately called setVisibleLogicalRange() to zoom
to the last 60 bars. But LWC hadn't fully ingested the data yet, so the
zoom call silently failed and fitContent() was used instead — displaying
all 2700 bars at once, each ~1px wide, which LOOKED like daily candles
because the x-axis auto-formatted to dates.

Fix: defer the zoom call by 2 animation frames so LWC has time to paint
the new series data before we ask the time-scale to focus on a subset.

Also:
  • /chart-tf cache: no-store (always fresh from yfinance)
  • Fetch URL adds &t=Date.now() cache-buster
  • Console-log the final visible-range so the operator can confirm zoom
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
    symbol="XOM", current_price=160.0,
    ema_55=150.80, ema_100=145.00, ema_200=134.20, rsi_14=65.9,
    context_flags=[],
)
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="XOM", snap=snap, chart_data=chart_data)


# ---------------------------------------------------------------------------
# 1. Deferred zoom via requestAnimationFrame
# ---------------------------------------------------------------------------
print("\n[1] Zoom call deferred 2 frames so LWC finishes ingesting setData")
check("_setDefaultVisibleRange wraps zoom in requestAnimationFrame",
      "requestAnimationFrame" in html_chart
      and "_setDefaultVisibleRange" in html_chart)
check("Two-frame defer (rAF inside rAF)",
      html_chart.count("requestAnimationFrame") >= 4)
check("doZoom inner function defined",
      "function doZoom()" in html_chart)
check("zoom logs final visible range to console",
      "[CC] zoom: tf=" in html_chart)
check("init-time zoom also deferred",
      "init zoom" in html_chart or "[CC] init zoom" in html_chart)


# ---------------------------------------------------------------------------
# 2. Cache busting on /chart-tf fetch
# ---------------------------------------------------------------------------
print("\n[2] /chart-tf cache busting")
check("Fetch URL adds cache-buster timestamp",
      "'&t=' + Date.now()" in html_chart)
# Server-side header (in render_html → not visible here, but we can grep
# the source file directly for the header).
src = (REPO / "scan_setups.py").read_text()
check("Server /chart-tf sets Cache-Control: no-store (Wave 21)",
      'self.send_header("Cache-Control", "no-store")' in src
      and "Wave 21" in src)


# ---------------------------------------------------------------------------
# 3. DEFAULT_VISIBLE_BARS still has the right values per TF
# ---------------------------------------------------------------------------
print("\n[3] Default visible bars per TF still correct")
check("1m default = 60 bars (~1 hour)",
      "'1m': 60" in html_chart)
check("3m default = 60 bars (~3 hours)",
      "'3m': 60" in html_chart)
check("5m default = 78 bars (~1 RTH day)",
      "'5m': 78" in html_chart)
check("30m default = 52 bars (~4 RTH days)",
      "'30m': 52" in html_chart)
check("1h default = 50 bars (~1 week)",
      "'1h': 50" in html_chart)
check("1D default = 120 bars (~6 months)",
      "'1D': 120" in html_chart)
check("1M default = 60 bars (~5 years)",
      "'1M': 60" in html_chart)
check("ALL default = 240 bars (~20 years monthly)",
      "'ALL': 240" in html_chart)


# ---------------------------------------------------------------------------
# 4. Regression — Waves 14-20 still in place
# ---------------------------------------------------------------------------
print("\n[4] Regression — earlier waves intact")
check("17 TF buttons still present",
      all(f'data-tf="{t}"' in html_chart for t in
          ["1m","3m","5m","15m","30m","45m","1h","2h","3h","4h",
           "1D","1W","1M","3M","6M","12M","ALL"]))
check("Wave 16 chart-style selector still present",
      'id="chart-style-select"' in html_chart)
check("Wave 16 _applyTfData rebuilds series",
      "function _applyTfData(" in html_chart
      and "removeSeries(h.candleSeries)" in html_chart)
check("All-Time Analysis button still rendered",
      'id="all-time-btn"' in html_chart)
check("38 detectors still registered",        len(cc.DETECTORS) == 38)

# Main page regression
html_main = cc.render_html(setups=[], scanned=0, duration_s=0.0)
check("Wave 20 — My Watchlist bar still removed",
      'class="mylist-bar"' not in html_main)
check("Wave 17 — handleScanSubmit still wired",
      "handleScanSubmit(event)" in html_main)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 21:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
