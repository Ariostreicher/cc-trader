"""E2E for Wave 1 — Fibonacci ladder + VWAP + Pivot Points + Round numbers.

This is the audit-driven expansion: a chart isn't complete with just EMAs
and swing pivots. CC methodology references the full Fib ladder, anchored
VWAP, classic Pivot Points, and round numbers as primary levels.
"""

from __future__ import annotations
import sys
import json as _json
import re
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
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. The FUBO scenario — 52-week high $56, low $8.
# Aaron's exact complaint: the app showed "resistance" at $45 (a swing pivot)
# but never told him that's actually the 0.786 Fib level, and never showed
# the other levels in the ladder.
# ---------------------------------------------------------------------------
print("\n[1] FUBO scenario — full Fib ladder from $56 high to $8 low")

# Build a synthetic FUBO-like df: hit $56 high early, drop to $8 low recently,
# rally back to $20.
n = 252
prices = []
# First 60 bars: climb 30 → 56
for i in range(60):
    prices.append(30 + i * (56 - 30) / 60)
# Bars 60-200: decline 56 → 8 over many months
for i in range(140):
    prices.append(56 + i * (8 - 56) / 140)
# Bars 200-end: bounce to current ~$20
for i in range(n - 200):
    prices.append(8 + i * (20 - 8) / (n - 200))
df_fubo = pd.DataFrame({
    "open":   prices,
    "high":   [p + 0.5 for p in prices],
    "low":    [p - 0.5 for p in prices],
    "close":  prices,
    "volume": [1_000_000] * n,
}, index=pd.date_range("2025-05-01", periods=n, freq="B"))

fib = cc.compute_fib_levels(df_fubo, lookback_bars=250)
check("compute_fib_levels returns a dict",   isinstance(fib, dict) and "retracements" in fib)
check("FUBO high detected ≈ $56",            abs(fib["high"] - 56.5) < 1.0,
      f"got high={fib['high']:.2f}")
check("FUBO low detected ≈ $8",              abs(fib["low"] - 7.5) < 1.0,
      f"got low={fib['low']:.2f}")
# The low (bar 200) came AFTER the high (bar 60) → direction = "down"
check("direction is 'down' (low more recent than high)",
      fib["direction"] == "down", f"got {fib['direction']}")

# Now verify each Fib retracement is mathematically correct.
# For down direction, retracements sit ABOVE the low:
# level = low + pct * (high - low)
expected = {
    "0.236": fib["low"] + 0.236 * (fib["high"] - fib["low"]),
    "0.382": fib["low"] + 0.382 * (fib["high"] - fib["low"]),
    "0.500": fib["low"] + 0.500 * (fib["high"] - fib["low"]),
    "0.618": fib["low"] + 0.618 * (fib["high"] - fib["low"]),
    "0.660": fib["low"] + 0.660 * (fib["high"] - fib["low"]),
    "0.786": fib["low"] + 0.786 * (fib["high"] - fib["low"]),
}
for pct, want in expected.items():
    got = fib["retracements"].get(pct)
    check(f"Fib {pct} ≈ ${want:.2f}", got is not None and abs(got - want) < 0.01,
          f"got ${got:.2f}")

# Show all FUBO fibs (sanity print)
print(f"\n     FUBO Fib retracements (high={fib['high']:.2f}, low={fib['low']:.2f}, down swing):")
for pct, px in fib["retracements"].items():
    print(f"       {pct}  →  ${px:.2f}")


# ---------------------------------------------------------------------------
# 2. Fib extensions
# ---------------------------------------------------------------------------
print("\n[2] Fibonacci extensions")
ext = fib.get("extensions", {})
check("extension 1.272 present", "1.272" in ext)
check("extension 1.414 present", "1.414" in ext)
check("extension 1.618 present", "1.618" in ext)
# For down direction, extensions sit BELOW the low: level = low - (ext-1) * range
want_1618 = fib["low"] - (1.618 - 1.0) * (fib["high"] - fib["low"])
check(f"Fib ext 1.618 ≈ ${want_1618:.2f}",
      abs(ext["1.618"] - want_1618) < 0.01, f"got ${ext['1.618']:.2f}")


