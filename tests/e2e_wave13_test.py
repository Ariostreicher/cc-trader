"""E2E for Wave 13 — collapsible sections, hover tooltips, annotation
management, favicon + PWA icon.
"""

from __future__ import annotations
import sys, json
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
    support_levels=[195.0, 185.0], resistance_levels=[210.0, 220.0],
    camarilla={"h4":207,"h3":204,"h2":202,"h1":201,"l1":199,"l2":198,"l3":196,"l4":193,"prev_close":201},
    context_flags=[],
)
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],"ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}

html_main = cc.render_html(setups=[], scanned=0, duration_s=0.1, snapshots=[snap],
                            levels_by_symbol={"AAPL": snap})
html_chart = cc.render_single_chart_html(symbol="AAPL", snap=snap, chart_data=chart_data)


# ---------------------------------------------------------------------------
# 1. Collapsible sections on main page
# ---------------------------------------------------------------------------
print("\n[1] Collapsible sections on main page")
check("collapsible-section CSS defined", ".collapsible-section {" in html_main)
check("<details> wrapper used (HTML-native collapsible)",
      "<details" in html_main and "</details>" in html_main)
check("Watchlist Monitor section collapsible",
      "📡 Watchlist Monitor" in html_main and "click to expand" in html_main)
check("My Manual Setups section collapsible",
      "📝 My Manual Setups" in html_main)
check("Forming Setups section collapsible",
      "👁 Forming Setups" in html_main)
check("All Tickers Overview section collapsible",
      "📊 All Tickers Overview" in html_main)
check("Trade Journal section collapsible",
      "📒 Trade Journal" in html_main)
# Verify these are NOT auto-opened (no `open` attribute)
check("Watchlist Monitor section starts COLLAPSED (no open attr)",
      '<details class="collapsible-section">' in html_main)


# ---------------------------------------------------------------------------
# 2. Hover tooltips on chart page
# ---------------------------------------------------------------------------
print("\n[2] Hover tooltips on chart lines")
check("subscribeCrosshairMove hooked up",     "subscribeCrosshairMove" in html_chart)
check("hover-tooltip CSS defined",            ".hover-tooltip {" in html_chart)
check("tooltipEl created/looked up",          "chart-tooltip" in html_chart)
check("price-line match tolerance applied",   "0.004" in html_chart or "tol = Math.abs" in html_chart)
check("EMA series included in tooltip search",
      "emaSeries" in html_chart and "Object.keys(emaSeries)" in html_chart)


# ---------------------------------------------------------------------------
# 3. Annotation management — "My Drawings" list
# ---------------------------------------------------------------------------
print("\n[3] My Drawings list")
check("my-drawings-list element present",     'id="my-drawings-list"' in html_chart)
check("annotations-list CSS defined",          ".annotations-list {" in html_chart)
check("renderDrawingsList() function defined", "function renderDrawingsList(" in html_chart)
check("deleteAnnotation() function defined",   "function deleteAnnotation(" in html_chart)
check("addAnnotation re-renders the list",     "renderDrawingsList(sym, chartId)" in html_chart)
check("renderDrawingsList called on page load",
      "renderDrawingsList('AAPL'" in html_chart)


# ---------------------------------------------------------------------------
# 4. Favicon + PWA manifest
# ---------------------------------------------------------------------------
print("\n[4] Favicon + PWA app icon")
fav_tags = cc._favicon_link_tags()
check("favicon <link rel=icon> tag",          'rel="icon"' in fav_tags)
check("apple-touch-icon tag",                  'rel="apple-touch-icon"' in fav_tags)
check("manifest tag",                          'rel="manifest"' in fav_tags)
check("theme-color meta",                      'name="theme-color"' in fav_tags)
check("apple-mobile-web-app-title meta",       'apple-mobile-web-app-title' in fav_tags)
check("main page <head> includes favicon tags", '/icon.svg' in html_main and 'manifest' in html_main)
check("chart page <head> includes favicon tags", '/icon.svg' in html_chart and 'manifest' in html_chart)
# Logo SVG content
check("LOGO_SVG is a real SVG",                cc.LOGO_SVG.startswith("<svg") and "</svg>" in cc.LOGO_SVG)
check("LOGO_SVG includes a viewBox",           'viewBox="0 0 64 64"' in cc.LOGO_SVG)
check("LOGO_SVG includes the candle elements (chart-style logo)",
      "<rect" in cc.LOGO_SVG and 'fill="#22c55e"' in cc.LOGO_SVG)
# Manifest content
manifest = json.loads(cc._build_manifest_json())
check("manifest has name CC Trader",           manifest.get("name") == "CC Trader")
check("manifest start_url is /",               manifest.get("start_url") == "/")
check("manifest display is standalone (PWA)",  manifest.get("display") == "standalone")
check("manifest theme_color set",              manifest.get("theme_color") == "#fbbf24")
check("manifest icons array non-empty",        len(manifest.get("icons", [])) > 0)


# ---------------------------------------------------------------------------
# 5. Regression — features still in place
# ---------------------------------------------------------------------------
print("\n[5] No regressions")
check("38 detectors still registered",        len(cc.DETECTORS) == 38)
check("Camarilla function still callable",    callable(cc.compute_camarilla_pivots))
check("render_single_chart_html still works", callable(cc.render_single_chart_html))
check("main page still has search bar",       'name="symbols"' in html_main)
check("main page still has tools-bar",        'class="tools-bar"' in html_main)
check("main page still has regime strip",     'class="regime-strip"' in html_main)
check("main page still has 'Open Chart →' links",
      "/chart?symbol=AAPL" in html_main)
check("chart page still has 1H/1D/1W/1M TF selector",
      all(f'data-tf="{t}"' in html_chart for t in ["1H","1D","1W","1M"]))
check("chart page still has view toggle",     "📊 CC View" in html_chart and "📈 TradingView" in html_chart)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 13:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
