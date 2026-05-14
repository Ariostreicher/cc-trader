"""E2E for Wave 8 — all the remaining CC concepts:
  • Full candlestick classifier (15+ patterns)
  • Bull/bear confirmation helpers
  • Keltner Channel + OBV helpers
  • BB Squeeze + Gap + Climax detectors
  • Camarilla pivots
  • Classic chart patterns: Double Top/Bottom, H&S, Triangle, Wedge, Flag, Cup&Handle
  • Harmonics: ABCD, Gartley, Bat, Butterfly, Crab, Cypher, Shark
  • Wolfe Wave
  • SMC extensions: Breaker block, Premium/Discount/OTE

After Wave 8, the codebase has 38 active detectors covering virtually every
named pattern in the 499 pages of CC material.
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
    return pd.DataFrame(rows, columns=["open","high","low","close","volume"])


# ---------------------------------------------------------------------------
# 1. Detector count check — Wave 8 brought us to 38
# ---------------------------------------------------------------------------
print("\n[1] Total detector count after Wave 8")
check("DETECTORS has ≥ 38 entries",
      len(cc.DETECTORS) >= 38, f"got {len(cc.DETECTORS)}")
check("BACKTESTED_CONVICTION has prior for every detector",
      len(cc.BACKTESTED_CONVICTION) >= 38)


# ---------------------------------------------------------------------------
# 2. Expanded bar_pattern classifier
# ---------------------------------------------------------------------------
print("\n[2] Expanded candlestick classifier")

# Doji variants
check("classifies plain doji",
      cc.bar_pattern(_df([(100,101,99,100,1000)])).endswith("doji"))
check("dragonfly-doji on long lower wick + tiny body",
      cc.bar_pattern(_df([(100,100.1,95,100.05,1000)])) == "dragonfly-doji")
check("gravestone-doji on long upper wick + tiny body",
      cc.bar_pattern(_df([(100,105,99.9,100.05,1000)])) == "gravestone-doji")

# Marubozu — body is ~all the range
check("marubozu-bull on body ~95% of range",
      cc.bar_pattern(_df([(100, 110.5, 99.5, 110, 1000)])) == "marubozu-bull")
check("marubozu-bear on body ~95% of range (red)",
      cc.bar_pattern(_df([(110, 110.5, 99.5, 100, 1000)])) == "marubozu-bear")

# Three white soldiers — 3 consecutive bullish higher closes
check("three-white-soldiers",
      cc.bar_pattern(_df([(95,100,94,99,1000),(98,103,97,102,1000),(101,106,100,105,1000)]))
      == "three-white-soldiers")
check("three-black-crows",
      cc.bar_pattern(_df([(105,106,100,101,1000),(101,102,97,98,1000),(98,99,94,95,1000)]))
      == "three-black-crows")

# Engulfing detected only when prev has real body
check("engulfing-bull (prev red, current green engulfs)",
      cc.bar_pattern(_df([(105,106,99,100,1000),(99,107,98,106,1000)])) == "engulfing-bull")
check("engulfing-bear (prev green, current red engulfs)",
      cc.bar_pattern(_df([(100,106,99,105,1000),(106,107,98,99,1000)])) == "engulfing-bear")

# Harami
check("harami-bull",
      cc.bar_pattern(_df([(105,106,99,100,1000),(102,103,101,102.5,1000)])) == "harami-bull")
check("harami-bear",
      cc.bar_pattern(_df([(100,106,99,105,1000),(103,103.5,102,102.5,1000)])) == "harami-bear")

# Pin bar — long wick > 60% of range. df rows are [prev, current]; iloc[-1] is current.
check("pin-bar-bull (huge lower wick)",
      cc.bar_pattern(_df([(98,102,97.5,101,1000),    # prev — big bull bar
                          (99,100.2,95,100,1000)]))  # current — hammer/pin-bar
      in ("pin-bar-bull", "hammer"))
# Inverted hammer / shooting star
check("shooting-star (long upper wick, red bar)",
      cc.bar_pattern(_df([(101,105,100.5,100,1000)])) in ("shooting-star", "pin-bar-bear"))

# Outside bar
check("outside bar (range engulfs prev range)",
      cc.bar_pattern(_df([(101,102,100,101,1000),(100,105,99,104,1000)])) == "outside")


# ---------------------------------------------------------------------------
# 3. is_bull_confirmation / is_bear_confirmation helpers
# ---------------------------------------------------------------------------
print("\n[3] Bull/bear confirmation helpers")
for p in ["hammer","engulfing-bull","morning-star","three-white-soldiers",
          "abandoned-baby-bull","kicker-bull","piercing-line","tweezer-bottom",
          "harami-bull","dragonfly-doji","pin-bar-bull","marubozu-bull"]:
    check(f"'{p}' is bull confirmation", cc.is_bull_confirmation(p))
for p in ["inverted-hammer","engulfing-bear","evening-star","three-black-crows",
          "kicker-bear","dark-cloud-cover","tweezer-top","harami-bear",
          "shooting-star","pin-bar-bear","marubozu-bear"]:
    check(f"'{p}' is bear confirmation", cc.is_bear_confirmation(p))
check("'doji' is neither bull nor bear",
      not cc.is_bull_confirmation("doji") and not cc.is_bear_confirmation("doji"))


# ---------------------------------------------------------------------------
# 4. Keltner Channel + OBV helpers
# ---------------------------------------------------------------------------
print("\n[4] Keltner + OBV indicators")
n = 60
prices = np.linspace(100, 110, n) + np.random.normal(0, 0.5, n)
df = pd.DataFrame({"open":prices,"high":prices+1,"low":prices-1,"close":prices,
                   "volume":np.full(n, 1_000_000)})
kc = cc.keltner_channel(df, length=20, mult=1.5)
check("keltner_channel returns DataFrame with mid/upper/lower",
      isinstance(kc, pd.DataFrame) and {"mid","upper","lower"} <= set(kc.columns))
check("Keltner upper > mid > lower",
      kc["upper"].iloc[-1] > kc["mid"].iloc[-1] > kc["lower"].iloc[-1])
obv_s = cc.obv(df)
check("obv returns a Series",  isinstance(obv_s, pd.Series))
check("obv has same length as df", len(obv_s) == len(df))


# ---------------------------------------------------------------------------
# 5. BB Squeeze detector
# ---------------------------------------------------------------------------
print("\n[5] BB Squeeze")
# Build a long compressed-then-expanding series so the squeeze releases on the
# final bar.
prices = list(np.full(40, 100.0) + np.random.normal(0, 0.1, 40))  # tight 40 bars
prices += [101, 103, 105]   # sudden expansion
df_sq = pd.DataFrame({
    "open":prices, "high":[p+0.5 for p in prices],
    "low": [p-0.5 for p in prices], "close":prices,
    "volume": [1_000_000]*40 + [2_500_000, 2_500_000, 3_000_000],
})
s = cc.detect_bb_squeeze("TEST", df_sq)
check("BB squeeze detector runs without crashing",
      s is None or isinstance(s, cc.Setup))


# ---------------------------------------------------------------------------
# 6. Gap detector
# ---------------------------------------------------------------------------
print("\n[6] Gap (breakaway) detector")
gap_rows = [(100, 101, 99, 100, 1_000_000)] * 32       # consolidation (need >=30 bars)
gap_rows.append((105, 108, 104, 107, 2_500_000))        # gap-up + strong close
df_gap = _df(gap_rows)
s = cc.detect_gap_play("TEST", df_gap)
check("Gap detector fires on breakaway gap up after consolidation",
      s is not None and "gap up" in (s.name.lower() if s else ""),
      f"got {s.name if s else 'None'}")


# ---------------------------------------------------------------------------
# 7. Climax bar detector
# ---------------------------------------------------------------------------
print("\n[7] Climax bar (buying/selling exhaustion)")
clx_rows = [(100, 101, 99, 100, 1_000_000)] * 32       # need >=30 bars
# Wide-range up bar (3+ ATR ~3+ points) on 3x volume that closes in lower half
clx_rows.append((100, 110, 99, 102, 3_000_000))         # close in lower half
df_clx = _df(clx_rows)
s = cc.detect_climax_bar("TEST", df_clx)
check("Climax detector fires on huge bar with weak close",
      s is not None,
      f"got {s.name if s else 'None'}")


# ---------------------------------------------------------------------------
# 8. Camarilla pivots
# ---------------------------------------------------------------------------
print("\n[8] Camarilla pivots")
cam = cc.compute_camarilla_pivots(_df([(95,100.0,90.0,98.0,1000),(98,99,97,98,1000)]))
check("camarilla returns h1-h4 + l1-l4",
      all(k in cam for k in ["h1","h2","h3","h4","l1","l2","l3","l4","prev_close"]))
check("h4 > h3 > h2 > h1 > prev_close",
      cam["h4"] > cam["h3"] > cam["h2"] > cam["h1"] > cam["prev_close"])
check("l4 < l3 < l2 < l1 < prev_close",
      cam["l4"] < cam["l3"] < cam["l2"] < cam["l1"] < cam["prev_close"])


# ---------------------------------------------------------------------------
# 9. Classic chart pattern detectors — smoke tests (each runs without crashing)
# ---------------------------------------------------------------------------
print("\n[9] Classic chart pattern detectors run robustly")
syn_rows = [(100, 102, 99, 101, 1_000_000)] * 80
df_syn = _df(syn_rows)
for fn in [cc.detect_double_top, cc.detect_double_bottom,
           cc.detect_head_and_shoulders, cc.detect_triangle,
           cc.detect_wedge, cc.detect_flag, cc.detect_cup_handle]:
    try:
        r = fn("TEST", df_syn)
        ok = r is None or isinstance(r, cc.Setup)
        check(f"{fn.__name__} robust on flat data", ok)
    except Exception as e:
        check(f"{fn.__name__} robust on flat data", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 10. Harmonic pattern detectors — smoke tests
# ---------------------------------------------------------------------------
print("\n[10] Harmonic detectors run robustly")
for fn in [cc.detect_abcd, cc.detect_gartley, cc.detect_bat,
           cc.detect_butterfly, cc.detect_crab, cc.detect_cypher,
           cc.detect_shark, cc.detect_wolfe_wave]:
    try:
        r = fn("TEST", df_syn)
        ok = r is None or isinstance(r, cc.Setup)
        check(f"{fn.__name__} robust on flat data", ok)
    except Exception as e:
        check(f"{fn.__name__} robust on flat data", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 11. SMC extensions — Breaker block, OTE
# ---------------------------------------------------------------------------
print("\n[11] SMC extensions")
for fn in [cc.detect_breaker_block, cc.detect_premium_discount_ote]:
    try:
        r = fn("TEST", df_syn)
        ok = r is None or isinstance(r, cc.Setup)
        check(f"{fn.__name__} robust", ok)
    except Exception as e:
        check(f"{fn.__name__} robust", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 12. find_xabcd_pivots helper
# ---------------------------------------------------------------------------
print("\n[12] find_xabcd_pivots helper")
# Synthetic: zigzag with 5 clear alternating pivots
zz_rows = []
zz_rows += [(100, 100, 100, 100, 1000)] * 5            # low X
zz_rows += [(101, 110, 100.5, 110, 1000)] * 6          # high A (just one peak)
zz_rows += [(110, 110, 100.5, 100.5, 1000)] * 6        # low B
zz_rows += [(102, 115, 102, 115, 1000)] * 6            # high C
zz_rows += [(115, 115, 105, 105, 1000)] * 6            # low D
zz_rows += [(105, 105, 105, 105, 1000)] * 5
df_zz = _df(zz_rows)
pts = cc.find_xabcd_pivots(df_zz, lookback=80, n=3)
check("find_xabcd_pivots returns dict or None",
      pts is None or isinstance(pts, dict))


# ---------------------------------------------------------------------------
# 13. Robustness on empty / tiny df — every new detector
# ---------------------------------------------------------------------------
print("\n[13] All Wave 8 detectors robust to empty/tiny dataframes")
empty = pd.DataFrame(columns=["open","high","low","close","volume"])
tiny = _df([(100,101,99,100,1000)] * 5)
new_dets = [cc.detect_bb_squeeze, cc.detect_gap_play, cc.detect_climax_bar,
            cc.detect_double_top, cc.detect_double_bottom,
            cc.detect_head_and_shoulders, cc.detect_triangle,
            cc.detect_wedge, cc.detect_flag, cc.detect_cup_handle,
            cc.detect_abcd, cc.detect_gartley, cc.detect_bat,
            cc.detect_butterfly, cc.detect_crab, cc.detect_cypher,
            cc.detect_shark, cc.detect_wolfe_wave,
            cc.detect_breaker_block, cc.detect_premium_discount_ote]
for fn in new_dets:
    try:
        r1 = fn("TEST", empty)
        r2 = fn("TEST", tiny)
        ok = (r1 is None or isinstance(r1, cc.Setup)) and (r2 is None or isinstance(r2, cc.Setup))
        check(f"{fn.__name__} OK", ok)
    except Exception as e:
        check(f"{fn.__name__} OK", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 14. All BACKTESTED_CONVICTION priors are 0.30-0.90
# ---------------------------------------------------------------------------
print("\n[14] BACKTESTED_CONVICTION priors all in range")
for k, v in cc.BACKTESTED_CONVICTION.items():
    check(f"  {k} prior in 0.30-0.90", 0.30 <= v <= 0.90, f"got {v}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  E2E Wave 8:  {passed} passed,  {failed} failed,  {len(results)} total")
print("=" * 60)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}  ({detail})")
sys.exit(0 if failed == 0 else 1)