# ---------------------------------------------------------------------------
# 3. Pivot Points
# ---------------------------------------------------------------------------
print("\n[3] Classic Pivot Points")
# A simple df where prev day was: H=110, L=90, C=100
df_p = pd.DataFrame({
    "open":   [95,  100],
    "high":   [110, 105],
    "low":    [90,   95],
    "close":  [100, 102],
    "volume": [1_000_000, 1_000_000],
})
piv = cc.compute_pivot_points(df_p)
# PP = (110 + 90 + 100) / 3 = 100
check("PP correct (100)",  abs(piv["pp"] - 100.0) < 0.01)
# R1 = 2*PP - L = 200 - 90 = 110
check("R1 correct (110)",  abs(piv["r1"] - 110.0) < 0.01)
# R2 = PP + range = 100 + 20 = 120
check("R2 correct (120)",  abs(piv["r2"] - 120.0) < 0.01)
# S1 = 2*PP - H = 200 - 110 = 90
check("S1 correct (90)",   abs(piv["s1"] - 90.0)  < 0.01)
# S2 = PP - range = 100 - 20 = 80
check("S2 correct (80)",   abs(piv["s2"] - 80.0)  < 0.01)


# ---------------------------------------------------------------------------
# 4. Anchored VWAP
# ---------------------------------------------------------------------------
print("\n[4] Anchored VWAP")
vwap = cc.compute_anchored_vwap(df_fubo, lookback_bars=250)
check("VWAP is a finite number",
      vwap is not None and 5 < vwap < 60,
      f"got {vwap}")


# ---------------------------------------------------------------------------
# 5. Round numbers
# ---------------------------------------------------------------------------
print("\n[5] Round numbers — step size scales with price")
# FUBO at ~$20 → step $5
rns_fubo = cc.compute_round_numbers(20.0, count=2)
check("FUBO ($20) gives $5 steps: [10, 15, 25, 30]",
      set([10.0, 15.0, 25.0, 30.0]).issubset(set(rns_fubo)),
      f"got {rns_fubo}")
# NVDA at $520 → step $25
rns_nvda = cc.compute_round_numbers(520.0, count=2)
check("NVDA ($520) gives $25 steps",
      any(abs(r - 500.0) < 0.01 for r in rns_nvda)
      and any(abs(r - 550.0) < 0.01 for r in rns_nvda),
      f"got {rns_nvda}")
# Sub-$5 ticker → 0.50 step
rns_micro = cc.compute_round_numbers(2.50, count=2)
check("Micro ($2.50) gives $0.50 steps",
      any(abs(r - 1.5) < 0.01 for r in rns_micro)
      and any(abs(r - 3.0) < 0.01 for r in rns_micro),
      f"got {rns_micro}")


# ---------------------------------------------------------------------------
# 6. Snapshot dataclass carries the new fields
# ---------------------------------------------------------------------------
print("\n[6] Snapshot carries Wave-1 fields")
snap = cc.Snapshot(
    symbol="FUBO", current_price=20.0,
    fib=fib, pivots=piv, vwap_anchored=15.5,
    round_numbers=rns_fubo,
)
check("snap.fib present",            snap.fib is not None and "retracements" in snap.fib)
check("snap.pivots present",         snap.pivots is not None and "pp" in snap.pivots)
check("snap.vwap_anchored present",  snap.vwap_anchored == 15.5)
check("snap.round_numbers present",  len(snap.round_numbers) > 0)


# ---------------------------------------------------------------------------
# 7. Chart price-lines include Fib/Pivot/VWAP/Round
# ---------------------------------------------------------------------------
print("\n[7] Chart price-lines wired through")
# Render snapshot card chart body
chart_data = {"FUBO": {
    "candles":[{"time":"2025-01-01","open":20,"high":20.5,"low":19.5,"close":20}],
    "volume":[{"time":"2025-01-01","value":1_000_000,"color":"#22c55e55"}],
    "ema_55":[],"ema_100":[],"ema_200":[],
}}
body = cc._snap_chart_body(snap, 0, chart_data)
# data-lines attr is JSON-encoded; pull it out and parse
m = re.search(r"data-lines='([^']+)'", body)
assert m, "data-lines attribute not found"
lines = _json.loads(m.group(1))
titles = [l["title"] for l in lines]

check("price-lines include Fib retracement 0.382",
      any("Fib 0.382" in t for t in titles))
check("price-lines include Fib retracement 0.618",
      any("Fib 0.618" in t for t in titles))
