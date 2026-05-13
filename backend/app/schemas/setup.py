"""Schemas for the Live Setups endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class CitationOut(BaseModel):
    document: str
    page: int
    snippet: str


class SetupOut(BaseModel):
    symbol: str
    timeframe: str
    name: str
    direction: str
    entry: float
    stop_loss: float
    targets: List[float]
    current_price: float
    conviction: float
    risk_reward: float
    reasoning: str
    citations: List[CitationOut]
    detected_at: datetime


class ScanOut(BaseModel):
    timeframe: str
    setups: List[SetupOut]
    scanned: int        # how many symbols we ran the detectors on
    skipped: int        # how many had no data / failed
    duration_ms: int


class ChartLevel(BaseModel):
    label: str
    value: float
    kind: str   # "support" | "resistance" | "ema" | "fib" | "entry" | "stop" | "target"


class ChartPayload(BaseModel):
    symbol: str
    timeframe: str
    bars: List[dict]              # [{t, o, h, l, c, v}, ...]
    levels: List[ChartLevel]
    setups: List[SetupOut]
