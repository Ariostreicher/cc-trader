"""Structured Equity Analysis Model — persisted reports.

Each report is the result of running the 9-step framework on one ticker.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class ConvictionLevel(str, enum.Enum):
    very_high = "very_high"      # 4.5 – 5.0
    high = "high"                # 4.0 – 4.49
    moderate = "moderate"        # 3.5 – 3.99
    selective = "selective"      # 3.0 – 3.49
    avoid = "avoid"              # < 3.0


class EquityReport(UUIDPKMixin, TimestampMixin, Base):
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))

    # nine narrative sections — store as JSONB so we can render structured
    sections: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # numeric scores
    composite_score: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    conviction: Mapped[ConvictionLevel] = mapped_column(
        Enum(ConvictionLevel, name="conviction_level"), nullable=False
    )
    investment_stance: Mapped[Optional[str]] = mapped_column(String(64))

    # bull/base/bear from step 7
    bull_target: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    base_target: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    bear_target: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    anchor_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))

    invalidation_triggers: Mapped[Optional[list]] = mapped_column(JSONB)
    raw_llm_output: Mapped[Optional[str]] = mapped_column(Text)
    model_used: Mapped[Optional[str]] = mapped_column(String(64))
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer)
    citations: Mapped[Optional[list]] = mapped_column(JSONB)

    user: Mapped["User"] = relationship(back_populates="equity_reports")
    category_scores: Mapped[List["CategoryScore"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class CategoryScore(UUIDPKMixin, TimestampMixin, Base):
    """One score per scoring category per report (7 rows per report)."""

    report_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("equity_reports.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)

    report: Mapped[EquityReport] = relationship(back_populates="category_scores")

    __table_args__ = (UniqueConstraint("report_id", "category", name="uq_score_report_cat"),)
