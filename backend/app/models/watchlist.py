"""Watchlists + watchlist assets."""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class AssetClass(str, enum.Enum):
    stock = "stock"
    etf = "etf"
    crypto = "crypto"
    index = "index"
    forex = "forex"


class Watchlist(UUIDPKMixin, TimestampMixin, Base):
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,  # NULL = system / public watchlist (e.g. CC 2026 list)
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1024))
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[Optional["User"]] = relationship(back_populates="watchlists")
    assets: Mapped[List["WatchlistAsset"]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
        order_by="WatchlistAsset.position",
    )

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),)


class WatchlistAsset(UUIDPKMixin, TimestampMixin, Base):
    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[Optional[str]] = mapped_column(String(32))
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, name="asset_class"), default=AssetClass.stock, nullable=False
    )
    sector: Mapped[Optional[str]] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(1024))

    watchlist: Mapped[Watchlist] = relationship(back_populates="assets")

    __table_args__ = (
        UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_asset_symbol"),
    )
