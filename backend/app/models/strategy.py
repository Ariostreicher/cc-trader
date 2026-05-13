"""Strategy extraction — deterministic rules derived from uploaded methodology."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .document import Document


class ExtractedStrategy(UUIDPKMixin, TimestampMixin, Base):
    """Parsed strategy JSON. Schema (loosely):
    {
        "name": "ORB",
        "timeframes": ["1m", "5m"],
        "entry": [{"type": "break_above", "level": "or_high"}],
        "exit":  [...],
        "stop":  {...},
        "take_profit": [...],
        "filters": [...],
        "confidence_scoring": {...}
    }
    """

    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    citations: Mapped[Optional[list]] = mapped_column(JSONB)  # [{"page":N,"snippet":"..."}]
    is_deterministic: Mapped[bool] = mapped_column(default=True, nullable=False)

    document: Mapped["Document"] = relationship(back_populates="strategies")
