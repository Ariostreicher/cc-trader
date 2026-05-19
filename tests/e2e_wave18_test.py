"""E2E for Wave 18 — forward-looking PLAN column + forming watches in
the main scan table.

Verifies:
  • _compute_plan_text(Setup) emits 'Long/Short now @ $X → $Y, stop $Z. R:R + citation'
  • _compute_watch_plan_text(WatchItem) emits 'Wait for $X (concept). Then long/short, distance %, citation'
  • Every plan carries the CC citation (no silent shortcuts)
  • _compute_verdict ranks updated: STRONG TAKE=1, TAKE=2, WATCH=3, MARGINAL=4, AVOID=5
  • Main table header renamed Rationale → Plan + CC citation
  • Forming watches render as rows with verdict 👁 WATCH
  • Verdict legend includes WATCH chip with count
  • Regression — Waves 14-17 still intact
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
# 1. _compute_plan_text — fired setup narrative
# ---------------------------------------------------------------------------
print("\n[1] _compute_plan_text (fired)")
s = cc.Setup(
    symbol="AAPL", name="EMA 55 Pullback (long)", direction="long",
    entry=200.0, stop_loss=195.0, targets=[210.0, 220.0],
    current_price=200.0, conviction=0.78,
    reasoning="Bull alignment + 0.618 retest", citation="First 18.pdf p.67",
    context_flags=[],
)
plan = cc._compute_plan_text(s)
check("plan mentions Long now",     "Long now" in plan)
check("plan shows entry $200.00",   "$200.00" in plan)
check("plan shows target $210.00",  "$210.00" in plan)
check("plan shows R:R (2.0R)",      "2.0R" in plan)
check("plan shows move +5.0%",      "+5.0%" in plan)
check("plan shows stop $195.00",    "$195.00" in plan)
check("plan cites CC citation 'First 18.pdf p.67'",
      "First 18.pdf p.67" in plan)
check("plan uses 📖 book emoji as citation marker", "📖" in plan)

# Short side
sh = cc.Setup(
    symbol="META", name="Resistance rejection", direction="short",
    entry=500.0, stop_loss=510.0, targets=[480.0, 460.0],
    current_price=500.0, conviction=0.72,
    reasoning="Bear pin bar at supply", citation="Second 18.pdf p.18",
    context_flags=[],
)
short_plan = cc._compute_plan_text(sh)
check("short plan says 'Short now'", "Short now" in short_plan)
check("short plan cites 'Second 18.pdf'", "Second 18.pdf" in short_plan)


# ---------------------------------------------------------------------------
# 2. _compute_watch_plan_text — forming watch narrative
# ---------------------------------------------------------------------------
print("\n[2] _compute_watch_plan_text (forming)")
w = cc.WatchItem(
    symbol="LULU", signal="EMA 55 pullback forming (long)",
    direction="long", level=187.5, current_price=200.0,
    distance_pct=-6.25,
    waiting_for="price to pull back to EMA55 $187.50",
    citation="First 18.pdf p.67", bars_estimate=3,
)
wp = cc._compute_watch_plan_text(w)
check("plan says 'Wait for $187.50'",  "Wait for $187.50" in wp)
check("plan names the CC concept",      "EMA 55 pullback" in wp)
check("plan says 'long' direction",     "long" in wp)
check("plan shows signed distance -6.2%",
      "-6.2%" in wp)
check("plan shows days estimate",       "3 days" in wp or "3 day" in wp)
check("plan cites CC PDF page",         "First 18.pdf p.67" in wp)


# ---------------------------------------------------------------------------
# 3. Verdict rank shuffle (rank 3 reserved for WATCH)
# ---------------------------------------------------------------------------
print("\n[3] Verdict ranks")

def _mk_setup(conviction, rr_target):
    """Helper: setup whose R:R == rr_target."""
    return cc.Setup(
        symbol="X", name="x", direction="long",
        entry=100.0, stop_loss=95.0,
        targets=[100.0 + 5.0 * rr_target],
        current_price=100.0, conviction=conviction,
        reasoning="", citation="", context_flags=[],
    )

st_strong = _mk_setup(0.80, 2.5)
st_take   = _mk_setup(0.70, 1.6)
st_marg   = _mk_setup(0.50, 1.0)
st_avoid  = _mk_setup(0.50, 0.5)

check("STRONG TAKE rank == 1",       cc._compute_verdict(st_strong)[2] == 1)
check("TAKE rank == 2",              cc._compute_verdict(st_take)[2]   == 2)
check("MARGINAL rank == 4 (Wave 18: bumped from 3)",
      cc._compute_verdict(st_marg)[2] == 4)
check("AVOID rank == 5 (Wave 18: bumped from 4)",
      cc._compute_verdict(st_avoid)[2] == 5)
check("WATCH rank == 3 (Wave 18: new, between TAKE and MARGINAL)",
      cc._watch_verdict()[2] == 3)
check("WATCH verdict label",         cc._watch_verdict()[0] == "👁 WATCH")


# ---------------------------------------------------------------------------
# 4. Main table HTML — header renamed + WATCH chip + watch rows render
# ---------------------------------------------------------------------------
print("\n[4] Main table renders PLAN column + WATCH rows")

snap = cc.Snapshot(
    symbol="AAPL", current_price=200.0,
    ema_55=195.0, ema_100=185.0, ema_200=170.0, rsi_14=58.0,
    support_levels=[195.0], resistance_levels=[210.0],
    context_flags=[],
)
# Build with one fired setup + two forming watches
fired = cc.Setup(
    symbol="AAPL", name="EMA 55 Pullback (long)", direction="long",
    entry=200.0, stop_loss=195.0, targets=[210.0, 220.0],
    current_price=200.0, conviction=0.78,
    reasoning="Bull alignment", citation="First 18.pdf p.67",
    context_flags=[],
)
watches = [
    cc.WatchItem(symbol="LULU", signal="EMA 55 pullback forming (long)",
                 direction="long", level=187.5, current_price=200.0,
                 distance_pct=-6.25,
                 waiting_for="price to pull back to EMA55 $187.50",
                 citation="First 18.pdf p.67", bars_estimate=3),
    cc.WatchItem(symbol="MSFT", signal="3rd touch pending @ $410.00",
                 direction="long", level=410.0, current_price=400.0,
                 distance_pct=2.5,
                 waiting_for="price to retest $410.00 (2 touches confirmed)",
                 citation="First 18.pdf p.66 — 3rd touch is highest probability",
                 bars_estimate=2),
]
html_main = cc.render_html(
    setups=[fired], scanned=3, duration_s=0.0,
    levels_by_symbol={"AAPL": snap},
    watches=watches,
)

check("table header renamed to 'Plan + CC citation'",
      "Plan + CC citation" in html_main)
check("table header no longer says 'Rationale (CC citation)'",
      "Rationale (CC citation)" not in html_main)
check("WATCH legend chip rendered",
      "👁 WATCH" in html_main)
check("WATCH chip shows count 2",
      "👁 WATCH · 2" in html_main)
check("fired row has 'Long now' plan",
      "Long now" in html_main)
check("LULU forming row renders",
      'data-symbol="LULU"' in html_main and "Wait for $187.50" in html_main)
check("MSFT forming row renders",
      'data-symbol="MSFT"' in html_main and "Wait for $410.00" in html_main)
check("forming rows use row-watch CSS class",
      'class="setup-row row-watch' in html_main)
check("forming rows show verdict 👁 WATCH",
      "👁 WATCH" in html_main and 'data-verdict="watch"' in html_main)
check("forming rows show signed distance %",
      "+2.5%" in html_main and "-6.2%" in html_main)
check("CC citation present in BOTH fired and forming rows",
      html_main.count("First 18.pdf") >= 3)  # fired + 2 watches


# ---------------------------------------------------------------------------
# 5. Forming-rows sorted by absolute distance (closest first)
# ---------------------------------------------------------------------------
print("\n[5] Sort order — closest forming first")
# LULU is 6.25% away, MSFT is 2.5% away.
msft_idx = html_main.find('data-symbol="MSFT"')
lulu_idx = html_main.find('data-symbol="LULU"')
check("MSFT (closer) renders before LULU (farther)",
      msft_idx > 0 and lulu_idx > 0 and msft_idx < lulu_idx)


# ---------------------------------------------------------------------------
# 6. Regression — Waves 14-17 features still intact
# ---------------------------------------------------------------------------
print("\n[6] Regression — earlier waves still work")
check("Wave 15 syncWatchlistToBackend still defined",
      "function syncWatchlistToBackend()" in html_main)
check("Wave 17 search bar uses handleScanSubmit",
      "handleScanSubmit(event)" in html_main)
check("38 detectors still registered",        len(cc.DETECTORS) == 38)

# Wave 16 chart-styles still rendered
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="AAPL", snap=snap, chart_data=chart_data)
check("Wave 16 chart-style-select still rendered",
      'id="chart-style-select"' in html_chart)
check("Wave 14 17-button TF selector still there",
      all(f'data-tf="{t}"' in html_chart for t in
          ["1m","5m","1h","1D","1M","ALL"]))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 18:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
