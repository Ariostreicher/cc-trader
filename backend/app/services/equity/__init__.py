"""Structured Equity Analysis Model (Chart Champions) — Phase 1."""

from .service import EquityAnalysisService  # noqa: F401
from .scoring import ConvictionBands, classify_conviction, compute_composite  # noqa: F401
