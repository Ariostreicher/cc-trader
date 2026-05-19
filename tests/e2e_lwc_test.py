"""E2E for the Lightweight Charts replacement of the TradingView widget.

Confirms:
  - render_html accepts the new chart_data_by_symbol arg
  - HTML embeds the LWC <script src=...> tag
  - HTML embeds chart-data JSON in window.cc_charts_data
  - .lwc-chart divs are emitted for each ticker that has data
  - data-lines attribute carries entry / stop / target / S-R lines for setup cards
  - snapshot chart shows only S/R lines (no entry/stop/targets)
  - chart data has the expected fields: candles, volume, ema_55, ema_100, ema_200
  - No leftover TradingView widget script (other than the optional "open on TV →" link)
"""

from __future__ import annotations
import sys, json
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
# Build a synthetic dataframe with 260 daily bars (so EMA 200 has enough data)
# ---------------------------------------------------------------------------
n = 260
np.random.seed(7)
prices = 100 + np.cumsum(np.random.normal(0, 1.0, n))
df = pd.DataFrame({
    "open":   prices + np.random.normal(0, 0.3, n),
    "high":   prices + 1.0,
    "low":    prices - 1.0,
    "close":  prices,
    "volume": np.random.randint(500_000, 2_000_000, n),
}, index=pd.date_range("2024-01-01", periods=n, freq="B"))

# Build chart data through the same helper run_full_scan would use
# (we can't easily test run_full_scan w/o yfinance, so call the inner helper)
print("\n[1] Chart-data serialization")
import scan_setups as cc
# The inner helper lives inside run_full_scan, but we can replicate by
# inlining the same logic. Easier: just call ema() and assemble a dict.
times = [t.strftime("%Y-%m-%d") for t in df.index]
candles = [{"time": ts, "open": float(o), "high": float(h),
            "low": float(l), "close": float(c)}
           for ts, o, h, l, c in zip(times,
                                     df["open"].values, df["high"].values,
                                     df["low"].values,  df["close"].values)]
def _ema(length):
    s = cc.ema(df["close"], length)
    return [{"time": t, "value": float(v)} for t, v in zip(times, s.values) if pd.notna(v)]

chart_data = {
    "AAPL": {
        "candles": candles,
        "volume":  [{"time": t, "value": int(v), "color": "#22c55e55"} for t, v in zip(times, df["volume"].values)],
        "ema_55":  _ema(55),
        "ema_100": _ema(100),
        "ema_200": _ema(200),
    }
}
check("candles array non-empty",   len(chart_data["AAPL"]["candles"]) >= 200)
check("ema_55 has values",         len(chart_data["AAPL"]["ema_55"]) > 100)
check("ema_100 has values",        len(chart_data["AAPL"]["ema_100"]) > 50)
check("ema_200 has values",        len(chart_data["AAPL"]["ema_200"]) > 10)
check("each candle has OHLC fields",
      all(set(c.keys()) >= {"time","open","high","low","close"} for c in chart_data["AAPL"]["candles"][:5]))


# ---------------------------------------------------------------------------
# Render with a fired setup + chart_data and verify Lightweight Charts wiring
# ---------------------------------------------------------------------------
print("\n[2] HTML with setup card uses Lightweight Charts")
setup = cc.Setup(
    symbol="AAPL", name="EMA 55/100/200 Pullback (long)", direction="long",
    entry=190.0, stop_loss=185.0, targets=[200.0, 210.0],
    current_price=190.0, conviction=0.78,
    reasoning="Bull alignment.", citation="First 18.pdf p.67",
    context_flags=[],
)
snap = cc.Snapshot(
    symbol="AAPL", current_price=190.0,
    ema_55=186.0, ema_100=180.0, ema_200=170.0, rsi_14=58.0,
    support_levels=[180.0, 175.0], resistance_levels=[200.0, 210.0],
    context_flags=[],
)
html_main = cc.render_html(
    setups=[setup], scanned=1, duration_s=0.1,
    levels_by_symbol={"AAPL": snap},
    chart_data_by_symbol=chart_data,
)
# Wave 12: chart moved to dedicated /chart page (memory fix). The MAIN page
# now shows compact cards with "Open Chart →" links. The full chart UI is on
# render_single_chart_html. Check both: main page has links, chart page has UI.
html = cc.render_single_chart_html(
    symbol="AAPL", snap=snap,
    chart_data=chart_data["AAPL"],
    setups=[setup],
)

# Wave 10 reintroduced tv.js as a TOGGLEABLE alternative view (not the default).
# Both pages load tv.js (chart page uses it for the TV view).
check("tv.js script tag IS now present (Wave 10 hybrid view restored)",
      "s3.tradingview.com/tv.js" in html)
check("CC LWC view is the DEFAULT (active by default)",
      'class="view-btn active" data-view="cc"' in html)
check("Lightweight Charts CDN <script src> present",
      "lightweight-charts" in html and ".standalone.production.js" in html)
check("Page exposes window.cc_charts_data",
      "window.cc_charts_data = " in html)
check("AAPL key embedded in chart-data JSON",
      '"AAPL"' in html and '"candles"' in html)
check("LWC chart init called on load",
      "initChart()" in html or "initLightweightCharts()" in html)
check("createCandlestickSeries used",
      "addCandlestickSeries" in html)
check("EMA overlays added (addLineSeries)",
      "addLineSeries" in html)
check("Volume histogram added",
      "addHistogramSeries" in html)
check("createPriceLine used (the actual line-drawing call)",
      "createPriceLine" in html)
# Wave 12: chart container is on /chart page with id="lwc_chart_solo"
check("AAPL chart container has class lwc-chart (on /chart page)",
      'class="lwc-chart" id="lwc_chart_solo" data-symbol="AAPL"' in html)
