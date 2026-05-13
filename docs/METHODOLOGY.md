# Methodology — extracted Chart Champions rules

This document is the **single canonical record** of what the system implements. All trading logic the platform executes must be traceable to a rule below. The rules are extracted verbatim from the user-uploaded PDFs.

> **Critical rule:** the AI must not invent methodology. If a question cannot be answered from a rule below, the system should respond *"not in methodology"* rather than guess.

---

## A. Structured Equity Analysis Model — fully implemented (Phase 1)

### A.1 Input rule
The only valid user input is **a company name (or ticker)**. No commentary, no questions. Upon receiving the input the system immediately begins Step 1.

### A.2 The nine steps (strict order)
1. Snapshot & Business Overview
2. Financial Quality, Balance Sheet & Valuation Metrics
3. Competitive Positioning & Moat
4. Bull Thesis & Growth Drivers
5. Bear Thesis & Structural Risks
6. Analyst Sentiment & Market Flow
7. Scenario-Based Valuation & Return Framework
8. Scorecard, Composite Rating & Final Assessment
9. Investment Thesis & Invalidation Triggers

### A.3 Execution constraints
- Do not skip sections.
- Do not merge sections.
- Do not reorder sections.
- Do not request clarification.
- Do not include commentary outside the framework.
- Do not add sections beyond the defined nine.
- Each scored category must include both structured analysis **and** a numerical score.

### A.4 Scoring rubric (1.0 – 5.0, decimals allowed)
| Score | Definition |
|-------|------------|
| 5.0   | Structurally dominant, durable advantages |
| 4.0   | Strong positioning with manageable risks  |
| 3.0   | Balanced strengths and weaknesses         |
| 2.0   | Structural vulnerabilities present        |
| 1.0   | Structural fragility or capital impairment risk |

### A.5 Scored categories
- Business Quality
- Financial Quality
- Competitive Positioning
- Growth Potential
- Risk Profile
- Sentiment & Positioning
- Valuation Outlook

### A.6 Composite rating
`composite = mean(category_scores)` — **no weighting, no narrative overrides.**

### A.7 Conviction bands
| Composite | Conviction |
|-----------|-----------|
| 4.5 – 5.0   | Very High Conviction |
| 4.0 – 4.49  | High Conviction      |
| 3.5 – 3.99  | Moderate Conviction  |
| 3.0 – 3.49  | Selective / Cautious |
| < 3.0       | Avoid / Monitor      |

### A.8 Required conclusion
- Composite rating
- Conviction level
- Investment stance
- Explicit invalidation triggers

### A.9 2026 Research watchlist (provided by Chart Champions)
Imported from `Equity Model 2026 Research TradingView Import List.txt`. Not a recommendation list — a research deployment list. Categories: Mega Cap Tech, Energy, Industrials, Financials, Communication Services, Consumer Discretionary, Consumer Staples, Healthcare, Real Estate, Materials, Transport, Automotive, Tech Software.

The watchlist is seeded into the database as a public, read-only "Chart Champions 2026 Research" list that every user can clone.

---

## B. Technical analysis rules — scaffolded (Phase 2)

The technical cheatsheets (499 pages across 3 PDFs) cover a deep curriculum. Below is the rule-extraction backlog organised by what is mechanically extractable versus what is discretionary (humans-only).

### B.1 Mechanically extractable → algorithm targets
These map cleanly to deterministic detectors.

| Concept | Source pages | Deterministic rule |
|---|---|---|
| **CC Region** | First18 p.63, Third batch | Fibonacci retracement zone `0.618 – 0.66` |
| **Fibonacci expansion** | Second 18 p.72 | `1.272 – 1.618` from leg |
| **Three Drives** | First 18 p.1–7 | (1) Drive 1 arbitrary; (2) Point A = 0.618–0.66 retrace of D1; (3) Drive 2 = 1.272–1.618 of 1A; (4) Point B = CC retrace of D2; (5) Drive 3 = 1.272–1.618 of 2B. Symmetry check on time between drives. |
| **ABCD** | First 18 p.7–12 | Similar to Three Drives without the third drive |
| **Butterfly harmonic** | Second 18 p.90 | XA leg → AB = 0.786, BC ext = 1.618–2.24, CD = 1.27–1.618 of XA |
| **ORB (Opening Range Breakout)** | First 18 p.31–35 | Mark OR high & low after first 30m of session; entry on break with confirmation |
| **EMA 55/100/200 strategy** | First 18 p.67 | Long when price > EMA55 > EMA100 > EMA200 in alignment + pullback to EMA55 |
| **Market structure (HH/HL/LH/LL)** | First 18 p.37, Third batch p.30 | Swing-pivot detector with `n` left/right bars; classify pivots; trend = HH+HL (up) / LH+LL (down) / mixed (range) |
| **Inside day / value-area-within-prior** | First 18 p.43 | Today's HL within yesterday's HL (or value area within yesterday's VA) |
| **Support / resistance flip** | First 18 p.61 | Broken resistance retested as support (and vice versa) within N bars |
| **3rd touch setup** | Second 18 p.45 | Wait for third touch of a level before entry |
| **45° trendline stops** | First 18 p.97 | Internal trendlines connecting many lows/highs at ~45° |
| **VWAP / Volume profile** | Second 18 (volume), Third batch p.240 | Standard VWAP; volume-by-price histogram with POC / VAH / VAL |
| **Pivot Points (Standard)** | First 18 p.73 | Classic P, R1/R2/R3, S1/S2/S3 |
| **TPO / Market Profile** | Third batch p.150–195 | Letters per 30m, single prints, POC, value area, day types (trending, normal, double-distribution) |

### B.2 Discretionary / pattern-recognition → AI-assist only
These require human judgement; the system **suggests** rather than executes.

- Wickoff phases (Accumulation A–E, Distribution A–E, ST, Spring, UTAD)
- Three-peak / valley discretionary identification
- "Igor Time" sessions and the soft narrative around them
- Open-interest divergence reading
- Hedging decisions
- COT (Change of Trend) candle combination
- DeMark-style "countdown" cancellation & recycling

For these, the platform exposes:
1. A detector that flags candidates (e.g., possible Spring) with a confidence.
2. The relevant cheat-sheet page citation via RAG so the operator can verify on chart.

### B.3 Implementation guardrail
Any technical-signal output rendered to a user **must** carry a `methodology_citation: [{page, doc, snippet}]` array. If retrieval returns nothing, the signal is suppressed.

---

## C. Open ambiguities (flagged for operator decision)

1. **"CC Region" exact bounds** — sources use `0.618 – 0.66` and `.382 – .618` in different cheatsheets. Implementation uses the explicit `0.618 – 0.66` from First 18 p.1 and First 18 p.63; the `.382` reference is treated as the *outer* CC region for failed retracements.
2. **Symmetry tolerance on Three Drives time** — source says "should spend equal time" without a tolerance. Implementation default: `± 25%` window; configurable per user.
3. **ORB session length** — source shows 30m examples; implementation default 30m; configurable.
4. **Swing pivot width `n`** — source shows visual examples without a fixed `n`. Implementation default `n=5` (5 bars on each side); configurable.
5. **EMA strategy timeframe** — source EMAs are 55/100/200 but timeframe is not pinned. Implementation default 1D; configurable per watchlist asset.

Operator should review these defaults before Phase 2 goes live to retail users.
