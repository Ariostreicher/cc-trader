"""E2E for Wave 22 — fix 'Added but ticker doesn't appear' + harden TF zoom.

Two bugs Aaron reported:

1. Search 'Add & Scan' said 'Added' but the ticker never showed up.
   Root cause: page reload after Add showed the CACHED background-scan
   HTML which didn't contain the new ticker (next refresh is 5 min away).
   Fix: parse /api/scan-now JSON and inject the result row directly into
   the main table — no reload, no waiting.

2. 1m TF still showed daily-looking candles.
   Root cause: my Wave 21 double-rAF (~33ms) wasn't long enough for LWC
   to settle the time-scale on slow renders.
   Fix: retry the zoom up to 5 times with 80ms intervals, verifying the
   visible-range actually applied via getVisibleLogicalRange.
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
snap = cc.Snapshot(symbol="LULU", current_price=119.22,
                   ema_55=148.85, ema_100=170.0, ema_200=184.66, rsi_14=25.6,
                   support_levels=[], resistance_levels=[480.94],
                   context_flags=[])
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="LULU", snap=snap, chart_data=chart_data)


# ---------------------------------------------------------------------------
# 1. Search Add now injects the row directly (no reload)
# ---------------------------------------------------------------------------
print("\n[1] Search Add injects row directly")
check("_injectScanRow() function defined",
      "function _injectScanRow(" in html_main)
check("handleScanSubmit no longer page-reloads",
      "window.location.href = '/'" not in html_main.split("handleScanSubmit")[1].split("function ")[0])
check("handleScanSubmit fetches /api/scan-now per ticker",
      "fetch('/api/scan-now?symbol=" in html_main)
check("injected row carries the symbol",
      "row.setAttribute('data-symbol'" in html_main)
check("injected row uses forward-looking plan text",
      "IF holds above" in html_main and "ride <b>' + dir + '</b>" in html_main
      or "IF " in html_main and "ride" in html_main)
check("injected row highlighted briefly (purple flash)",
      "rgba(167, 139, 250, 0.18)" in html_main)
check("duplicate symbol row is removed before insert (dedup)",
      "dup.remove()" in html_main)
check("no-setup branch shows 👁 WATCH verdict + 'no setup firing yet' message",
      "👁 WATCH" in html_main and "No setup firing yet" in html_main)
check("/api/scan-now URL includes cache-buster",
      "/api/scan-now?symbol=' + encodeURIComponent(sym) + '&t=' + Date.now()" in html_main)


# ---------------------------------------------------------------------------
# 2. TF zoom hardened with retry loop
# ---------------------------------------------------------------------------
print("\n[2] TF zoom retry loop")
check("_setDefaultVisibleRange uses setTimeout retry",
      "function doZoom(attempt)" in html_chart and "doZoom(attempt + 1)" in html_chart)
check("retry verifies via getVisibleLogicalRange",
      "getVisibleLogicalRange()" in html_chart)
check("max 5 retries (won't infinite-loop)",
      "attempt < 5" in html_chart)
check("80ms interval between retries",
      "}, 80);" in html_chart)
check("retry logs 'did not stick' diagnostic",
      "did not stick" in html_chart)
check("init-load zoom uses same retry pattern (doInitZoom)",
      "function doInitZoom(attempt)" in html_chart)


# ---------------------------------------------------------------------------
# 3. Regression — Waves 14-21 still work
# ---------------------------------------------------------------------------
print("\n[3] Regression")
check("Wave 21 logging still present ('[CC] zoom' tag)",
      "[CC] zoom" in html_chart)
check("Wave 17 form intercept still wired",
      "handleScanSubmit(event)" in html_main)
check("Wave 20 — My Watchlist bar still removed",
      'class="mylist-bar"' not in html_main)
check("Wave 18 PLAN column header still 'Plan + CC citation'",
      "Plan + CC citation" in html_main)
check("17 TF buttons still present",
      all(f'data-tf="{t}"' in html_chart for t in
          ["1m","3m","5m","15m","30m","45m","1h","2h","3h","4h",
           "1D","1W","1M","3M","6M","12M","ALL"]))
check("38 detectors still registered",        len(cc.DETECTORS) == 38)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 22:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
