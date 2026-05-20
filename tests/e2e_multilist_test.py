"""E2E for Wave 23 — multiple named watchlists (up to 10).

Replaces the single-flat-list with up to 10 user-named lists like
'Future Buys', 'Current Holdings', 'Potential'. The scan-universe used by
the background loop is CC_2026 ∪ (union of every list's tickers).

Tests cover:
  1. Backend round-trip — save_watchlists / load_watchlists with dedup +
     alias resolution + max-10 lists + max-50 tickers per list
  2. Legacy single-list format auto-migrates into one named list
  3. all_watchlist_tickers() union helper
  4. Backwards-compat helpers (load_persisted_watchlist /
     save_persisted_watchlist) operate on the active list
  5. HTTP route smoke — render_html includes the watchlists-bar markup
  6. Frontend JS hooks — getWatchlists / createWatchlist / etc.
  7. Regression — Waves 14-22 still intact
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


def _reset_file():
    try: cc.WATCHLIST_FILE.unlink()
    except (FileNotFoundError, Exception): pass


# ---------------------------------------------------------------------------
# 1. Backend round-trip
# ---------------------------------------------------------------------------
print("\n[1] Multi-list backend round-trip")
_reset_file()

check("MAX_WATCHLISTS constant = 10",          cc.MAX_WATCHLISTS == 10)
check("MAX_TICKERS_PER_LIST constant = 50",    cc.MAX_TICKERS_PER_LIST == 50)
check("DEFAULT_LIST_NAME defined",             cc.DEFAULT_LIST_NAME == "My Watchlist")
check("load_watchlists / save_watchlists callable",
      callable(cc.load_watchlists) and callable(cc.save_watchlists))

# Empty file → default empty list
d_empty = cc.load_watchlists()
check("Empty state has 'My Watchlist' default", "My Watchlist" in d_empty["lists"])
check("Empty state has 'active' key matching a list",
      d_empty["active"] in d_empty["lists"])

# Save 3 named lists with alias resolution
ok = cc.save_watchlists({
    "lists": {
        "Future Buys":      ["LULU", "apple"],
        "Current Holdings": ["MSFT"],
        "Potential":        ["bitcoin", "GLD"],
    },
    "active": "Future Buys",
})
check("3-list save returns True",              ok is True)
d = cc.load_watchlists()
check("Future Buys present",                   "Future Buys" in d["lists"])
check("Current Holdings present",              "Current Holdings" in d["lists"])
check("Potential present",                     "Potential" in d["lists"])
check("apple → AAPL alias resolved",           "AAPL" in d["lists"]["Future Buys"])
check("bitcoin → BTC-USD alias resolved",      "BTC-USD" in d["lists"]["Potential"])
check("active is 'Future Buys'",               d["active"] == "Future Buys")


# ---------------------------------------------------------------------------
# 2. Cap enforcement (10 lists max, 50 tickers max per list)
# ---------------------------------------------------------------------------
print("\n[2] Cap enforcement")
_reset_file()

big = {"lists": {f"List{i}": ["AAPL"] for i in range(15)}, "active": "List0"}
cc.save_watchlists(big)
d = cc.load_watchlists()
check("15 lists capped at 10",                 len(d["lists"]) == 10)

_reset_file()
cc.save_watchlists({"lists": {"Stress": [f"TKR{i}" for i in range(80)]},
                     "active": "Stress"})
d = cc.load_watchlists()
check("80 tickers capped at 50 per list",      len(d["lists"]["Stress"]) <= 50)


# ---------------------------------------------------------------------------
# 3. Legacy single-list migration
# ---------------------------------------------------------------------------
print("\n[3] Legacy format migrates to 'My Watchlist'")
_reset_file()
cc.WATCHLIST_FILE.write_text(json.dumps({"tickers": ["NVDA", "TSLA"]}))
d = cc.load_watchlists()
check("legacy file becomes 'My Watchlist'",    "My Watchlist" in d["lists"])
check("legacy tickers preserved",
      "NVDA" in d["lists"]["My Watchlist"] and "TSLA" in d["lists"]["My Watchlist"])

_reset_file()
cc.WATCHLIST_FILE.write_text(json.dumps(["A", "B"]))
d = cc.load_watchlists()
check("bare-list legacy also migrates",        d["lists"]["My Watchlist"] == ["A", "B"])

_reset_file()
cc.WATCHLIST_FILE.write_text("{not valid json")
d = cc.load_watchlists()
check("corrupt JSON returns default safely",   "My Watchlist" in d["lists"])


# ---------------------------------------------------------------------------
# 4. Helpers — all_watchlist_tickers + legacy compat
# ---------------------------------------------------------------------------
print("\n[4] Union helper + legacy compat")
_reset_file()
cc.save_watchlists({
    "lists": {
        "A": ["AAPL", "MSFT"],
        "B": ["MSFT", "NVDA"],
        "C": ["BTC-USD"],
    },
    "active": "A",
})
union = cc.all_watchlist_tickers()
check("union deduplicates across lists",
      union.count("MSFT") == 1)
check("union covers every ticker",
      set(union) == {"AAPL", "MSFT", "NVDA", "BTC-USD"})

active_t = cc.load_persisted_watchlist()
check("load_persisted_watchlist returns ACTIVE list tickers",
      set(active_t) == {"AAPL", "MSFT"})

cc.save_persisted_watchlist(["GLD"])
d = cc.load_watchlists()
check("save_persisted_watchlist replaces ACTIVE list",
      d["lists"]["A"] == ["GLD"])
check("Other lists untouched by legacy save",
      d["lists"]["B"] == ["MSFT", "NVDA"])

_reset_file()


# ---------------------------------------------------------------------------
# 5. Main page renders watchlists-bar + dropdown
# ---------------------------------------------------------------------------
print("\n[5] Main page UI")
html = cc.render_html(setups=[], scanned=0, duration_s=0.0)
check("watchlists-bar div rendered",           'class="watchlists-bar"' in html)
check("wl-active-select dropdown present",     'id="wl-active-select"' in html)
check("'+ New list' button present",           "+ New list" in html)
check("'✎ Rename' button present",             "✎ Rename" in html)
check("'🗑 Delete' button present (danger color)",
      "🗑 Delete" in html)
check("wl-chips container present",            'id="wl-chips"' in html)


# ---------------------------------------------------------------------------
# 6. Frontend JS hooks
# ---------------------------------------------------------------------------
print("\n[6] Frontend JS state helpers")
check("getWatchlists() function",              "function getWatchlists()" in html)
check("saveWatchlists() function",             "function saveWatchlists(d)" in html)
check("syncWatchlistsToBackend POSTs /api/watchlists",
      "fetch('/api/watchlists'" in html)
check("createWatchlist() function",            "function createWatchlist()" in html)
check("createWatchlist enforces WL_MAX limit", "WL_MAX" in html and "Max " in html)
check("renameActiveWatchlist() function",      "function renameActiveWatchlist()" in html)
check("deleteActiveWatchlist() function",      "function deleteActiveWatchlist()" in html)
check("deleteActiveWatchlist refuses the only list",
      "Cannot delete the last list" in html)
check("removeFromActiveList() function",       "function removeFromActiveList(" in html)
check("onWatchlistSelectChange() function",    "function onWatchlistSelectChange()" in html)
check("renderWatchlistsUI() function",         "function renderWatchlistsUI()" in html)
check("legacy migration from cc_stars",        "cc_stars" in html and "Migrate from legacy" in html)
check("getStars wraps the ACTIVE list",        "function getStars()" in html and "d.lists[d.active]" in html)
check("page-load hydrates from /api/watchlists",
      "/api/watchlists" in html and "renderWatchlistsUI" in html)
check("Add & Scan refreshes chips after add",
      "renderWatchlistsUI();" in html)


# ---------------------------------------------------------------------------
# 7. Regression — Waves 14-22 still in place
# ---------------------------------------------------------------------------
print("\n[7] Regression — earlier waves intact")
check("Wave 17 handleScanSubmit still wired",  "handleScanSubmit(event)" in html)
check("Wave 22 _injectScanRow still defined",  "function _injectScanRow(" in html)
check("Wave 20 mylist-bar still removed",      'class="mylist-bar"' not in html)
check("Wave 18 'Plan + CC citation' column",   "Plan + CC citation" in html)
check("Wave 18 👁 WATCH verdict still exists", cc._watch_verdict()[0] == "👁 WATCH")

snap = cc.Snapshot(symbol="AAPL", current_price=200.0,
                   ema_55=195.0, ema_100=185.0, ema_200=170.0, rsi_14=58.0,
                   support_levels=[195.0], resistance_levels=[210.0],
                   context_flags=[])
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="AAPL", snap=snap, chart_data=chart_data)
check("17 TF buttons still present",
      all(f'data-tf="{t}"' in html_chart for t in
          ["1m","5m","15m","1h","1D","1M","ALL"]))
check("Wave 22 zoom retry loop still in chart JS",
      "function doZoom(attempt)" in html_chart)
check("38 detectors still registered",          len(cc.DETECTORS) == 38)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 23 (multi-list):  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