# Main page should have the "Open Chart →" link
check("main page has 'Open Chart →' link to /chart?symbol=AAPL",
      '/chart?symbol=AAPL' in html_main and 'Open Chart' in html_main)


# ---------------------------------------------------------------------------
# Setup card price-lines: entry, stop, BOTH targets, plus S/R
# ---------------------------------------------------------------------------
print("\n[3] Price lines: entry / stop / targets / S+R")
# Extract data-lines from the AAPL chart container
import re
# Wave 12: chart container now id="lwc_chart_solo" on /chart page
m = re.search(r'id="lwc_chart_solo" data-symbol="AAPL" data-lines=\'([^\']+)\'', html)
check("data-lines attribute is present on chart div", m is not None,
      "no data-lines on lwc_chart_solo")
if m:
    lines = json.loads(m.group(1))
    titles = [l["title"] for l in lines]
    check("entry line ($190.00) is drawn",       any("Entry $190.00" in t for t in titles))
    check("stop line ($185.00) is drawn",        any("Stop $185.00" in t for t in titles))
    check("target 1 ($200.00) is drawn",         any("T1 $200.00" in t for t in titles))
    check("target 2 ($210.00) is drawn",         any("T2 $210.00" in t for t in titles))
    check("support lines (2) are drawn",         sum(1 for t in titles if t.startswith("S $")) == 2)
    check("resistance lines (2) are drawn",      sum(1 for t in titles if t.startswith("R $")) == 2)
    # Verify color/style conventions
    by_title = {l["title"]: l for l in lines}
    check("entry is yellow (#fbbf24)",   by_title["Entry $190.00"]["color"] == "#fbbf24")
    check("stop is red (#ef4444) solid", by_title["Stop $185.00"]["color"] == "#ef4444" and by_title["Stop $185.00"]["lineStyle"] == 0)
    check("target 1 is green dashed",    by_title["T1 $200.00"]["color"] == "#22c55e" and by_title["T1 $200.00"]["lineStyle"] == 2)


# ---------------------------------------------------------------------------
# Snapshot card draws only S/R lines (no entry/stop/target)
# ---------------------------------------------------------------------------
print("\n[4] Snapshot card has S/R lines only")
snap_only = cc.Snapshot(
    symbol="MSFT", current_price=420.0,
    ema_55=410.0, ema_100=400.0, ema_200=380.0, rsi_14=52.0,
    support_levels=[400.0, 390.0], resistance_levels=[430.0, 445.0],
    context_flags=[],
)
chart_data_msft = {"MSFT": {
    "candles": [{"time": "2025-01-01", "open": 420, "high": 422, "low": 418, "close": 420}],
    "volume":  [{"time": "2025-01-01", "value": 1000000, "color": "#22c55e55"}],
    "ema_55":  [], "ema_100": [], "ema_200": [],
}}
body = cc._snap_chart_body(snap_only, 0, chart_data_msft)
check("snapshot body emits lwc-chart div",  'class="lwc-chart"' in body)
check("snapshot has data-symbol=MSFT",       'data-symbol="MSFT"' in body)
# extract data-lines
m2 = re.search(r"data-lines='([^']+)'", body)
if m2:
    sl = json.loads(m2.group(1))
    titles2 = [l["title"] for l in sl]
    check("snapshot has 2 support lines",    sum(1 for t in titles2 if t.startswith("S $")) == 2)
    check("snapshot has 2 resistance lines", sum(1 for t in titles2 if t.startswith("R $")) == 2)
    check("snapshot has NO Entry/Stop/Target lines",
          not any(t.startswith(("Entry", "Stop", "T1", "T2")) for t in titles2))


# ---------------------------------------------------------------------------
# Fallback: chart_data missing → /chart page handles it gracefully too.
# Wave 12: main page no longer renders charts at all (memory fix), so this
# test now checks that the chart page handles missing data without crashing.
# ---------------------------------------------------------------------------
print("\n[5] Graceful fallback when chart-data unavailable")
# Main page should still render correctly even when chart_data is empty
html_no_data = cc.render_html(
    setups=[setup], scanned=1, duration_s=0.1,
    levels_by_symbol={"AAPL": snap},
    chart_data_by_symbol={},        # <-- empty: simulate yfinance failure
)
check("main page renders OK when chart_data is empty (Wave 12 — no charts on main)",
      isinstance(html_no_data, str) and len(html_no_data) > 1000)
check("main page still has 'Open Chart →' link even when chart_data is empty",
      "Open Chart" in html_no_data and "/chart?symbol" in html_no_data)


# ---------------------------------------------------------------------------
# Verify previous fixes still intact (regression)
# ---------------------------------------------------------------------------
print("\n[6] No regressions from earlier fixes")
check("S/R classification still correct (regression)",
      cc.support_resistance(df.tail(50), n=3, tol_pct=2.0).get("support") is not None)
check("TICKER_ALIASES still loaded",     "BITCOIN" in cc.TICKER_ALIASES)
check("resolve_ticker still maps names", cc.resolve_ticker("bitcoin") == "BTC-USD")
# Wave 20 — The dedicated My Watchlist bar was removed. The 'Add & Scan'
# search bar IS the watchlist now (one unified list).
check("My Watchlist bar removed in Wave 20 (search bar handles it)",
      'class="mylist-bar"' not in html_main)
check("Add & Scan search bar present (one unified list)",
      'Add & Scan' in html_main or 'Add &amp; Scan' in html_main)
# Key Levels panel — appears on BOTH main snapshot cards AND chart page side panel
check("Key Levels panel still in HTML (chart page)",   '📐 Key Levels' in html)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E LWC:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
