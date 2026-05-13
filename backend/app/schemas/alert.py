"""Alert schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.alert import AlertChannel, AlertTrigger


class AlertIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    trigger: AlertTrigger
    params: dict = Field(default_factory=dict)
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    channels: List[AlertChannel] = Field(default_factory=lambda: [AlertChannel.in_app])
    note: Optional[str] = None
    is_enabled: bool = True


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    trigger: AlertTrigger
    params: dict
    cooldown_seconds: int
    channels: List[str]
    note: Optional[str] = None
    is_enabled: bool
    last_triggered_at: Optional[datetime] = None
    created_at: datetime
