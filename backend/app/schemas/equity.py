"""Pydantic schemas for the Structured Equity Analysis Model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.equity import ConvictionLevel


class EquityRunIn(BaseModel):
    """Per the methodology: input is only a company name (or ticker)."""

    ticker: str = Field(min_length=1, max_length=32)
    company_name: Optional[str] = Field(default=None, max_length=255)


class CategoryScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    score: float
    summary: Optional[str] = None


class EquityReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    company_name: Optional[str]
    sections: dict[str, dict[str, Any]]
    composite_score: float
    conviction: ConvictionLevel
    investment_stance: Optional[str]
    bull_target: Optional[float]
    base_target: Optional[float]
    bear_target: Optional[float]
    anchor_price: Optional[float]
    invalidation_triggers: Optional[list[str]] = None
    category_scores: list[CategoryScoreOut] = []
    citations: Optional[list[dict[str, Any]]] = None
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    created_at: datetime


class EquityReportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    company_name: Optional[str]
    composite_score: float
    conviction: ConvictionLevel
    investment_stance: Optional[str]
    created_at: datetime
