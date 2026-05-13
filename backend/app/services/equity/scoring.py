"""Composite scoring + conviction-band classification.

Verbatim from the Chart Champions Structured Equity Model Master Instructions:

> Composite Rating = Average of all category scores. No weighting adjustments.
> No narrative overrides.

> Conviction Bands:
> 4.5 – 5.0 → Very High Conviction
> 4.0 – 4.49 → High Conviction
> 3.5 – 3.99 → Moderate Conviction
> 3.0 – 3.49 → Selective / Cautious
> Below 3.0 → Avoid / Monitor
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ...models.equity import ConvictionLevel

REQUIRED_CATEGORIES: tuple[str, ...] = (
    "Business Quality",
    "Financial Quality",
    "Competitive Positioning",
    "Growth Potential",
    "Risk Profile",
    "Sentiment & Positioning",
    "Valuation Outlook",
)


@dataclass(slots=True, frozen=True)
class ConvictionBand:
    label: str
    lower: float
    upper: float  # inclusive upper bound for all but the open-ended top band
    level: ConvictionLevel


# Order matters: highest band first so iteration stops at the first match.
ConvictionBands: tuple[ConvictionBand, ...] = (
    ConvictionBand("Very High Conviction", 4.5, 5.0, ConvictionLevel.very_high),
    ConvictionBand("High Conviction", 4.0, 4.49, ConvictionLevel.high),
    ConvictionBand("Moderate Conviction", 3.5, 3.99, ConvictionLevel.moderate),
    ConvictionBand("Selective / Cautious", 3.0, 3.49, ConvictionLevel.selective),
    ConvictionBand("Avoid / Monitor", 0.0, 2.99, ConvictionLevel.avoid),
)


def compute_composite(scores: Mapping[str, float]) -> float:
    """Mean of the seven category scores. Strict: missing keys raise."""
    missing = [c for c in REQUIRED_CATEGORIES if c not in scores]
    if missing:
        raise ValueError(f"missing required category scores: {missing}")
    values = [float(scores[c]) for c in REQUIRED_CATEGORIES]
    return round(sum(values) / len(values), 2)


def classify_conviction(composite: float) -> ConvictionBand:
    """Return the band that contains ``composite``. The method has zero
    tolerance — bands are right-inclusive — exactly as the source documents
    state."""
    if composite >= 4.5:
        return ConvictionBands[0]
    if composite >= 4.0:
        return ConvictionBands[1]
    if composite >= 3.5:
        return ConvictionBands[2]
    if composite >= 3.0:
        return ConvictionBands[3]
    return ConvictionBands[4]


def validate_scores(scores: Mapping[str, float]) -> None:
    """Raise if any score falls outside [1.0, 5.0]."""
    for cat in REQUIRED_CATEGORIES:
        s = float(scores[cat])
        if not (1.0 <= s <= 5.0):
            raise ValueError(f"score for '{cat}' = {s} is outside [1.0, 5.0]")
