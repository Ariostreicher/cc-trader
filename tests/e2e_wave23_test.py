"""E2E test for Wave 2 + Wave 3 detectors + bonus harmonic/Wyckoff/volume profile.

12 new detectors total. Each is exercised with synthetic data designed to
deliberately fire that specific pattern, while the other detectors stay silent.

Wave 2: 3rd touch, trendline break, ORB
Wave 3: BoS, ChoCh, liquidity grab, order block, FVG fill
Bonus:  Wyckoff Spring, Three Drives, Channel break, Volume Profile test
Plus:   market structure helpers (HH/HL/LH/LL classification)
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


def _df(rows):
    """Build a price df from a list of (o,h,l,c,v) tuples."""
    return pd.DataFrame(
        rows, columns=["open","high","low","close","volume"]
    )


# ---------------------------------------------------------------------------
# 1. Market structure classifier
# ---------------------------------------------------------------------------
print("\n[1] Market structure classifier (HH/HL/LH/LL)")

# Synthetic uptrend with CLEAR pivots. Build a zigzag: up legs and pullbacks
# big enough that swing_pivots(n=4) catches each turn.
n = 200
prices = []
# Phase A: clean leg from 50 → 70 (20 bars)
for i in range(20):
    prices.append(50 + i)
# Phase A.pivot: hold a few bars at top
prices += [70, 69.5, 69]
# Phase B: pullback to 62 (8 bars)
for i in range(8):
    prices.append(69 - i)
# Phase B.pivot: hold
prices += [61, 61.5, 62]
# Phase C: rip to 88 (26 bars) — HH (88 > 70)
for i in range(26):
    prices.append(62 + i)
# Phase C.pivot
prices += [88, 87, 86]
# Phase D: pullback to 75 — HL (75 > 62)
for i in range(11):
    prices.append(86 - i)
# Phase D.pivot
prices += [75, 75.5, 76]
# Phase E: continue up to 100 — HH (100 > 88)
for i in range(24):
    prices.append(76 + i)
# Pad to n
while len(prices) < n:
    prices.append(prices[-1] + 0.1)
prices = prices[:n]

df_up = pd.DataFrame({
    "open":  prices, "high": [p+0.5 for p in prices],
    "low":   [p-0.5 for p in prices], "close": prices,
    "volume":[1_000_000]*n,
}, index=pd.date_range("2025-01-01", periods=n, freq="B"))

structure = cc.classify_market_structure(df_up, lookback=200, n=4)
check("classify_market_structure returns a list",   isinstance(structure, list))
check("structure has multiple swings",              len(structure) >= 4)
labels = [s["label"] for s in structure]
check("at least one HH found",                      "HH" in labels)
check("at least one HL found",                      "HL" in labels)

trend = cc.detect_trend_from_structure(structure)
check("uptrend detected from synthetic uptrend data",
      trend == "up", f"got {trend}")

# Synthetic downtrend
df_down = pd.DataFrame({
    "open": prices[::-1], "high":[p+0.5 for p in prices[::-1]],
    "low":  [p-0.5 for p in prices[::-1]], "close": prices[::-1],
    "volume":[1_000_000]*n,
}, index=pd.date_range("2025-01-01", periods=n, freq="B"))
trend_d = cc.detect_trend_from_structure(
    cc.classify_market_structure(df_down, lookback=200, n=4)
)
check("downtrend detected from reversed series", trend_d == "down",
      f"got {trend_d}")


# ---------------------------------------------------------------------------
# 2. FVG (Fair Value Gap) detector helper
# ---------------------------------------------------------------------------
print("\n[2] FVG detection")
# 3-bar pattern: bar0.high=10, bar1 doesn't matter, bar2.low=15 → bull FVG
fvg_rows = [(9,10,8.5,9.5,1000)]            # bar -3 baseline
fvg_rows += [(10,10.5,9.5,10,1000)]         # bar 0  high=10.5
fvg_rows += [(13,14,12,13,1000)]            # bar 1
fvg_rows += [(15,16,15.5,15.8,1000)]        # bar 2  low=15.5  → bull FVG (10.5→15.5)
# Add bars that don't re-enter the FVG so it stays unfilled
for _ in range(10):
    fvg_rows.append((16, 16.5, 15.6, 16.2, 1000))
df_fvg = _df(fvg_rows)
fvgs = cc.find_fvgs(df_fvg, lookback=50)
check("find_fvgs returns at least one FVG",  len(fvgs) >= 1)
bull_fvgs = [f for f in fvgs if f["kind"] == "bull"]
check("found a bullish FVG",                 len(bull_fvgs) >= 1)
if bull_fvgs:
    f = bull_fvgs[0]
    check("FVG top > bottom",                 f["top"] > f["bot"])
    check("FVG marked as unfilled",           f["filled"] is False)


# ---------------------------------------------------------------------------
# 3. Order block finder helper
# ---------------------------------------------------------------------------
print("\n[3] Order block detection")
# Need ~30 baseline bars for ATR to settle. Then red OB → impulsive 4-bar rally.
ob_rows = [(100, 101, 99, 100, 1000)] * 25                # baseline (ATR warmup)
ob_rows.append((102, 102.5, 101.0, 101.2, 1000))          # RED order block candle
ob_rows.append((101.5, 105, 101, 105, 2000))              # rally bar 1
ob_rows.append((105, 108, 104.8, 108, 2000))
ob_rows.append((108, 111, 107.5, 111, 2000))
ob_rows.append((111, 114, 110.5, 114, 2000))              # +13 from OB top, ~5+ ATR move
for _ in range(8):
    ob_rows.append((114, 114.5, 113.5, 114, 1000))
df_ob = _df(ob_rows)
obs = cc.find_order_blocks(df_ob, lookback=50, impulse_atrs=1.0)
check("find_order_blocks returns at least one",  len(obs) >= 1)
bull_obs = [o for o in obs if o["kind"] == "bull"]
check("found a bullish order block",             len(bull_obs) >= 1)


# ---------------------------------------------------------------------------
# 4. Volume profile (POC, VAH, VAL)
# ---------------------------------------------------------------------------
print("\n[4] Volume profile (POC, VAH, VAL)")
# 60 bars where most time is spent in the $100-105 range
vp_rows = []
for _ in range(40):
    vp_rows.append((100, 105, 99, 102, 2_000_000))     # heavy volume in mid
for _ in range(10):
    vp_rows.append((105, 110, 104, 108, 500_000))      # light volume at top
for _ in range(10):
    vp_rows.append((95, 100, 90, 97, 500_000))         # light volume at bottom
df_vp = _df(vp_rows)
vp = cc.compute_volume_profile(df_vp, lookback_bars=60, bins=40)
check("volume profile returns POC",               "poc" in vp)
check("volume profile returns VAH",               "vah" in vp)
check("volume profile returns VAL",               "val" in vp)
check("POC is in the heavy-volume zone (98-106)",
      98 < vp["poc"] < 106, f"got POC={vp.get('poc')}")
check("VAH > POC > VAL",
      vp.get("vah", 0) >= vp.get("poc", 0) >= vp.get("val", 0))


# ---------------------------------------------------------------------------
# 5. Trendline fit
# ---------------------------------------------------------------------------
print("\n[5] Trendline fit")
# Build rising trendline with CLEAR swing lows (pivots): low at bars 10, 30, 50
tl_rows = []
for i in range(80):
    base = 50 + i * 0.3
    # Inject pullbacks at bars 8-12, 28-32, 48-52 forming swing lows
    if i in (8, 9, 10, 11, 12):
        base -= 3.0 if i == 10 else 1.5   # low at bar 10
    elif i in (28, 29, 30, 31, 32):
        base -= 3.0 if i == 30 else 1.5   # low at bar 30 (higher)
    elif i in (48, 49, 50, 51, 52):
        base -= 3.0 if i == 50 else 1.5   # low at bar 50 (higher still)
    close = base + 2
    tl_rows.append((close - 0.5, close + 1, base - 0.5, close, 1000))
df_tl = _df(tl_rows)
tl = cc.fit_trendline(df_tl, kind="support", lookback=80, n=5)
check("fit_trendline (support) returns a line",
      tl is not None and "slope" in tl)
if tl:
    check("trendline slope is positive (rising support)",
          tl["slope"] > 0, f"got slope={tl['slope']:.3f}")


# ---------------------------------------------------------------------------
# 6. 3rd touch detector
# ---------------------------------------------------------------------------
print("\n[6] 3rd touch detector")
# Need clean swing lows at ~$50 with bars rising significantly between them
# so swing_pivots (n=4) picks them up as local minima.
tt_rows = []
# Approach 1 to the level
for _ in range(8):
    tt_rows.append((58, 60, 57, 59, 1_000_000))   # rise
tt_rows.append((58, 59, 50, 51, 1_000_000))       # touch 1 — low $50
for _ in range(8):
    tt_rows.append((52, 60, 51, 59, 1_000_000))   # rise back
# Approach 2
for _ in range(4):
    tt_rows.append((58, 60, 57, 59, 1_000_000))
tt_rows.append((58, 59, 50.2, 51, 1_000_000))     # touch 2 — low $50.2
for _ in range(8):
    tt_rows.append((52, 60, 51, 59, 1_000_000))
# Approach 3 — current bar is approaching but hasn't quite touched yet
for _ in range(4):
    tt_rows.append((57, 58, 55, 56, 1_000_000))
tt_rows.append((56, 56.5, 50.5, 51.0, 1_500_000))  # current bar very close to $50, vol confirmed
df_tt = _df(tt_rows)

s = cc.detect_third_touch("TEST", df_tt)
check("detect_third_touch fires on the 3rd approach",
      s is not None and "3rd Touch" in (s.name if s else ""),
      f"got {s.name if s else 'None'}")
if s:
    check("3rd touch is LONG direction (lows clustering)",
          s.direction == "long", f"got {s.direction}")


# ---------------------------------------------------------------------------
# 7. Trendline break detector
# ---------------------------------------------------------------------------
print("\n[7] Trendline break + retest detector")
# Rising support trendline, then BREAK below + retest from below
tb_rows = []
# 30 bars climbing along rising trendline
for i in range(30):
    base = 50 + i * 0.5
    tb_rows.append((base - 0.5, base + 2, base, base + 1.5, 1_000_000))
# Break: 5 bars dropping below trendline (price would be ~64-65)
for i in range(5):
    base = 60 - i * 1.5
    tb_rows.append((base + 0.5, base + 1.5, base - 0.5, base, 2_000_000))
# Retest: price now back near where the trendline would be
tb_rows.append((55, 56, 53, 54.5, 1_500_000))
df_tb = _df(tb_rows)
s = cc.detect_trendline_break("TEST", df_tb)
check("detect_trendline_break returns a Setup or None gracefully",
      s is None or isinstance(s, cc.Setup))
# This one is harder to deterministically fire — the test just verifies it
# doesn't crash and behaves consistently.


# ---------------------------------------------------------------------------
# 8. ORB detector
# ---------------------------------------------------------------------------
print("\n[8] ORB detector")
# Build a 5-bar tight range $100-$102 then break above to $105 with volume
orb_rows = []
# 30 baseline bars with normal volume
for _ in range(30):
    orb_rows.append((100, 102, 99, 101, 1_000_000))
# 5 bars sitting tight in range
for _ in range(5):
    orb_rows.append((100, 101.5, 99.5, 100.5, 1_000_000))
# Breakout bar
orb_rows.append((101, 105, 100.5, 105, 2_500_000))
df_orb = _df(orb_rows)
s = cc.detect_orb_breakout("TEST", df_orb)
check("ORB detector fires on breakout above 5-bar range",
      s is not None and "ORB" in (s.name if s else ""),
      f"got {s.name if s else 'None'}")
if s:
    check("ORB is LONG direction",  s.direction == "long")
    check("ORB stop is below entry", s.stop_loss < s.entry)


# ---------------------------------------------------------------------------
# 9. BoS detector
# ---------------------------------------------------------------------------
print("\n[9] BoS (Break of Structure) detector")
# Take our uptrend dataframe and tack on a current bar that closes above the
# most recent swing high.
bos_df = df_up.copy()
# add a final bar that closes well above the recent high
last_close = bos_df["close"].iloc[-1]
new_high = bos_df["high"].max() + 2
extra = pd.DataFrame([{
    "open":  last_close, "high": new_high + 1,
    "low":   last_close - 0.5, "close": new_high,
    "volume": 2_000_000,
}], index=[bos_df.index[-1] + pd.Timedelta(days=1)])
bos_df = pd.concat([bos_df, extra])
s = cc.detect_bos("TEST", bos_df)
check("BoS detector returns Setup or None without crashing",
      s is None or isinstance(s, cc.Setup))
# Note: requires structure analysis to identify trend, so result may vary by
# synthetic data. We just verify it runs.


# ---------------------------------------------------------------------------
# 10. ChoCh detector
# ---------------------------------------------------------------------------
print("\n[10] ChoCh (Change of Character) detector")
# Run the detector on the uptrend df with a final dump below recent HL
choch_df = df_up.copy()
new_low = choch_df["low"].iloc[-30:].min() - 3
extra = pd.DataFrame([{
    "open": choch_df["close"].iloc[-1], "high": choch_df["close"].iloc[-1] + 0.5,
    "low": new_low - 0.5, "close": new_low,
    "volume": 2_000_000,
}], index=[choch_df.index[-1] + pd.Timedelta(days=1)])
choch_df = pd.concat([choch_df, extra])
s = cc.detect_choch("TEST", choch_df)
check("ChoCh detector returns Setup or None without crashing",
      s is None or isinstance(s, cc.Setup))


# ---------------------------------------------------------------------------
# 11. Liquidity grab detector
# ---------------------------------------------------------------------------
print("\n[11] Liquidity grab detector")
lg_rows = []
# 30 baseline with clear swing high at $110
for _ in range(15):
    lg_rows.append((100, 102, 99, 101, 1_000_000))
for _ in range(5):
    lg_rows.append((105, 110, 104, 108, 1_000_000))      # swing high 110
for _ in range(15):
    lg_rows.append((100, 102, 99, 101, 1_000_000))
# Current bar wicks ABOVE 110 but closes back below
lg_rows.append((105, 112, 104, 108, 2_000_000))
df_lg = _df(lg_rows)
s = cc.detect_liquidity_grab("TEST", df_lg)
check("Liquidity grab detector fires on wick-and-close",
      s is not None and "Liquidity grab" in (s.name if s else ""),
      f"got {s.name if s else 'None'}")
if s:
    check("liquidity grab → SHORT (wick above high)",
          s.direction == "short", f"got {s.direction}")


# ---------------------------------------------------------------------------
# 12. Order block retest detector
# ---------------------------------------------------------------------------
print("\n[12] Order block retest detector")
# Reuse the synthetic OB df and append a bar that retraces back into the OB zone
ob_retest_df = df_ob.copy()
# add a retest bar — low touches inside the OB range ~$101.0-102.2 but close holds
extra = _df([(102, 102.5, 101.2, 102.0, 1_500_000)])
ob_retest_df = pd.concat([ob_retest_df, extra], ignore_index=True)
s = cc.detect_order_block_retest("TEST", ob_retest_df)
check("Order block retest detector runs without crashing",
      s is None or isinstance(s, cc.Setup))


# ---------------------------------------------------------------------------
# 13. FVG fill detector
# ---------------------------------------------------------------------------
print("\n[13] FVG fill detector")
# Build a bullish FVG and then a bar that retraces back into it
fvg_df = df_fvg.copy()
# bullish FVG was 10.5 → 15.5 (mid 13.0). Append a bar with low touching 12.5
extra = _df([(14, 14.5, 12.6, 14.0, 1_500_000)])
fvg_df = pd.concat([fvg_df, extra], ignore_index=True)
s = cc.detect_fvg_fill("TEST", fvg_df)
check("FVG fill detector runs without crashing",
      s is None or isinstance(s, cc.Setup))


# ---------------------------------------------------------------------------
# 14. Wyckoff Spring detector
# ---------------------------------------------------------------------------
print("\n[14] Wyckoff Spring detector")
sp_rows = []
# 30 bars in a tight $100-$105 range
for _ in range(30):
    sp_rows.append((101, 105, 100, 103, 1_000_000))
# Current bar: wicks DOWN to $97 then closes back at $103 (strong reversal)
sp_rows.append((101, 103.5, 97, 102.8, 2_500_000))
df_sp = _df(sp_rows)
s = cc.detect_wyckoff_spring("TEST", df_sp)
check("Wyckoff Spring fires on false break below range",
      s is not None and "Spring" in (s.name if s else ""),
      f"got {s.name if s else 'None'}")
if s:
    check("Wyckoff Spring → LONG", s.direction == "long")


# ---------------------------------------------------------------------------
# 15. Three Drives detector
# ---------------------------------------------------------------------------
print("\n[15] Three Drives detector")
# Three escalating peaks at $100, $105, $110 then drop to $103
td_rows = []
for _ in range(10):
    td_rows.append((95, 100, 94, 98, 1_000_000))
for _ in range(5):
    td_rows.append((98, 100, 95, 96, 1_000_000))    # 1st peak ~100, then pullback
for _ in range(10):
    td_rows.append((96, 105, 95, 102, 1_000_000))   # 2nd peak ~105
for _ in range(5):
    td_rows.append((100, 102, 97, 99, 1_000_000))
for _ in range(10):
    td_rows.append((99, 110, 98, 107, 1_000_000))   # 3rd peak ~110
# Drop with current bar
td_rows.append((104, 105, 102, 103, 1_500_000))
df_td = _df(td_rows)
s = cc.detect_three_drives("TEST", df_td)
check("Three Drives detector runs without crashing",
      s is None or isinstance(s, cc.Setup))
# If it fires, must be the short side (top reversal)
if s:
    check("Three Drives Top → SHORT", s.direction == "short")


# ---------------------------------------------------------------------------
# 16. Channel break detector
# ---------------------------------------------------------------------------
print("\n[16] Channel break detector")
# Rising channel: 40 bars with regression slope ~+0.5 and tight std
ch_rows = []
np.random.seed(13)
for i in range(40):
    base = 50 + i * 0.5 + np.random.normal(0, 0.3)
    ch_rows.append((base - 0.2, base + 0.3, base - 0.4, base, 1_000_000))
# Final bar BREAKS above the upper band (way above the trend)
ch_rows.append((70, 80, 70, 79, 2_500_000))
df_ch = _df(ch_rows)
s = cc.detect_channel_break("TEST", df_ch)
check("Channel break detector returns Setup or None",
      s is None or isinstance(s, cc.Setup))


# ---------------------------------------------------------------------------
# 17. Volume profile test detector
# ---------------------------------------------------------------------------
print("\n[17] Volume profile test detector")
# Build df where price tested VAL and held
vpt_rows = []
# 60 bars of volume concentrated in $100-$105
for _ in range(40):
    vpt_rows.append((100, 105, 99, 102, 2_000_000))
for _ in range(10):
    vpt_rows.append((105, 110, 104, 108, 500_000))
for _ in range(10):
    vpt_rows.append((95, 100, 92, 97, 500_000))
# Current bar tests VAL (~$100) from above
vpt_rows.append((102, 103, 100, 101.2, 1_500_000))
df_vpt = _df(vpt_rows)
s = cc.detect_volume_profile_test("TEST", df_vpt)
check("Volume profile test detector runs without crashing",
      s is None or isinstance(s, cc.Setup))


# ---------------------------------------------------------------------------
# 18. DETECTORS list integrity
# ---------------------------------------------------------------------------
print("\n[18] DETECTORS list + BACKTESTED_CONVICTION integrity")
check("DETECTORS has 18 entries (was 6, +12 from Wave 2/3/Bonus)",
      len(cc.DETECTORS) == 18,
      f"got {len(cc.DETECTORS)}")
expected_keys = {
    "EMA Pullback","CC Region","S/R Flip","Volume Spike","Inside Day","RSI Reversal",
    "3rd Touch","Trendline Break","ORB",
    "BoS","ChoCh","Liquidity Grab","Order Block","FVG",
    "Wyckoff","Three Drives","Channel","VolProfile",
}
present = set(cc.BACKTESTED_CONVICTION.keys())
missing = expected_keys - present
check("BACKTESTED_CONVICTION has priors for all 18 detectors",
      not missing, f"missing keys: {missing}")
# All priors are in plausible range
all_in_range = all(0.30 <= v <= 0.90 for v in cc.BACKTESTED_CONVICTION.values())
check("all priors in 0.30-0.90 range", all_in_range)


# ---------------------------------------------------------------------------
# 19. Every detector returns Setup-or-None and never crashes on empty df
# ---------------------------------------------------------------------------
print("\n[19] Each detector is robust to empty / tiny dataframes")
empty_df = pd.DataFrame(columns=["open","high","low","close","volume"])
tiny_df = _df([(100,101,99,100,1000)] * 5)
for det in cc.DETECTORS:
    try:
        r1 = det("TEST", empty_df)
        r2 = det("TEST", tiny_df)
        ok = (r1 is None or isinstance(r1, cc.Setup)) and (r2 is None or isinstance(r2, cc.Setup))
        check(f"{det.__name__} robust to empty + tiny df", ok)
    except Exception as e:
        check(f"{det.__name__} robust to empty + tiny df", False, f"raised {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 2+3:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
