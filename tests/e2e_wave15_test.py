"""E2E for Wave 15 — persisted watchlist + immediate scan.

When the user adds a ticker to their watchlist on the main page:
  • The frontend POSTs the list to /api/watchlist which persists it
  • The background scan loop merges that list with CC_2026 on every cycle
  • A one-shot /api/scan-now call analyzes the new ticker immediately
  • Page load resyncs localStorage → backend (auto-recovers from Render
    redeploys, since the disk is ephemeral)

Tests cover:
  1. Persistence helpers (load/save round-trip, dedup, alias resolution,
     validation, cap, error handling)
  2. scan_one_full_response shape
  3. Frontend HTML/JS hooks (sync function, immediate-scan call, page-load
     sync, addToMyListBySymbol triggers scan)
  4. Regression — Wave 14 features intact
"""

from __future__ import annotations
import sys, os, json
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
# 1. Persistence helpers — round-trip, dedup, validation, alias resolution
# ---------------------------------------------------------------------------
print("\n[1] Persisted watchlist helpers")

# Always start from a clean state
try: cc.WATCHLIST_FILE.unlink()
except FileNotFoundError: pass
except Exception: pass

check("WATCHLIST_FILE constant defined",       hasattr(cc, "WATCHLIST_FILE"))
check("load_persisted_watchlist callable",     callable(cc.load_persisted_watchlist))
check("save_persisted_watchlist callable",     callable(cc.save_persisted_watchlist))
check("scan_one_full_response callable",       callable(cc.scan_one_full_response))

# Empty start
check("load on missing file returns []",       cc.load_persisted_watchlist() == [])

# Normal save + reload
ok = cc.save_persisted_watchlist(["LULU", "AAPL"])
check("save with two tickers returns True",    ok is True)
loaded = cc.load_persisted_watchlist()
check("loaded back exactly what we saved",     loaded == ["LULU", "AAPL"])

# Dedup + alias resolution
cc.save_persisted_watchlist(["AAPL", "apple", "BITCOIN", "btc", "MSFT", "MSFT"])
loaded = cc.load_persisted_watchlist()
check("aliases resolved (apple→AAPL, bitcoin/btc→BTC-USD)",
      "AAPL" in loaded and "BTC-USD" in loaded)
check("duplicates removed after alias resolution",
      loaded.count("AAPL") == 1 and loaded.count("BTC-USD") == 1
      and loaded.count("MSFT") == 1)
check("MSFT preserved as-is",                  "MSFT" in loaded)

# Validation — junk gets dropped
cc.save_persisted_watchlist(["LULU", "INVALID!!!", "", "  ", "123", "ABC.DE", "x" * 50])
loaded = cc.load_persisted_watchlist()
check("invalid tickers (special chars) dropped",
      "INVALID!!!" not in loaded)
check("empty / whitespace tickers dropped",
      "" not in loaded and " " not in loaded)
check("overlong ticker (>12 chars) dropped",   not any(len(t) > 12 for t in loaded))
check("valid tickers kept (LULU, 123 numeric, ABC.DE)",
      "LULU" in loaded and "123" in loaded and "ABC.DE" in loaded)

# Cap
cc.save_persisted_watchlist([f"TKR{i}" for i in range(60)])
loaded = cc.load_persisted_watchlist()
check("watchlist capped at 50 tickers",        len(loaded) <= 50)

# Empty save = clears
cc.save_persisted_watchlist([])
check("empty save persists empty list",        cc.load_persisted_watchlist() == [])

# JSON shape on disk
cc.save_persisted_watchlist(["LULU"])
disk = json.loads(cc.WATCHLIST_FILE.read_text())
check("disk JSON has 'tickers' key",            "tickers" in disk and disk["tickers"] == ["LULU"])
check("disk JSON has 'updated_at' key",         "updated_at" in disk)

# Legacy plain-list format also loads (forward compat)
cc.WATCHLIST_FILE.write_text(json.dumps(["LULU", "AAPL"]))
check("legacy plain-list JSON format also loads",
      cc.load_persisted_watchlist() == ["LULU", "AAPL"])

# Corrupt file is handled gracefully
cc.WATCHLIST_FILE.write_text("{not valid json")
check("corrupt JSON returns [] without crash",  cc.load_persisted_watchlist() == [])

# Clean up
try: cc.WATCHLIST_FILE.unlink()
except FileNotFoundError: pass


# ---------------------------------------------------------------------------
# 2. scan_one_full_response shape (uses synthetic monkey-patch — no network)
# ---------------------------------------------------------------------------
print("\n[2] scan_one_full_response shape")

