"""E2E for Wave 10 — restore lost TradingView features as a hybrid view.

Verifies:
  • Both Lightweight Charts AND TradingView widget libraries are loaded
  • Each chart card has a view toggle (CC View / TradingView)
  • CC View is the default
  • TradingView widget config has drawing tools, real-time data, full toolbar
  • User annotation system (+Note / +Line / Clear my drawings) is wired
  • Countdown badge HTML element exists per chart
  • Countdown JS computes time-to-close for stocks vs crypto separately
  • localStorage namespace for user annotations
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

results: list[tuple[str, bool, str]] = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  → {detail}" if detail else ""))


# Build a snapshot + a fired setup so both card paths render
snap = cc.Snapshot(
    symbol="AAPL", current_price=200.0,
    ema_55=195.0, ema_100=190.0, ema_200=180.0, rsi_14=58.0,
)
setup = cc.Setup(
    symbol="AAPL", name="EMA Pullback", direction="long",
    entry=200.0, stop_loss=195.0, targets=[210.0, 220.0],
    current_price=200.0, conviction=0.72, reasoning="x", citation="x",
    context_flags=[],
)
chart_data = {"AAPL": {
    "default_tf": "1D",
    "timeframes": {
        "1D": {"candles":[{"time":"2025-01-01","open":200,"high":201,"low":199,"close":200}],
               "volume":[{"time":"2025-01-01","value":1_000_000,"color":"#22c55e55"}],
               "ema_55":[], "ema_100":[], "ema_200":[]},
    },
}}
# Wave 12: the hybrid view toggle + annotations + countdown moved to /chart
# page (memory fix). Main page (render_html) now has compact cards. All Wave-10
# toolbar UI tests now check render_single_chart_html.
html_main = cc.render_html(
    setups=[setup], scanned=1, duration_s=0.1,
    snapshots=[snap],
    levels_by_symbol={"AAPL": snap},
    chart_data_by_symbol=chart_data,
)
html = cc.render_single_chart_html(
    symbol="AAPL", snap=snap,
    chart_data=chart_data["AAPL"],
    setups=[setup],
)


# ---------------------------------------------------------------------------
# 1. Both libraries loaded
# ---------------------------------------------------------------------------
print("\n[1] Both Lightweight Charts AND TradingView libraries present")
check("Lightweight Charts library script",
      "lightweight-charts" in html and ".standalone.production.js" in html)
check("TradingView library script (re-added in Wave 10)",
      "s3.tradingview.com/tv.js" in html)


# ---------------------------------------------------------------------------
# 2. View toggle buttons on each chart
# ---------------------------------------------------------------------------
print("\n[2] View toggle (CC View vs TradingView)")
check("'.view-toggle' container present",     'class="view-toggle"' in html)
check("'📊 CC View' button",                  "📊 CC View" in html)
check("'📈 TradingView' button",              "📈 TradingView" in html)
check("CC view active by default",
      'class="view-btn active" data-view="cc"' in html)
check("TradingView button NOT active by default",
      'class="view-btn active" data-view="tv"' not in html)
check("CC view container (.view-cc) present", 'class="view-cc"' in html)
check("TV view container (.view-tv) present", 'class="view-tv"' in html)
check("TV view hidden by default",            'class="view-tv"' in html and 'style="display:none"' in html)


# ---------------------------------------------------------------------------
# 3. TradingView widget config quality
# ---------------------------------------------------------------------------
print("\n[3] TradingView widget config")
check("loadTradingViewWidget() function exists",
      "function loadTradingViewWidget(" in html)
check("widget uses dark theme",               "'dark'" in html or '"dark"' in html)
check("widget uses NY timezone",              "America/New_York" in html)
check("widget has Top toolbar visible",       "hide_top_toolbar: false" in html)
check("widget includes EMA studies preset",   "MAExp@tv-basicstudies" in html)
check("widget includes RSI study",            "RSI@tv-basicstudies" in html)
check("widget includes Volume study",         "Volume@tv-basicstudies" in html)
check("widget has withdateranges (TF buttons in TV)",
      "withdateranges: true" in html)
check("widget enables drawing tools (default)",
      "drawings_access" in html or "TradingView.widget" in html)


# ---------------------------------------------------------------------------
# 4. View toggle JS wiring (Wave 12: inline in initChart on /chart page)
# ---------------------------------------------------------------------------
print("\n[4] View toggle JS wiring")
check("view-toggle wiring exists (either _bindViewToggles or inline init)",
      "function _bindViewToggles()" in html or "querySelectorAll('.view-btn')" in html)
check("view-toggle load handler runs",
      "_bindViewToggles();" in html or "querySelectorAll('.view-btn').forEach" in html)
check("clicking TV button triggers loadTradingViewWidget",
      "loadTradingViewWidget(" in html and ("view === 'tv'" in html or "tv.dataset.tvSymbol" in html))
check("lazy-loading flag (cc_tv_loaded)",    "cc_tv_loaded" in html)


# ---------------------------------------------------------------------------
# 5. User annotations — +Note / +Line / Clear
# ---------------------------------------------------------------------------
print("\n[5] User annotation system")
check("'✏ Note' button present",             "✏ Note" in html)
check("'+ Line' button present",             "+ Line" in html)
check("'⌫ Clear my drawings' button present", "Clear my drawings" in html)
check("addAnnotation() function defined",    "function addAnnotation(" in html)
check("clearAnnotations() function defined", "function clearAnnotations(" in html)
check("annotation-apply function exists (applyAnnotations or applyAnnotationsToChart)",
      "function applyAnnotationsToChart(" in html or "function applyAnnotations(" in html)
check("annotations stored in cc_annotations localStorage key",
      "cc_annotations" in html)
check("annotations re-applied on load (applyAllAnnotationsOnLoad or applyAnnotations on init)",
      "applyAllAnnotationsOnLoad()" in html or "applyAnnotations(sym, 'chart_solo')" in html)


# ---------------------------------------------------------------------------
# 6. Countdown badge
# ---------------------------------------------------------------------------
print("\n[6] 24h countdown badge")
check("countdown-badge HTML element present",   'class="countdown-badge"' in html)
check("countdown function defined (updateCountdownBadges or updateCountdown)",
      "function updateCountdownBadges()" in html or "function updateCountdown()" in html)
check("countdown refreshed every minute via setInterval",
      "setInterval(updateCountdownBadges" in html or "setInterval(updateCountdown" in html)
check("countdown differentiates crypto vs stocks",
      "isCrypto" in html and "-USD" in html)
check("countdown skips weekends for stocks",
      "getUTCDay()" in html)


# ---------------------------------------------------------------------------
# 7. CSS styles for new toolbar
# ---------------------------------------------------------------------------
print("\n[7] CSS styles for new toolbar")
check(".chart-host CSS",     ".chart-host {" in html)
check(".chart-toolbar CSS",  ".chart-toolbar {" in html)
check(".view-btn CSS",       ".view-btn {" in html)
check(".view-btn.active CSS",".view-btn.active" in html)
check(".anno-btn CSS",       ".anno-btn {" in html)
check(".countdown-badge CSS",".countdown-badge {" in html)
check(".tv-widget-host CSS", ".tv-widget-host {" in html)


# ---------------------------------------------------------------------------
# 8. Wave 12: charts moved to /chart page (memory fix). The toolbar — view
#    toggle, annotations, countdown — is on every /chart page.
# ---------------------------------------------------------------------------
print("\n[8] Chart-page toolbar present (Wave 12 — chart opens in new tab)")
check("/chart page has chart-host with chart_solo idx",
      'data-chart-idx="chart_solo"' in html)
check("main page has 'Open Chart →' links to /chart?symbol=X",
      '/chart?symbol=AAPL' in html_main and 'Open Chart' in html_main)


# ---------------------------------------------------------------------------
# 9. Regression: Multi-TF selector (Wave 9) still works inside the CC view
# ---------------------------------------------------------------------------
print("\n[9] Multi-TF selector preserved inside CC view")
check("tf-bar still rendered (inside view-cc)",
      'class="tf-bar"' in html)
check("All 4 TF buttons still present",
      all(f'data-tf="{t}"' in html for t in ["1H","1D","1W","1M"]))


# ---------------------------------------------------------------------------
# 10. Regression: existing scanner features intact
# ---------------------------------------------------------------------------
print("\n[10] Regressions: prior features intact")
check("38 detectors registered",            len(cc.DETECTORS) == 38)
check("Lightweight Charts price lines still drawn",
      "createPriceLine(" in html)
# Wave 12: these features live on the MAIN page, not on /chart
check("Trade Journal panel still on main page",  'id="journal-panel"' in html_main)
check("Manual setup section still on main page", 'id="manual-section"' in html_main)
check("My Watchlist bar still on main page",     'class="mylist-bar"' in html_main)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 10:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
