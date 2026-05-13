"""End-to-end verification of the 5 fixes Aaron asked for.

Runs against scan_setups.py without hitting the network — every external call
(yfinance, Groq) is replaced by a synthetic stub so the test is hermetic and
deterministic.

Each fix is asserted with print(PASS/FAIL).
"""

from __future__ import annotations
import sys
import io
import json
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Suppress real yfinance imports printing junk
import warnings
warnings.filterwarnings("ignore")

# Import — this is what we're testing.
import scan_setups as cc

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# FIX #1: Support / Resistance classification by position vs current price.
# ---------------------------------------------------------------------------
print("\n[1] S/R classification (RC-style downtrend)")

import pandas as pd
import numpy as np

# Simulate the RC chart: price slides from $5 → $1.73 with swing pivots along
# the way. Old swing-lows ($2.30, $3.70, $4.10) sit ABOVE current $1.73 — they
# should NOT be classified as support; they're resistance.
prices = (
    [5.00, 4.80, 4.60, 4.40, 4.20]          # early plateau
  + [4.10, 4.30, 4.20, 4.10, 4.00]          # pivot near 4.10
  + [3.90, 3.80, 3.70, 3.85, 3.75]          # pivot near 3.70
  + [3.60, 3.40, 3.20, 3.00, 2.80]
  + [2.60, 2.40, 2.30, 2.50, 2.45]          # pivot near 2.30
  + [2.30, 2.20, 2.10, 2.00, 1.90]
  + [1.80, 1.75, 1.73]                       # current
)
df_rc = pd.DataFrame({
    "open":   prices,
    "high":   [p + 0.05 for p in prices],
    "low":    [p - 0.05 for p in prices],
    "close":  prices,
    "volume": [100_000] * len(prices),
})
sr = cc.support_resistance(df_rc, n=3, tol_pct=2.0)
current = df_rc["close"].iloc[-1]
support_all_below   = all(v < current for v in sr["support"])
resistance_all_above = all(v > current for v in sr["resistance"])
check(
    "support levels are all BELOW current price",
    support_all_below,
    f"current={current}, support={sr['support']}",
)
check(
    "resistance levels are all ABOVE current price",
    resistance_all_above,
    f"current={current}, resistance={sr['resistance']}",
)
check(
    "at least one resistance level captured from former swing-lows",
    len(sr["resistance"]) >= 1,
    f"resistance={sr['resistance']}",
)


# ---------------------------------------------------------------------------
# FIX #2: TradingView symbol auto-resolve (no hardcoded NYSE: prefix)
# ---------------------------------------------------------------------------
print("\n[2] TradingView symbol mapping")

check("AAPL stays bare 'AAPL' (TV auto-resolves NASDAQ)",
      cc._tv_symbol("AAPL") == "AAPL", repr(cc._tv_symbol("AAPL")))
check("LULU stays bare 'LULU'",
      cc._tv_symbol("LULU") == "LULU", repr(cc._tv_symbol("LULU")))
check("GLD (ETF on NYSEARCA) stays bare 'GLD'",
      cc._tv_symbol("GLD") == "GLD", repr(cc._tv_symbol("GLD")))
check("BTC-USD → COINBASE:BTCUSD",
      cc._tv_symbol("BTC-USD") == "COINBASE:BTCUSD", repr(cc._tv_symbol("BTC-USD")))
check("DOGE-USD → COINBASE:DOGEUSD",
      cc._tv_symbol("DOGE-USD") == "COINBASE:DOGEUSD", repr(cc._tv_symbol("DOGE-USD")))
check("no hardcoded NYSE: prefix",
      "NYSE:" not in cc._tv_symbol("AAPL") and "NYSE:" not in cc._tv_symbol("GLD"))


# ---------------------------------------------------------------------------
# FIX #3: Ticker aliases — "bitcoin" → BTC-USD, etc.
# ---------------------------------------------------------------------------
print("\n[3] Ticker alias resolver")

