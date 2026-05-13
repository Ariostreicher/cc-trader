"""Watchlist schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.watchlist import AssetClass


class WatchlistAssetIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    exchange: Optional[str] = Field(default=None, max_length=32)
    asset_class: AssetClass = AssetClass.stock
    sector: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=1024)


class WatchlistAssetOut(WatchlistAssetIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    created_at: datetime


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    pinned: bool = False


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str] = None
    is_public: bool
    pinned: bool
    created_at: datetime
    assets: List[WatchlistAssetOut] = []
