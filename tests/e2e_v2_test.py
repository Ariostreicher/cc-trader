"""E2E test for the new round of features:
  - Custom watchlist UI (My-list bar, add/remove/scan controls)
  - Key Levels panel on every setup card
  - Watching section (forming setups)
  - AI offline placeholder
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
import numpy as np

results: list[tuple[str, bool, str]] = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. Custom watchlist UI elements
# ---------------------------------------------------------------------------
print("\n[1] Custom watchlist UI — Wave 20: REMOVED, unified into Add & Scan")
html = cc.render_html(setups=[], scanned=0, duration_s=0.0)

# Wave 20 — the dedicated 'My Watchlist' bar + 'My list' filter were deleted.
# The 'Add & Scan' search bar handles the watchlist invisibly now (one list).
check("My Watchlist bar removed",        'class="mylist-bar"' not in html)
check("'+ Add ticker' button HTML removed",
      '">+ Add ticker</button>' not in html)
check("'Scan my list now' button removed", '🎯 Scan my list' not in html)
check("'Clear all' button removed",      'clear-my-list' not in html)
check("'⭐ My list' filter removed",     '⭐ My list' not in html)
# JS functions still defined as no-ops for backwards-compat with any
# inline-onclick attribute that might still call them.
check("addToMyList() JS still defined (no-op)",   'function addToMyList()' in html)
check("removeFromMyList() JS still defined",      'function removeFromMyList(' in html)
check("clearMyList() JS still defined",           'function clearMyList()' in html)
# Add & Scan search bar IS the watchlist now.
check("Add & Scan button is the new entry point",
      'Add &amp; Scan' in html or 'Add & Scan' in html)
check("handleScanSubmit handler wired",  "handleScanSubmit(event)" in html)


# ---------------------------------------------------------------------------
# 2. Key Levels panel
# ---------------------------------------------------------------------------
print("\n[2] Key Levels panel rendering")
snap = cc.Snapshot(
    symbol="TSLA", current_price=200.0,
    ema_55=195.0, ema_100=180.0, ema_200=170.0, rsi_14=52.4,
    support_levels=[175.0, 188.0], resistance_levels=[210.0, 225.0],
    context_flags=[],
)
panel = cc._render_key_levels_panel(snap)
check("panel has 'Key Levels' header",   '📐 Key Levels' in panel)
check("shows current price ($200.00)",   '$200.00' in panel)
check("shows EMA 55 / 100 / 200",        '$195.00' in panel and '$180.00' in panel and '$170.00' in panel)
check("shows RSI 14",                    '52.4' in panel)
check("shows support levels",            '$175.00' in panel and '$188.00' in panel)
check("shows resistance levels",         '$210.00' in panel and '$225.00' in panel)
check("shows distance % from current",   '%' in panel and '↑' in panel and '↓' in panel)
# Support is BELOW current → distance should be negative
check("supports show '↓ -X%' (below current)",
      '↓' in panel and '>Support<' in panel)
# Resistance is ABOVE current → distance should be positive
check("resistances show '↑ +X%' (above current)",
      '↑' in panel and '>Resistance<' in panel)


# ---------------------------------------------------------------------------
# 3. Watching section — forming setups
# ---------------------------------------------------------------------------
print("\n[3] Watching section + find_watches detector")

# Build a synthetic uptrend that ends with price 1.5 ATR above EMA55
# (which is the "forming pullback" zone — 1 to 3 ATR away from EMA55).
n = 260
np.random.seed(42)
# A long slow uptrend with bigger range so ATR is meaningful
base = np.linspace(50.0, 100.0, n)
noise = np.random.normal(0, 0.3, n)
close_prices = base + noise
# Make daily range about 1.0 — so ATR ≈ 1.0
highs = close_prices + 0.6
lows  = close_prices - 0.6
# Bend the last bar so it sits 1.5 ATR above the trailing EMA55.
# Computed: EMA55 trails behind the linear trend by ~(55/2)*slope; slope = 50/260 ≈ 0.19/bar
# So at end, EMA55 ≈ 100 - 27.5*0.19 ≈ 94.8. We want px ≈ 96.3 to be 1.5 ATR above.
close_prices[-1] = 96.3
highs[-1] = 97.0
lows[-1] = 95.7
df = pd.DataFrame({
    "open": close_prices,
    "high": highs,
    "low":  lows,
    "close": close_prices,
    "volume": np.full(n, 1_000_000),
})

watches = cc.find_watches("AAPL", df)
check("find_watches returns a list", isinstance(watches, list))
check("found at least 1 forming setup", len(watches) >= 1, f"watches: {[w.signal for w in watches]}")
if watches:
    w = watches[0]
    check("WatchItem has all fields",
          hasattr(w, 'symbol') and hasattr(w, 'signal') and hasattr(w, 'level')
          and hasattr(w, 'distance_pct') and hasattr(w, 'waiting_for')
          and hasattr(w, 'citation') and hasattr(w, 'bars_estimate'))

# Now render an HTML page with this watch item and verify it shows up
levels = {"AAPL": cc.Snapshot(symbol="AAPL", current_price=float(close_prices[-1]),
                              ema_55=98.0, ema_100=80.0, ema_200=60.0, rsi_14=70.0,
                              support_levels=[90.0], resistance_levels=[],
                              context_flags=[])}
html2 = cc.render_html(
    setups=[], scanned=1, duration_s=0.1,
    levels_by_symbol=levels, watches=watches,
)
check("rendered HTML has 'Watching' section",  'Watching — setups forming' in html2)
check("rendered HTML has watching-card",       'class="watching-card"' in html2)
check("rendered HTML names AAPL",              '<b>AAPL</b>' in html2)


# ---------------------------------------------------------------------------
# 4. AI Senior Trader offline placeholder
# ---------------------------------------------------------------------------
print("\n[4] AI offline placeholder")
offline = cc._ai_voice_block("")
check("offline placeholder mentions Groq key", 'Groq key' in offline)
check("offline placeholder names env var",     'OPENAI_API_KEY' in offline)
check("offline placeholder names Render env",  'Render dashboard' in offline)

online = cc._ai_voice_block("Strong setup — take it.")
check("online block contains the AI text",     'Strong setup — take it.' in online)
check("online block has the 'Senior Trader Read' header", '🎯 Senior Trader Read' in online)


# ---------------------------------------------------------------------------
# 5. Key Levels appears on EVERY setup card (not just snapshots)
# ---------------------------------------------------------------------------
print("\n[5] Key Levels on setup cards")

fake_setup = cc.Setup(
    symbol="NVDA", name="EMA 55/100/200 Pullback (long)", direction="long",
    entry=500.0, stop_loss=490.0, targets=[520.0, 540.0],
    current_price=500.0, conviction=0.78,
    reasoning="Bull alignment", citation="First 18.pdf p.67",
    context_flags=[],
)
levels_n = {"NVDA": cc.Snapshot(
    symbol="NVDA", current_price=500.0,
    ema_55=495.0, ema_100=480.0, ema_200=460.0, rsi_14=58.0,
    support_levels=[470.0, 480.0], resistance_levels=[510.0, 525.0],
    context_flags=[],
)}
html3 = cc.render_html(setups=[fake_setup], scanned=1, duration_s=0.1,
                      levels_by_symbol=levels_n)
# Wave 19 — the dedicated per-ticker setup card was removed from the main
# page. Key Levels now appear (a) inside the All-Tickers-Overview snapshot
# section and (b) on the /chart?symbol=X page. The main-page table still
# carries entry/stop/targets in dedicated columns.
check("Main-page table still has entry/stop/targets",
      '$500.00' in html3 and '$490.00' in html3 and '$520.00' in html3)
# Key Levels + Senior Trader placeholder now live at /chart
chart_page = cc.render_single_chart_html(symbol="NVDA",
    snap=levels_n["NVDA"], chart_data={"default_tf":"1D","timeframes":{
        "1D":{"candles":[],"volume":[],"ema_8":[],"ema_21":[],
              "ema_55":[],"ema_100":[],"ema_200":[]}}},
    setups=[fake_setup])
check("Key Levels panel still renders on /chart page", '📐 Key Levels' in chart_page)
check("/chart page shows EMA distance to current", '↓' in chart_page or '↑' in chart_page)
check("Senior Trader offline placeholder on /chart (no API key)",
      'OPENAI_API_KEY' in chart_page)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E v2:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
