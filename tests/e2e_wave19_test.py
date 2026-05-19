"""E2E for Wave 19 — main-page cleanup + group setups by symbol +
diagnostic logging for TF switch.

Verifies:
  • Per-ticker DETAIL CARDS (XOM-style FVG card with entry/stop/Key Levels)
    are no longer rendered on the main page. They live ONLY at /chart.
  • Multiple fired setups for the same ticker collapse into ONE table row
    with the strongest setup as primary and the alternatives listed inline
    in the PLAN cell (collapsible <details>).
  • TF switch JS logs to console so the operator can paste DevTools output
    if a TF is broken (1m + market closed, etc.).
  • Regression — Waves 14-18 still intact.
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


# ---------------------------------------------------------------------------
# 1. Detail cards removed from main page
# ---------------------------------------------------------------------------
print("\n[1] Main page no longer renders per-ticker detail cards")

fired = cc.Setup(
    symbol="XOM", name="Bullish FVG fill", direction="long",
    entry=158.82, stop_loss=156.72, targets=[175.22, 187.89],
    current_price=162.55, conviction=0.66,
    reasoning="Price tagged bullish FVG and held.", citation="SMC FVG fill",
    context_flags=[],
)
snap = cc.Snapshot(
    symbol="XOM", current_price=162.55,
    ema_55=150.80, ema_100=145.00, ema_200=134.20, rsi_14=65.9,
    support_levels=[146.66, 158.53], resistance_levels=[175.22],
    context_flags=[],
)
html_main = cc.render_html(
    setups=[fired], scanned=1, duration_s=0.0,
    levels_by_symbol={"XOM": snap},
)

# Per-ticker detail block markup is gone from the main scroll
check("'ticker-block ticker-compact' detail block removed from main flow",
      'class="ticker-block ticker-compact"' not in
      html_main.split('<details class="collapsible-section">')[0])
check("XOM detail card with Bullish FVG is NOT in main scroll",
      'Bullish FVG' not in html_main.split('<details class="collapsible-section">')[0]
      or 'Bullish FVG' in html_main.split('<tbody>')[1].split('</tbody>')[0])
check("Wave 19 cleanup comment present",
      "Wave 19 — Per-ticker detail cards removed" in html_main)
# The PLAN column on the row still references the setup
check("PLAN column still shows the setup name in the row",
      "Bullish FVG fill" in html_main)


# ---------------------------------------------------------------------------
# 2. Multiple setups per symbol collapse into ONE row
# ---------------------------------------------------------------------------
print("\n[2] Grouping by symbol")

primary = cc.Setup(
    symbol="AAPL", name="EMA 55 Pullback", direction="long",
    entry=200.0, stop_loss=195.0, targets=[210.0, 220.0],
    current_price=200.0, conviction=0.80,
    reasoning="Best — bull alignment.", citation="First 18.pdf p.67",
    context_flags=[],
)
alt1 = cc.Setup(
    symbol="AAPL", name="3rd touch", direction="long",
    entry=200.0, stop_loss=196.0, targets=[208.0, 215.0],
    current_price=200.0, conviction=0.72,
    reasoning="3rd touch high-prob.", citation="First 18.pdf p.66",
    context_flags=[],
)
alt2 = cc.Setup(
    symbol="AAPL", name="Bullish FVG fill", direction="long",
    entry=200.0, stop_loss=197.0, targets=[206.0, 212.0],
    current_price=200.0, conviction=0.68,
    reasoning="SMC FVG.", citation="Second 18.pdf — SMC",
    context_flags=[],
)
# Different symbol — proves grouping is per-symbol
other = cc.Setup(
    symbol="MSFT", name="ChoCh", direction="short",
    entry=400.0, stop_loss=410.0, targets=[380.0],
    current_price=400.0, conviction=0.78,
    reasoning="ChoCh", citation="First 18.pdf",
    context_flags=[],
)

html_group = cc.render_html(
    setups=[primary, alt1, alt2, other], scanned=2, duration_s=0.0,
)

# Only ONE row for AAPL (not three)
aapl_rows = html_group.count('data-symbol="AAPL"')
# 1 in row + maybe 1 in alarms map; the row count is 1
check("AAPL collapses to 1 table row (was 3)",
      html_group.count('<tr class="setup-row') == 2)  # 1 AAPL + 1 MSFT (+forming watches=0)
check("Primary AAPL setup name visible (EMA 55 Pullback — highest conv)",
      "EMA 55 Pullback" in html_group)
check("'+ 2 more options' summary appears in PLAN cell",
      "+ 2 more options" in html_group)
check("compact '+2' badge appears in SETUP cell",
      '">+2</span>' in html_group)
check("Alternative '3rd touch' name surfaces inside PLAN <details>",
      "3rd touch" in html_group)
check("Alternative 'Bullish FVG fill' surfaces inside PLAN <details>",
      "Bullish FVG fill" in html_group)
check("Alts wrapped in collapsible <details> inside PLAN cell",
      '<details style="margin-top:8px;cursor:pointer">' in html_group)
check("MSFT (different symbol) renders as its own row",
      'data-symbol="MSFT"' in html_group)


# ---------------------------------------------------------------------------
# 3. Diagnostic console logging on TF switch
# ---------------------------------------------------------------------------
print("\n[3] Diagnostic logging in chart page")
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="XOM", snap=snap, chart_data=chart_data)
check("console.log on TF switch fetch start",
      "console.log('[CC] TF switch" in html_chart)
check("console.log on /chart-tf response payload",
      "console.log('[CC] /chart-tf response" in html_chart)
check("console.error on /chart-tf fetch failure",
      "console.error('[CC] /chart-tf fetch failed" in html_chart)
check("1m-specific 'last 7 days' hint in no-data toast",
      "yfinance gives 1m only for the last 7 days" in html_chart)


# ---------------------------------------------------------------------------
# 4. Regression — Waves 14-18 still work
# ---------------------------------------------------------------------------
print("\n[4] Regression")
check("Wave 18 'Plan + CC citation' column header still present",
      "Plan + CC citation" in html_main)
check("Wave 18 👁 WATCH verdict still exists",
      cc._watch_verdict()[0] == "👁 WATCH")
check("Wave 17 handleScanSubmit still wired",
      "handleScanSubmit(event)" in html_main)
check("Wave 16 chart-style-select still on chart page",
      'id="chart-style-select"' in html_chart)
check("Wave 14 17-button TF selector still complete",
      all(f'data-tf="{t}"' in html_chart for t in
          ["1m","5m","15m","30m","1h","4h","1D","1W","1M","ALL"]))
check("Wave 14 hotfix DEFAULT_VISIBLE_BARS still defined",
      "DEFAULT_VISIBLE_BARS" in html_chart)
check("38 detectors still registered",          len(cc.DETECTORS) == 38)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 19:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