check("'bitcoin' → BTC-USD",     cc.resolve_ticker("bitcoin")  == "BTC-USD")
check("'BITCOIN' → BTC-USD",     cc.resolve_ticker("BITCOIN")  == "BTC-USD")
check("'btc' → BTC-USD",         cc.resolve_ticker("btc")      == "BTC-USD")
check("'ethereum' → ETH-USD",    cc.resolve_ticker("ethereum") == "ETH-USD")
check("'dogecoin' → DOGE-USD",   cc.resolve_ticker("dogecoin") == "DOGE-USD")
check("'apple' → AAPL",          cc.resolve_ticker("apple")    == "AAPL")
check("'tesla' → TSLA",          cc.resolve_ticker("tesla")    == "TSLA")
check("'google' → GOOGL",        cc.resolve_ticker("google")   == "GOOGL")
check("'gold' → GLD",            cc.resolve_ticker("gold")     == "GLD")
check("real ticker passes through ('AAPL' → 'AAPL')",
      cc.resolve_ticker("AAPL") == "AAPL")
check("real ticker uppercased ('aapl' → 'AAPL')",
      cc.resolve_ticker("aapl") == "AAPL")


# ---------------------------------------------------------------------------
# FIX #4: Autocomplete suggestions in HTML
# ---------------------------------------------------------------------------
print("\n[4] Search box autocomplete (datalist)")

# Render an empty page and inspect the HTML
html = cc.render_html(setups=[], scanned=0, duration_s=0.0, snapshots=[])
check("has <datalist id=\"ticker-suggestions\">",
      'id="ticker-suggestions"' in html)
check("input is bound to the datalist via list=...",
      'list="ticker-suggestions"' in html)
for alias in ["BITCOIN", "ETHEREUM", "APPLE", "GOLD", "BTC-USD", "ETH-USD"]:
    check(f"datalist contains '{alias}'",
          f'<option value="{alias}">' in html)
check("placeholder text mentions both common-names AND tickers",
      "bitcoin" in html and "apple" in html and "AAPL" in html)


# ---------------------------------------------------------------------------
# FIX #5: Senior Trader 403 graceful fallback
# ---------------------------------------------------------------------------
print("\n[5] Senior Trader 403 graceful fallback")

# Monkey-patch urlopen to raise 403 like a revoked API key would
import urllib.request

class _FakeResp:
    headers = {}
    msg = "Forbidden"

def _raise_403(*a, **kw):
    raise urllib.error.HTTPError(
        url="https://api.groq.com/v1", code=403, msg="Forbidden",
        hdrs=None, fp=io.BytesIO(b"forbidden"),
    )

orig_urlopen = urllib.request.urlopen
urllib.request.urlopen = _raise_403
try:
    fake_setup = cc.Setup(
        symbol="AAPL", name="Test", direction="long",
        entry=100.0, stop_loss=98.0, targets=[104.0, 108.0],
        current_price=100.0, conviction=0.75,
        reasoning="test", citation="test", context_flags=[],
    )
    out = cc.ai_enhance_setup(fake_setup, api_key="bad_key", model="llama-3.3-70b-versatile")
finally:
    urllib.request.urlopen = orig_urlopen

check("403 produces friendly message (not raw 'HTTP Error 403: Forbidden')",
      "HTTP Error 403" not in out and "offline" in out.lower(),
      f"got: {out[:120]}")
check("message names the fix (env var + console.groq.com)",
      "OPENAI_API_KEY" in out and "groq.com" in out.lower())


# 429 path
def _raise_429(*a, **kw):
    raise urllib.error.HTTPError(
        url="https://api.groq.com/v1", code=429, msg="Too Many Requests",
        hdrs=None, fp=io.BytesIO(b"rate limited"),
    )

urllib.request.urlopen = _raise_429
try:
    out429 = cc.ai_enhance_setup(fake_setup, api_key="x", model="x")
finally:
    urllib.request.urlopen = orig_urlopen

check("429 produces 'quota hit' message",
      "quota" in out429.lower() or "rate" in out429.lower(),
      f"got: {out429[:80]}")


# ---------------------------------------------------------------------------
# FIX #3b: Ad-hoc search uses resolve_ticker (not just upper())
# ---------------------------------------------------------------------------
print("\n[3b] Ad-hoc search wires resolve_ticker into the handler")

src = (REPO / "scan_setups.py").read_text()
# Make sure the handler actually calls resolve_ticker
adhoc_block_start = src.find("Ad-hoc scan: ?symbols=")
adhoc_block = src[adhoc_block_start: adhoc_block_start + 600]
check("server's ad-hoc branch calls resolve_ticker(...)",
      "resolve_ticker(" in adhoc_block,
      f"snippet: {adhoc_block[:200]}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