check("price-lines include Fib retracement 0.786",
      any("Fib 0.786" in t for t in titles))
check("price-lines include Fib extension 1.618",
      any("Fib ext 1.618" in t for t in titles))
check("price-lines include PP",   any("PP " in t for t in titles))
check("price-lines include R1",   any("R1 " in t for t in titles))
check("price-lines include S1",   any("S1 " in t for t in titles))
check("price-lines include VWAP", any("VWAP" in t for t in titles))
check("price-lines include round numbers ($15, $25)",
      any(t == "$15" for t in titles) and any(t == "$25" for t in titles))

# 0.618 + 0.660 (CC region) should be drawn brighter (lineWidth 2)
cc_region_lines = [l for l in lines if l["title"].startswith(("Fib 0.618", "Fib 0.660"))]
check("CC region Fibs (0.618, 0.660) drawn with lineWidth 2",
      all(l.get("lineWidth") == 2 for l in cc_region_lines)
      and len(cc_region_lines) == 2,
      f"got {[(l['title'], l.get('lineWidth')) for l in cc_region_lines]}")


# ---------------------------------------------------------------------------
# 8. Key Levels panel surfaces nearest Fib above/below price
# ---------------------------------------------------------------------------
print("\n[8] Key Levels panel shows Fib + VWAP + Pivots")
panel = cc._render_key_levels_panel(snap)
check("Key Levels panel mentions a Fib level (support)",
      'Fib 0.' in panel and 'support' in panel)
check("Key Levels panel mentions a Fib level (resist)",
      'Fib 0.' in panel and 'resist' in panel)
check("Key Levels panel shows VWAP",
      'VWAP' in panel)
check("Key Levels panel shows DAILY PP, R1, S1",
      'DAILY PP' in panel and 'DAILY R1' in panel and 'DAILY S1' in panel)


# ---------------------------------------------------------------------------
# 9. Setup-card chart also gets the Fib/VWAP/Pivot/Round overlay
#    Wave 12: charts moved to /chart page (memory fix). The setup chart lives
#    on render_single_chart_html now, which gets the full Wave-1 overlay.
# ---------------------------------------------------------------------------
print("\n[9] Setup-card chart also has Wave-1 overlays (now on /chart page)")
fake_setup = cc.Setup(
    symbol="FUBO", name="Test", direction="long",
    entry=20.0, stop_loss=18.0, targets=[24.0, 28.0],
    current_price=20.0, conviction=0.6, reasoning="x", citation="x",
    context_flags=[],
)
# Build the /chart page for FUBO (where the chart lives in Wave 12 architecture)
html_setup = cc.render_single_chart_html(
    symbol="FUBO", snap=snap,
    chart_data=chart_data["FUBO"],
    setups=[fake_setup],
)
# Find data-lines on the /chart page (id="lwc_chart_solo")
m2 = re.search(r'id="lwc_chart_solo" data-symbol="FUBO" data-lines=\'([^\']+)\'', html_setup)
check("setup-card chart has data-lines (on /chart page)", m2 is not None)
if m2:
    setup_lines = _json.loads(m2.group(1))
    setup_titles = [l["title"] for l in setup_lines]
    check("setup chart shows Entry + Stop + targets",
          any("Entry" in t for t in setup_titles)
          and any("Stop"  in t for t in setup_titles)
          and any("T1"    in t for t in setup_titles))
    check("setup chart ALSO shows Fib levels",
          any(t.startswith("Fib 0.") for t in setup_titles))
    check("setup chart ALSO shows VWAP",
          any("VWAP" in t for t in setup_titles))
    check("setup chart ALSO shows PP",
          any("PP " in t for t in setup_titles))


# ---------------------------------------------------------------------------
# 10. Empty snapshot still renders (no crash)
# ---------------------------------------------------------------------------
print("\n[10] Graceful behavior with missing data")
snap_empty = cc.Snapshot(symbol="EMPTY", current_price=100.0)
panel2 = cc._render_key_levels_panel(snap_empty)
check("Key Levels works with no Fib data",  'Current' in panel2)
check("Key Levels works with no VWAP",      panel2 is not None)
fib_none = cc.compute_fib_levels(pd.DataFrame(), lookback_bars=10)
check("compute_fib_levels returns {} on empty df", fib_none == {})


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 1:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
