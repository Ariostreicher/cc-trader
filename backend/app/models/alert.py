"""Alerts + notification audit log."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class AlertTrigger(str, enum.Enum):
    price_above = "price_above"
    price_below = "price_below"
    rsi_above = "rsi_above"
    rsi_below = "rsi_below"
    macd_cross_up = "macd_cross_up"
    macd_cross_down = "macd_cross_down"
    volume_spike = "volume_spike"
    sr_break_above = "sr_break_above"
    sr_break_below = "sr_break_below"
    sr_bounce = "sr_bounce"
    ema_cross_up = "ema_cross_up"
    ema_cross_down = "ema_cross_down"
    ai_confidence_above = "ai_confidence_above"
    custom = "custom"


class AlertChannel(str, enum.Enum):
    in_app = "in_app"
    email = "email"
    telegram = "telegram"
    discord = "discord"
    sms = "sms"
    push = "push"


class Alert(UUIDPKMixin, TimestampMixin, Base):
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    trigger: Mapped[AlertTrigger] = mapped_column(
        Enum(AlertTrigger, name="alert_trigger"), nullable=False
    )
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    channels: Mapped[List[str]] = mapped_column(JSONB, default=list, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="alerts")
    notifications: Mapped[List["NotificationLog"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )


class NotificationLog(UUIDPKMixin, TimestampMixin, Base):
    alert_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[AlertChannel] = mapped_column(Enum(AlertChannel, name="alert_channel"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # delivered | failed
    payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    error: Mapped[Optional[str]] = mapped_column(Text)

    alert: Mapped[Optional[Alert]] = relationship(back_populates="notifications")
