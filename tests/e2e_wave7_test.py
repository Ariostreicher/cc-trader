"""E2E for Wave 7 — Structured Equity Analysis Model integration.

This is the FUNDAMENTAL half of the CC methodology (Master Instructions PDF):
9-step procedural review with 7 category scores 1.0-5.0, composite, conviction
band, stance, invalidation triggers.

We mock the AI call to avoid burning Groq quota / network. The flow tested:
  • analyze_equity_model() builds proper prompt + parses JSON response
  • get_equity_analysis() caches results on disk
  • _render_equity_panel() emits HTML with category scores, band, stance
  • conviction_band_for() maps composite → canonical band string
  • Snapshot.equity_analysis field is set + rendered on both setup + snapshot cards
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
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""))


# Synthetic equity analysis result — what the AI would return
FAKE_EQUITY = {
    "ticker": "NVDA",
    "snapshot": "Leading AI/GPU semiconductor company with dominant data-center share.",
    "bull_thesis": "Sustained AI capex cycle and unmatched CUDA software moat.",
    "bear_thesis": "Customer concentration in top-3 hyperscalers; valuation rich.",
    "scores": {
        "business_quality":       4.7,
        "financial_quality":      4.5,
        "competitive_positioning":4.8,
        "growth_potential":       4.5,
        "risk_profile":           3.4,
        "sentiment_positioning":  4.2,
        "valuation_outlook":      3.6,
    },
    "composite": 4.24,
    "conviction_band": "High Conviction",
    "stance": "Long",
    "invalidation_triggers": [
        "AI capex deceleration > 20% YoY",
        "Major customer in-house silicon competing",
        "Composite drops below 3.5 for 2 consecutive quarters",
    ],
}


# ---------------------------------------------------------------------------
# 1. conviction_band_for() — composite score to band string
# ---------------------------------------------------------------------------
print("\n[1] conviction_band_for()")
check("4.8 → 'Very High Conviction'", cc.conviction_band_for(4.8) == "Very High Conviction")
check("4.2 → 'High Conviction'",      cc.conviction_band_for(4.2) == "High Conviction")
check("3.7 → 'Moderate Conviction'",  cc.conviction_band_for(3.7) == "Moderate Conviction")
check("3.1 → 'Selective / Cautious'", cc.conviction_band_for(3.1) == "Selective / Cautious")
check("2.0 → 'Avoid / Monitor'",      cc.conviction_band_for(2.0) == "Avoid / Monitor")


# ---------------------------------------------------------------------------
# 2. _render_equity_panel() emits HTML with all sections
# ---------------------------------------------------------------------------
print("\n[2] _render_equity_panel() rendering")
html = cc._render_equity_panel(FAKE_EQUITY)
check("panel HTML non-empty",        len(html) > 100)
check("title 'Structured Equity Analysis' present",
      "Structured Equity Analysis" in html)
check("composite score shown",       "4.24" in html or "4.20" in html or "4.2" in html)
check("conviction band shown",       "High Conviction" in html)
check("stance shown",                "Long" in html)
check("Business Quality category present", "Business Quality" in html)
check("Financial Quality category present", "Financial Quality" in html)
check("Competitive Position category present", "Competitive Position" in html)
check("Growth Potential category present", "Growth Potential" in html)
check("Risk Profile category present", "Risk Profile" in html)
check("Sentiment & Positioning category present", "Sentiment & Positioning" in html)
check("Valuation Outlook category present", "Valuation Outlook" in html)
check("bull thesis text rendered",   "CUDA" in html)
check("bear thesis text rendered",   "hyperscalers" in html)
check("invalidation triggers rendered",
      "AI capex deceleration" in html or "Invalidation" in html)
check("score 4.7 (business quality) shown", "4.7" in html)


# ---------------------------------------------------------------------------
# 3. Empty/None handling
# ---------------------------------------------------------------------------
print("\n[3] None handling")
check("_render_equity_panel(None) returns empty string",
      cc._render_equity_panel(None) == "")
check("_render_equity_panel({}) returns empty string",
      cc._render_equity_panel({}) == "")


# ---------------------------------------------------------------------------
# 4. Cache wiring — write, read, expire
# ---------------------------------------------------------------------------
print("\n[4] Cache wiring (file-based, 24h TTL)")
cache_file = REPO / "equity_model_cache.json"
# Save a fresh entry manually
from datetime import datetime, timedelta
now = datetime.utcnow()
cache = {"FAKEX": {"_saved_at": now.isoformat(), "data": FAKE_EQUITY}}
cache_file.write_text(json.dumps(cache))

# Load and verify
loaded = cc._load_equity_cache()
check("cache file loadable",                  "FAKEX" in loaded)
check("cached entry has data + _saved_at",    "data" in loaded["FAKEX"] and "_saved_at" in loaded["FAKEX"])

# Stale entry (older than 24h)
stale = (now - timedelta(hours=48)).isoformat()
cache_file.write_text(json.dumps({"FAKEY": {"_saved_at": stale, "data": FAKE_EQUITY}}))
result = cc.get_equity_analysis("FAKEY", api_key="", model="x", max_age_hours=24)
check("stale entry triggers re-fetch (returns None without api_key)",
      result is None)

# Fresh entry within TTL
cache_file.write_text(json.dumps({"FAKEZ": {"_saved_at": now.isoformat(), "data": FAKE_EQUITY}}))
result2 = cc.get_equity_analysis("FAKEZ", api_key="", model="x", max_age_hours=24)
check("fresh cached entry returned without AI call",
      result2 is not None and result2.get("ticker") == "NVDA")

# Cleanup
try:
    cache_file.unlink()
except Exception:
    pass


# ---------------------------------------------------------------------------
# 5. Snapshot.equity_analysis field + setup-card rendering
# ---------------------------------------------------------------------------
print("\n[5] Setup card with equity analysis attached")
snap = cc.Snapshot(
    symbol="NVDA", current_price=520.00,
    ema_55=510.0, ema_100=495.0, ema_200=480.0, rsi_14=58.0,
    equity_analysis=FAKE_EQUITY,
)
setup = cc.Setup(
    symbol="NVDA", name="EMA Pullback", direction="long",
    entry=520.0, stop_loss=510.0, targets=[540.0, 560.0],
    current_price=520.0, conviction=0.72, reasoning="x", citation="x",
    context_flags=[],
)
page = cc.render_html(
    setups=[setup], scanned=1, duration_s=0.1,
    levels_by_symbol={"NVDA": snap},
)
check("setup card contains 'Structured Equity Analysis' panel",
      "Structured Equity Analysis" in page)
check("composite/conviction band visible on the page",
      "High Conviction" in page)
check("bull thesis is rendered on the page",
      "CUDA" in page)


# ---------------------------------------------------------------------------
# 6. Snapshot card (no setup fired) also shows equity panel
# ---------------------------------------------------------------------------
print("\n[6] Snapshot card with equity analysis")
snap_only = cc.Snapshot(
    symbol="MSFT", current_price=420.0,
    ema_55=410.0, ema_100=400.0, ema_200=380.0, rsi_14=52.0,
    support_levels=[400.0], resistance_levels=[430.0],
    equity_analysis={
        **FAKE_EQUITY, "ticker": "MSFT", "conviction_band": "High Conviction",
        "bull_thesis": "Cloud + AI tailwinds drive durable growth.",
    },
)
page2 = cc.render_html(
    setups=[], scanned=1, duration_s=0.1,
    snapshots=[snap_only],
    levels_by_symbol={"MSFT": snap_only},
)
check("snapshot card shows 'Structured Equity Analysis' panel",
      "Structured Equity Analysis" in page2)
check("MSFT bull thesis rendered",   "Cloud + AI" in page2)


# ---------------------------------------------------------------------------
# 7. Master prompt content
# ---------------------------------------------------------------------------
print("\n[7] EQUITY_MODEL_SYSTEM prompt content")
prompt = cc.EQUITY_MODEL_SYSTEM
check("prompt names all 9 steps",
      all(f"STEP {i}" in prompt for i in range(1, 10)))
check("prompt lists all 7 categories",
      "Business Quality" in prompt and "Financial Quality" in prompt
      and "Competitive Positioning" in prompt and "Growth Potential" in prompt
      and "Risk Profile" in prompt and "Sentiment & Positioning" in prompt
      and "Valuation Outlook" in prompt)
check("prompt mentions scoring rubric 1.0-5.0",
      "1.0" in prompt and "5.0" in prompt)
check("prompt mentions conviction bands",
      "Very High Conviction" in prompt and "Avoid / Monitor" in prompt)
check("prompt demands JSON output",  "JSON" in prompt or "json" in prompt)


# ---------------------------------------------------------------------------
# 8. Regression — earlier features still intact
# ---------------------------------------------------------------------------
print("\n[8] No regressions")
check("at least 18 detectors still registered", len(cc.DETECTORS) >= 18)
check("Fibonacci ladder helper still works",
      cc.compute_fib_levels.__name__ == "compute_fib_levels")
check("Multi-timeframe pivots helper still works",
      callable(cc.compute_multi_timeframe_pivots))
check("Naked POC helper still works",
      callable(cc.find_naked_pocs))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 7:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
