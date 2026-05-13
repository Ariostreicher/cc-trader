"""Parse the LLM JSON output into validated, persistable objects."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .scoring import REQUIRED_CATEGORIES, validate_scores

NINE_STEP_KEYS: tuple[str, ...] = (
    "step_1_snapshot",
    "step_2_financial_quality",
    "step_3_competitive",
    "step_4_bull",
    "step_5_bear",
    "step_6_sentiment",
    "step_7_valuation",
    "step_8_scorecard",
    "step_9_thesis",
)


class ParseError(ValueError):
    pass


@dataclass(slots=True)
class ParsedReport:
    ticker: str
    company_name: str | None
    anchor_price: float | None
    sections: dict[str, dict[str, Any]]
    scores: dict[str, float]
    investment_stance: str | None
    invalidation_triggers: list[str]
    bull_target: float | None
    base_target: float | None
    bear_target: float | None


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _strip_fence(raw: str) -> str:
    m = _JSON_FENCE.search(raw)
    return m.group(1) if m else raw.strip()


def parse(raw: str) -> ParsedReport:
    text = _strip_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Be forgiving: try to slice the first {...} block.
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            try:
                data = json.loads(text[first : last + 1])
            except json.JSONDecodeError as exc2:
                raise ParseError(f"could not parse LLM output as JSON: {exc2}") from exc2
        else:
            raise ParseError(f"no JSON object found in LLM output: {exc}") from exc

    if not isinstance(data, dict):
        raise ParseError("LLM output is not a JSON object")

    steps = data.get("steps")
    if not isinstance(steps, dict):
        raise ParseError("missing 'steps' object")
    missing_steps = [k for k in NINE_STEP_KEYS if k not in steps]
    if missing_steps:
        raise ParseError(f"missing steps: {missing_steps}")

    scores = data.get("scores")
    if not isinstance(scores, dict):
        raise ParseError("missing 'scores' object")
    missing_scores = [c for c in REQUIRED_CATEGORIES if c not in scores]
    if missing_scores:
        raise ParseError(f"missing scores for: {missing_scores}")
    try:
        scores = {c: float(scores[c]) for c in REQUIRED_CATEGORIES}
    except (TypeError, ValueError) as exc:
        raise ParseError(f"non-numeric score: {exc}") from exc
    validate_scores(scores)

    step7 = steps["step_7_valuation"]
    invalidation = data.get("invalidation_triggers") or []
    if not isinstance(invalidation, list):
        raise ParseError("invalidation_triggers must be a list")

    return ParsedReport(
        ticker=str(data.get("ticker") or "").upper(),
        company_name=data.get("company_name") or None,
        anchor_price=_to_float(data.get("anchor_price")),
        sections=steps,
        scores=scores,
        investment_stance=data.get("investment_stance"),
        invalidation_triggers=[str(t) for t in invalidation if str(t).strip()],
        bull_target=_to_float(step7.get("bull_target") if isinstance(step7, dict) else None),
        base_target=_to_float(step7.get("base_target") if isinstance(step7, dict) else None),
        bear_target=_to_float(step7.get("bear_target") if isinstance(step7, dict) else None),
    )


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
