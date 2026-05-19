"""E2E for Wave 12 — lazy chart loading + standalone /chart page.

Verifies:
  • Main page (render_html) no longer pre-renders 53-ticker charts (memory fix)
  • Main page has "Open Chart →" links to /chart?symbol=X for every card
  • render_single_chart_html() emits a full standalone chart page
  • Standalone page has all panels: chart + toolbar + Key Levels + Equity + Setup + Watches
  • EMA 8 + EMA 21 are drawn on chart
  • Camarilla pivots are in price lines
  • build_single_chart_response() top-level orchestrator works
  • build_multi_tf_chart_data + build_snapshot_for_symbol are reusable
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
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  → {detail}" if detail else ""))


# Build a realistic Snapshot with all Wave 1+5+12 fields populated
snap = cc.Snapshot(
    symbol="AAPL", current_price=200.0,
    ema_55=195.0, ema_100=185.0, ema_200=170.0, rsi_14=58.0,
    support_levels=[195.0, 185.0], resistance_levels=[210.0, 220.0],
    bid=199.95, ask=200.05, spread_pct=0.05, avg_volume=50_000_000,
    fib={"high": 250.0, "low": 100.0, "direction": "up",
         "retracements": {"0.236": 214.6, "0.382": 192.7, "0.500": 175.0,
                          "0.618": 157.3, "0.660": 151.0, "0.786": 132.1, "1.000": 100.0},
         "extensions":  {"1.272": 290.8, "1.414": 312.1, "1.618": 342.7}},
    pivots={"pp": 201.0, "r1": 205.0, "r2": 210.0, "s1": 197.0, "s2": 192.0,
            "prev_high": 205.0, "prev_low": 197.0, "prev_close": 201.0},
    vwap_anchored=198.0,
    round_numbers=[180.0, 190.0, 210.0, 220.0],
    pivots_weekly={"pp": 200.5, "r1": 206.0, "r2": 212.0, "s1": 195.0, "s2": 190.0,
                   "prev_high": 206.0, "prev_low": 195.0, "prev_close": 200.5},
    pivots_monthly={"pp": 198.0, "r1": 215.0, "s1": 185.0,
                    "prev_high": 215.0, "prev_low": 185.0, "prev_close": 198.0},
    recent_weekly=[
        {"period_end": "2025-01-03", "high": 205.0, "low": 196.0},
        {"period_end": "2025-01-10", "high": 208.0, "low": 198.0},
    ],
    recent_monthly=[
        {"period_end": "2024-12-31", "high": 215.0, "low": 185.0},
    ],
    vp_weekly={"poc": 201.5, "vah": 207.0, "val": 196.0},
    vp_monthly={"poc": 199.0, "vah": 213.0, "val": 188.0},
    naked_pocs=[
        {"period_end": "2024-12-13", "poc": 192.0, "naked": True, "distance_pct": -4.0},
        {"period_end": "2024-11-29", "poc": 188.5, "naked": True, "distance_pct": -5.75},
    ],
    camarilla={"h4": 207.0, "h3": 204.0, "h2": 202.5, "h1": 201.5,
               "l1": 199.5, "l2": 198.5, "l3": 196.0, "l4": 193.0,
               "prev_close": 201.0},
    context_flags=[],
)

chart_data = {
    "default_tf": "1D",
    "timeframes": {
        "1D": {
            "candles":[{"time":"2025-01-01","open":200,"high":201,"low":199,"close":200}],
            "volume":[{"time":"2025-01-01","value":1_000_000,"color":"#22c55e55"}],
            "ema_8":[], "ema_21":[],
            "ema_55":[], "ema_100":[], "ema_200":[],
        },
    },
}

setup = cc.Setup(
    symbol="AAPL", name="EMA Pullback (long)", direction="long",
    entry=200.0, stop_loss=195.0, targets=[210.0, 220.0],
    current_price=200.0, conviction=0.72,
    reasoning="Bull EMA align", citation="First 18.pdf p.67",
    ai_analysis="Test commentary.",
    context_flags=[],
)


# ---------------------------------------------------------------------------
# 1. Main page (render_html) no longer pre-renders charts
# ---------------------------------------------------------------------------
print("\n[1] Main page (render_html) — slim, no charts")
html_main = cc.render_html(
    setups=[setup], scanned=1, duration_s=0.1,
    snapshots=[snap],
    levels_by_symbol={"AAPL": snap},
)
check("main page is not empty", isinstance(html_main, str) and len(html_main) > 1000)
check("main page does NOT have inline lwc-chart div",
      'class="lwc-chart"' not in html_main)
check("main page does NOT bake in chart data for tickers (empty cc_charts_data)",
      "window.cc_charts_data = {}" in html_main or '"AAPL"' not in html_main.split("window.cc_charts_data")[1][:500] if "window.cc_charts_data" in html_main else True)
check("main page does NOT pre-render TradingView widget host divs",
      "tv_host_setup_" not in html_main and "tv_host_snap_" not in html_main)
check("main page DOES have 'Open Chart →' links to /chart?symbol=AAPL",
      "/chart?symbol=AAPL" in html_main and "Open Chart" in html_main)
check("main page still has compact ticker-block cards",
      'class="ticker-block ticker-compact"' in html_main)
check("main page still has Trade Journal panel",
      'id="journal-panel"' in html_main)
check("main page still has Manual setup section",
      'id="manual-section"' in html_main)
check("My Watchlist bar removed (Wave 20 — unified into Add & Scan)",
      'class="mylist-bar"' not in html_main)
check("main page still has Monitor table",
      'id="monitor-table"' in html_main)
check("main page still has search box",
      'name="symbols"' in html_main and 'id="search-input"' in html_main)


# ---------------------------------------------------------------------------
# 2. render_single_chart_html — full standalone /chart page
# ---------------------------------------------------------------------------
print("\n[2] Standalone /chart page has full UI")
html = cc.render_single_chart_html(
    symbol="AAPL", snap=snap,
    chart_data=chart_data,
    setups=[setup],
)
check("chart page is non-empty", isinstance(html, str) and len(html) > 5000)
check("loads Lightweight Charts library",
      "lightweight-charts" in html)
check("loads TradingView library",
      "s3.tradingview.com/tv.js" in html)
check("has CC View / TradingView toggle buttons",
      "📊 CC View" in html and "📈 TradingView" in html)
check("has TF selector buttons 1h/1D/1W/1M (Wave 14: 1h lowercase + 13 more)",
      all(f'data-tf="{t}"' in html for t in ["1h","1D","1W","1M"]))
check("has annotation buttons (Note / Line / Clear)",
      "✏ Note" in html and "+ Line" in html and "Clear my drawings" in html)
check("has countdown badge",
      'class="countdown-badge"' in html)
check("has 'Back to scanner' link",
      'href="/"' in html and 'Back to scanner' in html)
check("symbol header shows AAPL",
      "<h1>AAPL</h1>" in html)
check("current price displayed prominently",
      "$200.00" in html)


# ---------------------------------------------------------------------------
# 3. EMA 8 + EMA 21 added to chart overlay
# ---------------------------------------------------------------------------
print("\n[3] EMA 8 + EMA 21 drawn on chart")
check("EMA 8 added as line series",   "addEMA('ema_8'" in html and "EMA 8" in html)
check("EMA 21 added as line series",  "addEMA('ema_21'" in html and "EMA 21" in html)
check("legend shows EMA 8",            "EMA 8</div>" in html)
check("legend shows EMA 21",           "EMA 21</div>" in html)
check("EMA 55/100/200 still drawn",
      "addEMA('ema_55'" in html and "addEMA('ema_100'" in html and "addEMA('ema_200'" in html)


# ---------------------------------------------------------------------------
# 4. Camarilla pivots in price-lines
# ---------------------------------------------------------------------------
print("\n[4] Camarilla pivots in chart price-lines")
m = re.search(r"data-lines='([^']+)'", html)
check("chart has data-lines attribute", m is not None)
if m:
    lines = _json.loads(m.group(1))
    titles = [l["title"] for l in lines]
    check("Camarilla H1 line present",  any("CAM H1" in t for t in titles))
    check("Camarilla H4 line present",  any("CAM H4" in t for t in titles))
    check("Camarilla L1 line present",  any("CAM L1" in t for t in titles))
    check("Camarilla L4 line present",  any("CAM L4" in t for t in titles))
    # And all other scanner levels too
    check("Fib levels still drawn",      sum(1 for t in titles if t.startswith("Fib")) >= 4)
    check("DAILY pivots still drawn",    sum(1 for t in titles if "DAILY" in t) >= 2)
    check("WEEKLY pivots still drawn",   sum(1 for t in titles if "WEEKLY" in t) >= 2)
    check("MONTHLY pivots still drawn",  sum(1 for t in titles if "MONTHLY" in t) >= 1)
    check("WEEKLY POC drawn",            any("WEEKLY POC" in t for t in titles))
    check("MONTHLY POC drawn",           any("MONTHLY POC" in t for t in titles))
    check("nPOCs drawn",                 any("nPOC" in t for t in titles))
    check("VWAP drawn",                  any("VWAP" in t for t in titles))
    check("Entry line drawn (fired setup)", any("Entry" in t for t in titles))
    check("Stop line drawn",              any("Stop" in t for t in titles))
    check("Target lines drawn",           any("T1" in t for t in titles))


# ---------------------------------------------------------------------------
# 5. Side panel — Key Levels, Equity, Setup card, Watches
# ---------------------------------------------------------------------------
print("\n[5] Side panel content")
check("Key Levels panel present",         "📐 Key Levels" in html)
check("EMA 55 / 100 / 200 in Key Levels", "EMA 55" in html and "EMA 100" in html and "EMA 200" in html)
check("RSI shown",                        "RSI 14" in html or "RSI:" in html)
check("Fired setup card visible",         "EMA Pullback (long)" in html or "Entry" in html)
check("Action bar — Toggle Watchlist",    "Toggle Watchlist" in html)
check("Action bar — Set price alarm",     "Set price alarm" in html)
check("Action bar — Add manual setup",    "Add manual setup" in html)
check("Action bar — Open in TradingView.com", "Open in TradingView.com" in html)


# ---------------------------------------------------------------------------
# 6. Module-level helpers callable
# ---------------------------------------------------------------------------
print("\n[6] Module-level helpers")
check("build_snapshot_for_symbol exists",
      callable(getattr(cc, "build_snapshot_for_symbol", None)))
check("build_multi_tf_chart_data exists",
      callable(getattr(cc, "build_multi_tf_chart_data", None)))
check("build_single_chart_response exists",
      callable(getattr(cc, "build_single_chart_response", None)))
check("serialize_chart_tf exists",
      callable(getattr(cc, "serialize_chart_tf", None)))
check("render_single_chart_html exists",
      callable(getattr(cc, "render_single_chart_html", None)))


# ---------------------------------------------------------------------------
# 7. serialize_chart_tf includes EMA 8 + EMA 21
# ---------------------------------------------------------------------------
print("\n[7] serialize_chart_tf includes EMA 8 + EMA 21")
n = 60
prices = 100 + np.cumsum(np.random.normal(0, 1, n))
df = pd.DataFrame({
    "open": prices, "high": prices+1, "low": prices-1, "close": prices,
    "volume": np.full(n, 1_000_000),
}, index=pd.date_range("2025-01-01", periods=n, freq="B"))
serialized = cc.serialize_chart_tf(df, daily_format=True)
check("serialize_chart_tf returns ema_8 series",  "ema_8" in serialized)
check("serialize_chart_tf returns ema_21 series", "ema_21" in serialized)
check("ema_8 has values",                          len(serialized["ema_8"]) > 0)
check("ema_21 has values",                         len(serialized["ema_21"]) > 0)


# ---------------------------------------------------------------------------
# 8. Snapshot dataclass has camarilla field
# ---------------------------------------------------------------------------
print("\n[8] Snapshot.camarilla field")
empty_snap = cc.Snapshot(symbol="X", current_price=100.0)
check("Snapshot has camarilla attribute", hasattr(empty_snap, "camarilla"))
check("Snapshot.camarilla defaults to None", empty_snap.camarilla is None)
check("Snapshot.camarilla can hold a dict", snap.camarilla is not None and "h4" in snap.camarilla)


# ---------------------------------------------------------------------------
# 9. Empty handling
# ---------------------------------------------------------------------------
print("\n[9] Empty / missing data handled gracefully")
empty_html = cc.render_single_chart_html(
    symbol="X", snap=None, chart_data={"default_tf":"1D","timeframes":{}},
)
check("empty snap produces a valid HTML page (not crash)",
      isinstance(empty_html, str) and "<h1>X</h1>" in empty_html)


# ---------------------------------------------------------------------------
# 10. Regression — no detector / Snapshot field removed
# ---------------------------------------------------------------------------
print("\n[10] Regression — features preserved")
check("38 detectors still registered", len(cc.DETECTORS) == 38)
check("BACKTESTED_CONVICTION has every detector prior",
      len(cc.BACKTESTED_CONVICTION) >= 38)
check("EMA helpers still callable (ema, sma, rsi, atr)",
      callable(cc.ema) and callable(cc.sma) and callable(cc.rsi) and callable(cc.atr))
check("compute_camarilla_pivots still callable",
      callable(cc.compute_camarilla_pivots))
check("compute_fib_levels still callable",
      callable(cc.compute_fib_levels))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 12:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