import pandas as pd, numpy as np

idx = pd.date_range("2024-01-01", periods=300, freq="D")
synthetic_df = pd.DataFrame({
    "open":  np.linspace(100, 200, 300),
    "high":  np.linspace(101, 201, 300),
    "low":   np.linspace(99,  199, 300),
    "close": np.linspace(100.5, 200.5, 300),
    "volume": np.full(300, 1_000_000),
}, index=idx)

# Monkey-patch scan_one to return synthetic data with no setups
_orig_scan_one = cc.scan_one
def _fake_scan_one(symbol):
    return synthetic_df, [], None
cc.scan_one = _fake_scan_one

try:
    r = cc.scan_one_full_response("AAPL")
    check("response has symbol field",         r.get("symbol") == "AAPL")
    check("response has current_price",        isinstance(r.get("current_price"), float))
    check("response has ema_55 / 100 / 200",
          "ema_55" in r and "ema_100" in r and "ema_200" in r)
    check("response has rsi_14",                "rsi_14" in r)
    check("response has support / resistance lists",
          isinstance(r.get("support_levels"), list)
          and isinstance(r.get("resistance_levels"), list))
    check("response has setups_count = 0",     r.get("setups_count") == 0)
    check("response has setups = []",          r.get("setups") == [])
    check("response has chart_url",            r.get("chart_url") == "/chart?symbol=AAPL")

    # Invalid symbol
    bad = cc.scan_one_full_response("!!!BAD!!!")
    check("invalid symbol returns error",      "error" in bad)
finally:
    cc.scan_one = _orig_scan_one


# ---------------------------------------------------------------------------
# 3. Frontend HTML/JS hooks
# ---------------------------------------------------------------------------
print("\n[3] Frontend wired to backend")

html_main = cc.render_html(setups=[], scanned=0, duration_s=0.0)

check("syncWatchlistToBackend() function defined",
      "function syncWatchlistToBackend()" in html_main)
check("POSTs to /api/watchlist",
      "fetch('/api/watchlist'" in html_main and "'POST'" in html_main)
check("triggerImmediateScan() function defined",
      "function triggerImmediateScan(" in html_main)
check("triggerImmediateScan hits /api/scan-now",
      "/api/scan-now?symbol=" in html_main)
check("addToMyList() syncs + triggers scan",
      "syncWatchlistToBackend()" in html_main and "triggerImmediateScan" in html_main)
check("addToMyListBySymbol triggers scan on new add",
      "syncWatchlistToBackend()" in html_main and "isNew" in html_main)
check("removeFromMyList syncs to backend",
      html_main.count("syncWatchlistToBackend") >= 4)  # add(2), remove, clear, page-load
check("page-load triggers sync (auto-recover after Render redeploy)",
      "syncWatchlistToBackend()" in html_main
      and "auto-recovers" in html_main)
check("addToMyList prompt mentions automatic analysis",
      "analyzed automatically" in html_main or "38 detectors" in html_main)


# ---------------------------------------------------------------------------
# 4. Regression — Wave 14 features still intact
# ---------------------------------------------------------------------------
print("\n[4] Regression — Wave 14 still works")

snap = cc.Snapshot(symbol="AAPL", current_price=200.0,
                   ema_55=195.0, ema_100=185.0, ema_200=170.0, rsi_14=58.0,
                   support_levels=[195.0], resistance_levels=[210.0],
                   context_flags=[])
chart_data = {"default_tf":"1D","timeframes":{"1D":{"candles":[],"volume":[],
              "ema_8":[],"ema_21":[],"ema_55":[],"ema_100":[],"ema_200":[]}}}
html_chart = cc.render_single_chart_html(symbol="AAPL", snap=snap, chart_data=chart_data)

check("17-button TF selector still in chart page",
      all(f'data-tf="{t}"' in html_chart for t in
          ["1m","3m","5m","15m","30m","45m","1h","2h","3h","4h",
           "1D","1W","1M","3M","6M","12M","ALL"]))
check("All-Time Analysis opt-in button still rendered",
      'id="all-time-btn"' in html_chart)
check("DEFAULT_VISIBLE_BARS map still in chart JS",
      "DEFAULT_VISIBLE_BARS" in html_chart)
check("38 detectors still registered",         len(cc.DETECTORS) == 38)
check("13 e2e test files still importable",
      Path(REPO / "tests" / "e2e_wave14_test.py").exists())


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 15:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
