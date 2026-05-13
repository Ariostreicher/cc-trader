"""Paper trading: portfolios + trades."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class TradeSide(str, enum.Enum):
    long = "long"
    short = "short"


class TradeStatus(str, enum.Enum):
    open = "open"
    closed = "closed"
    cancelled = "cancelled"


class Portfolio(UUIDPKMixin, TimestampMixin, Base):
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="Paper")
    starting_cash: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, default=100_000)
    cash: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, default=100_000)
    realized_pnl: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, default=0)

    user: Mapped["User"] = relationship(back_populates="portfolios")
    trades: Mapped[List["PaperTrade"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PaperTrade(UUIDPKMixin, TimestampMixin, Base):
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide, name="trade_side"), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    take_profit: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    status: Mapped[TradeStatus] = mapped_column(
        Enum(TradeStatus, name="trade_status"), default=TradeStatus.open, nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    pnl: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    journal: Mapped[Optional[str]] = mapped_column(Text)

    portfolio: Mapped[Portfolio] = relationship(back_populates="trades")
