"""Unit tests for the deterministic parts of the equity model.

These are the parts the methodology mandates exactly:
- composite = average of 7 categories
- band classification by composite
"""

from __future__ import annotations

import pytest

from app.models.equity import ConvictionLevel
from app.services.equity.scoring import (
    REQUIRED_CATEGORIES,
    classify_conviction,
    compute_composite,
)


def _scores(value: float) -> dict[str, float]:
    return {c: value for c in REQUIRED_CATEGORIES}


def test_composite_is_arithmetic_mean():
    s = _scores(4.0)
    s["Risk Profile"] = 2.0
    expected = round((4 * 6 + 2) / 7, 2)
    assert compute_composite(s) == expected


def test_missing_category_raises():
    s = _scores(4.0)
    del s["Risk Profile"]
    with pytest.raises(ValueError):
        compute_composite(s)


@pytest.mark.parametrize(
    "composite,expected",
    [
        (4.9, ConvictionLevel.very_high),
        (4.5, ConvictionLevel.very_high),
        (4.49, ConvictionLevel.high),
        (4.0, ConvictionLevel.high),
        (3.99, ConvictionLevel.moderate),
        (3.5, ConvictionLevel.moderate),
        (3.49, ConvictionLevel.selective),
        (3.0, ConvictionLevel.selective),
        (2.99, ConvictionLevel.avoid),
        (1.0, ConvictionLevel.avoid),
    ],
)
def test_band_classification(composite, expected):
    assert classify_conviction(composite).level is expected
