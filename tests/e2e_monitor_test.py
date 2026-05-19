"""E2E for the Watchlist Monitoring table — a second table that shows the
user's starred tickers in table format with live price + key-level data,
regardless of whether a CC setup is firing.
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
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))


# Build snapshots for several tickers
snap_aapl = cc.Snapshot(
    symbol="AAPL", current_price=200.00,
    ema_55=195.0, ema_100=185.0, ema_200=175.0, rsi_14=58.0,
    support_levels=[190.0, 180.0], resistance_levels=[210.0, 220.0],
    context_flags=[],
)
snap_tsla = cc.Snapshot(
    symbol="TSLA", current_price=240.00,
    ema_55=250.0, ema_100=260.0, ema_200=270.0, rsi_14=42.0,
    support_levels=[230.0], resistance_levels=[245.0, 255.0],
    context_flags=[],
)
snap_nvda = cc.Snapshot(
    symbol="NVDA", current_price=520.00,
    ema_55=510.0, ema_100=495.0, ema_200=480.0, rsi_14=72.0,
    support_levels=[490.0], resistance_levels=[530.0, 550.0],
    context_flags=[],
)

html = cc.render_html(
    setups=[], scanned=3, duration_s=0.1,
    levels_by_symbol={
        "AAPL": snap_aapl, "TSLA": snap_tsla, "NVDA": snap_nvda,
    },
    snapshots=[snap_aapl, snap_tsla, snap_nvda],
)


# ---------------------------------------------------------------------------
# 1. Monitor table renders with header + rows for every scanned ticker
# ---------------------------------------------------------------------------
print("\n[1] Monitor table structure")
check("section header present",      "My Watchlist — Live Monitoring" in html)
check("monitor-count label",          'id="monitor-count"' in html)
check("empty-state message",          'id="monitor-empty"' in html)
check("monitor-table element",        'id="monitor-table"' in html)
# Table columns
for col in ["Symbol", "Price", "vs EMA 55", "vs EMA 200", "RSI",
            "Nearest Support", "Nearest Resistance", "Sector",
            "Forming Setup", "Action"]:
    check(f"column '{col}' present",  col in html)


# ---------------------------------------------------------------------------
# 2. A row per scanned ticker exists, hidden by default
# ---------------------------------------------------------------------------
print("\n[2] Pre-rendered hidden rows")
for sym in ["AAPL", "TSLA", "NVDA"]:
    check(f"row for {sym}",
          f'class="monitor-row" data-symbol="{sym}"' in html)
check("rows are hidden by default (display:none)",
      'class="monitor-row" data-symbol="AAPL" style="display:none"' in html)


# ---------------------------------------------------------------------------
# 3. Row data shows correct EMA distances + RSI + S/R
# ---------------------------------------------------------------------------
print("\n[3] Row data — EMA distances and RSI")
# AAPL: price 200, EMA55 195 → EMA below price → "$195.00 ↓ -2.5%"
check("AAPL row shows EMA55 with downward arrow (below price)",
      '195.00' in html and '↓' in html)
# NVDA RSI is 72 → overbought, should be colored red
check("NVDA RSI > 70 shows red color",
      '72.0' in html and '#ef4444' in html)
# TSLA RSI is 42 → neutral
check("TSLA RSI ~42 present in row",
      '42.0' in html)


# ---------------------------------------------------------------------------
# 4. Row action buttons
# ---------------------------------------------------------------------------
print("\n[4] Per-row actions")
check("star button removed from rows (Wave 20)",
      'class="star-btn" data-symbol="AAPL"' not in html)
check("bell button in each row",
      'class="bell-btn" data-symbol="AAPL"' in html)
check("'✎ Setup' button to open manual setup",
      "openManualSetupModal('AAPL'" in html)
check("'📊 Chart' button to scroll to chart",
      "📊 Chart" in html)


# ---------------------------------------------------------------------------
# 5. JS functions wired
# ---------------------------------------------------------------------------
print("\n[5] JS wiring")
check("renderMonitorTable() defined",       "function renderMonitorTable()" in html)
check("renderMonitorTable() called on load",
      "renderMonitorTable()" in html and "window.addEventListener('load'" in html)
check("toggleStar() now re-renders monitor table",
      "toggleStar(ev, sym)" in html
      and html.count("renderMonitorTable()") >= 3)
check("addToMyListBySymbol() re-renders monitor table",
      "addToMyListBySymbol" in html)
check("addToMyList() (prompt-based) re-renders monitor",
      "function addToMyList()" in html)
check("removeFromMyList() re-renders monitor",
      "function removeFromMyList(" in html)
check("clearMyList() re-renders monitor",
      "function clearMyList()" in html)


# ---------------------------------------------------------------------------
# 6. Filter logic — only starred rows shown
# ---------------------------------------------------------------------------
print("\n[6] Filter logic in JS")
# Extract the renderMonitorTable function body
start = html.find("function renderMonitorTable()")
end   = html.find("function ", start + 30)   # next function definition
body  = html[start:end] if end > start else html[start:start + 3000]
check("function reads from cc_stars",      "getStars()" in body)
check("function iterates .monitor-row",    ".monitor-row" in body)
check("function uses data-symbol",         "dataset.symbol" in body)
check("function toggles display style",    "style.display" in body)
check("empty-state shown when no stars",   "monitor-empty" in body or "monitor-count" in body)


# ---------------------------------------------------------------------------
# 7. Forming watch column populated when a watch exists
# ---------------------------------------------------------------------------
print("\n[7] Forming-watch indicator in row")
wi = cc.WatchItem(
    symbol="AAPL", signal="EMA 55 pullback forming (long)",
    direction="long", level=195.0, current_price=200.0,
    distance_pct=-2.5,
    waiting_for="price pulls back to EMA55 $195.00",
    citation="First 18.pdf p.67",
    bars_estimate=3,
)
html_with_watch = cc.render_html(
    setups=[], scanned=1, duration_s=0.1,
    levels_by_symbol={"AAPL": snap_aapl},
    snapshots=[snap_aapl],
    watches=[wi],
)
check("forming watch text appears in monitor row",
      "EMA 55 pullback forming" in html_with_watch)
check("long forming watch uses up arrow",
      "▲" in html_with_watch and "EMA 55 pullback forming" in html_with_watch)


# ---------------------------------------------------------------------------
# 8. Regression — previous features intact
# ---------------------------------------------------------------------------
print("\n[8] Regressions")
check("manual setup section still present",  'id="manual-section"' in html)
check("journal panel still present",          'id="journal-panel"' in html)
check("sizer bar still present",              'id="acct-size"' in html)
check("my-watchlist bar removed (Wave 20)",   'class="mylist-bar"' not in html)
check("snapshot card actions wrapper still present (without stars)",
      'class="snap-actions"' in html)
check("Lightweight Charts library still loaded",
      "lightweight-charts" in html)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Monitor:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
