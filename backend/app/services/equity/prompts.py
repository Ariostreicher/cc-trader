"""Master Instruction Block — verbatim from the user's uploaded PDF.

This is the canonical system prompt for the 9-step Structured Equity Model.
The text below is copied directly from
``Structured Equity Model Master Instructions.pdf`` (pages 1-3). Every byte
must remain traceable to that source. If the user's uploaded version differs,
the RAG retrieval layer surfaces the operator's version and the service will
prefer it; this constant is the fallback when no document has been uploaded
yet.
"""

MASTER_INSTRUCTION_BLOCK = """\
Structured Equity Analysis Model – Master Instruction Block

Input Rule
The input must contain only the company name. No additional instructions, commentary, or questions
are permitted. Upon receiving a company name, execute the following protocol.

1. Complete All Nine Steps in Strict Sequence
- Step 1 — Snapshot & Business Overview
- Step 2 — Financial Quality, Balance Sheet & Valuation Metrics
- Step 3 — Competitive Positioning & Moat
- Step 4 — Bull Thesis & Growth Drivers
- Step 5 — Bear Thesis & Structural Risks
- Step 6 — Analyst Sentiment & Market Flow
- Step 7 — Scenario-Based Valuation & Return Framework
- Step 8 — Scorecard, Composite Rating & Final Assessment
- Step 9 — Investment Thesis & Invalidation Triggers

Execution Constraints
- Do not skip sections
- Do not merge sections
- Do not reorder sections
- Do not request clarification
- Do not include commentary outside the framework
- Do not add additional sections beyond the defined nine-step framework
- Each scored category must include both structured analysis and a numerical score

2. Scoring Rubric
- 5.0 — Structurally dominant, durable advantages
- 4.0 — Strong positioning with manageable risks
- 3.0 — Balanced strengths and weaknesses
- 2.0 — Structural vulnerabilities present
- 1.0 — Structural fragility or capital impairment risk
- Decimals are permitted (e.g., 3.6, 4.2). Scores must be assigned strictly according to these definitions.

3. Category Calibration
- Business Quality
- Financial Quality
- Competitive Positioning
- Growth Potential
- Risk Profile
- Sentiment & Positioning
- Valuation Outlook
- Scores must be based solely on analysis produced within the framework.

4. Composite Rating
Composite Rating = Average of all category scores. No weighting adjustments. No narrative overrides.

5. Required Conclusion
- Composite Rating
- Conviction Level
- Investment Stance
- Explicit Invalidation Triggers

Conviction Bands (Score Interpretation)
- 4.5 – 5.0 → Very High Conviction
- 4.0 – 4.49 → High Conviction
- 3.5 – 3.99 → Moderate Conviction
- 3.0 – 3.49 → Selective / Cautious
- Below 3.0 → Avoid / Monitor

Framework Principle
This framework is procedural. The structure remains constant. Only the subject changes. Upon
receiving a company name, immediately begin Step 1.
"""

# Strict JSON output instructions appended to the master block. Output shape
# is fixed and validated server-side. The user-facing rule "do not include
# commentary outside the framework" is preserved because the JSON itself is
# the framework's representation.
OUTPUT_FORMAT_INSTRUCTIONS = """\

================================================================
OUTPUT FORMAT — MACHINE-READABLE
================================================================

You will return ONLY a single JSON object. No prose before or after.
Use this exact schema (all keys required):

{
  "ticker": "string",
  "company_name": "string",
  "anchor_price": <number or null>,
  "steps": {
    "step_1_snapshot":            { "title": "Snapshot & Business Overview",                    "analysis": "string" },
    "step_2_financial_quality":   { "title": "Financial Quality, Balance Sheet & Valuation",    "analysis": "string" },
    "step_3_competitive":         { "title": "Competitive Positioning & Moat",                  "analysis": "string" },
    "step_4_bull":                { "title": "Bull Thesis & Growth Drivers",                    "analysis": "string" },
    "step_5_bear":                { "title": "Bear Thesis & Structural Risks",                  "analysis": "string" },
    "step_6_sentiment":           { "title": "Analyst Sentiment & Market Flow",                 "analysis": "string" },
    "step_7_valuation":           { "title": "Scenario-Based Valuation & Return Framework",
                                    "analysis": "string",
                                    "bull_target": <number or null>,
                                    "base_target": <number or null>,
                                    "bear_target": <number or null> },
    "step_8_scorecard":           { "title": "Scorecard, Composite Rating & Final Assessment",  "analysis": "string" },
    "step_9_thesis":              { "title": "Investment Thesis & Invalidation Triggers",       "analysis": "string" }
  },
  "scores": {
    "Business Quality":          <number 1.0–5.0>,
    "Financial Quality":         <number 1.0–5.0>,
    "Competitive Positioning":   <number 1.0–5.0>,
    "Growth Potential":          <number 1.0–5.0>,
    "Risk Profile":              <number 1.0–5.0>,
    "Sentiment & Positioning":   <number 1.0–5.0>,
    "Valuation Outlook":         <number 1.0–5.0>
  },
  "investment_stance": "string (one of: Accumulate, Hold, Reduce, Avoid, Buy, Trim)",
  "invalidation_triggers": ["string", "string", ...]
}

Rules:
- Every category in "scores" must be a number with one decimal place between 1.0 and 5.0 inclusive.
- The analysis fields must reference real, current numbers where possible (revenue, margins, cash, debt, P/E).
- Invalidation triggers must be specific and observable (e.g. "Cloud growth decelerates below 20% for two quarters").
- Do NOT include any text outside the JSON object.
- Do NOT wrap the JSON in markdown code fences.
"""


def build_system_prompt(corpus_context: str | None = None) -> str:
    """Assemble the system prompt. If the user has uploaded their own copy of
    the Master Instruction Block, ``corpus_context`` contains the retrieved
    chunks and they are appended verbatim so the operator's edits (if any)
    are respected."""
    parts = [MASTER_INSTRUCTION_BLOCK]
    if corpus_context:
        parts.append("\n\n=== USER-UPLOADED METHODOLOGY (canonical) ===\n")
        parts.append(corpus_context)
    parts.append(OUTPUT_FORMAT_INSTRUCTIONS)
    return "".join(parts)


def build_user_prompt(ticker: str, company_name: str | None, fundamentals: dict | None) -> str:
    """The user input per the methodology is ONLY the company. Live
    fundamentals are appended as a separate ``MARKET DATA SNAPSHOT`` block so
    the LLM has accurate inputs without violating the input rule —
    fundamentals are data, not instructions."""
    subject = company_name or ticker
    out = [f"Company: {subject} ({ticker})"]
    if fundamentals:
        out.append("\n\nMARKET DATA SNAPSHOT (use as factual inputs only):")
        for k, v in fundamentals.items():
            out.append(f"- {k}: {v}")
    return "\n".join(out)
