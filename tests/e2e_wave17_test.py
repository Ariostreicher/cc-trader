"""E2E for Wave 17 — main search bar adds to persisted watchlist.

The top search-bar 'Scan' button used to fire a temporary ad-hoc scan that
vanished on reload. Wave 17 makes it ADD the searched ticker(s) to the
persisted watchlist + trigger /api/scan-now + reload, so the ticker gets
the same full-CC treatment (38 detectors, Key Levels, Fib, AI) as the
rest of CC_2026 — appearing in the main table on subsequent loads.
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


html_main = cc.render_html(setups=[], scanned=0, duration_s=0.0)


# ---------------------------------------------------------------------------
# 1. Form intercept
# ---------------------------------------------------------------------------
print("\n[1] Search form intercept")
check("form has onsubmit hook",
      'onsubmit="return handleScanSubmit(event);"' in html_main)
check("form still has id 'search-form'",
      'id="search-form"' in html_main)
check("submit button renamed to 'Add & Scan'",
      "Add &amp; Scan" in html_main or "Add & Scan" in html_main)
check("placeholder mentions adding a ticker (Wave 23: 'active list')",
      "Add ticker to" in html_main or "Add to watchlist" in html_main)


# ---------------------------------------------------------------------------
# 2. handleScanSubmit JS — wiring to watchlist + immediate scan
# ---------------------------------------------------------------------------
print("\n[2] handleScanSubmit wiring")
check("handleScanSubmit function defined",
      "function handleScanSubmit(" in html_main)
check("calls preventDefault to stop default GET",
      "ev.preventDefault" in html_main)
check("reads from #search-input",
      "getElementById('search-input')" in html_main)
check("parses comma-separated list (multiple tickers OK)",
      "raw.split(',')" in html_main)
check("dedupes against existing stars",
      "stars.indexOf(sym) < 0" in html_main)
check("persists list via syncWatchlistToBackend()",
      "syncWatchlistToBackend()" in html_main)
check("triggers immediate scan for each new ticker",
      "triggerImmediateScan(" in html_main)
check("shows confirmation toast (Wave 23: 'Added X to <list>' or 'Already in active list')",
      "Added " in html_main and "Already in active list" in html_main)
check("clears input field after submit",
      "input.value = ''" in html_main)
check("Wave 22 — does NOT reload (injects row instead)",
      "_injectScanRow(" in html_main)


# ---------------------------------------------------------------------------
# 3. Existing flows still work
# ---------------------------------------------------------------------------
print("\n[3] Wave 15 + Wave 16 still in place")
check("Wave 15 syncWatchlistToBackend still defined",
      "function syncWatchlistToBackend()" in html_main)
check("Wave 15 triggerImmediateScan still defined",
      "function triggerImmediateScan(" in html_main)
check("Wave 20 addToMyList prompt removed (function is now a no-op)",
      "function addToMyList()" in html_main)
check("/api/scan-now endpoint reference unchanged",
      "/api/scan-now?symbol=" in html_main)
check("/api/watchlist POST endpoint reference unchanged",
      "/api/watchlist" in html_main)
check("Star button still adds to watchlist",
      "function addToMyListBySymbol(" in html_main)

# Wave 16 — chart page still renders 12 styles
snap = cc.Snapshot(symbol="AAPL", current_price=200.0,
                   ema_55=195.0, ema_100=185.0, ema_200=170.0, rsi_14=58.0,
                   support_levels=[195.0], resistance_levels=[210.0],
                   context_flags=[])
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="AAPL", snap=snap, chart_data=chart_data)
check("Wave 16 chart-style dropdown still rendered",
      'id="chart-style-select"' in html_chart)
check("Wave 16 _buildMainSeries still defined",
      "function _buildMainSeries(" in html_chart)

# Backend regressions
check("38 detectors still registered",          len(cc.DETECTORS) == 38)
check("load_persisted_watchlist still callable",
      callable(cc.load_persisted_watchlist))
check("scan_one_full_response still callable",
      callable(cc.scan_one_full_response))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 17:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
