"""Setup output types — what every detector returns when it sees a trade."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Literal

SetupDirection = Literal["long", "short"]


@dataclass(slots=True)
class Citation:
    document: str   # e.g. "First 18.pdf"
    page: int       # source page
    snippet: str    # short quote from that page


@dataclass(slots=True)
class Setup:
    """One trade setup the system has detected.

    All price levels are absolute (in the asset's currency, not pct).
    """

    symbol: str
    timeframe: str
    name: str
    direction: SetupDirection
    entry: float
    stop_loss: float
    targets: List[float]
    current_price: float
    conviction: float            # 0.0–1.0 — higher when more confirmations align
    risk_reward: float           # |(target1 - entry)| / |(entry - stop_loss)|
    reasoning: str               # 1–3 sentence explanation citing the rule
    citations: List[Citation] = field(default_factory=list)
    detected_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "name": self.name,
            "direction": self.direction,
            "entry": round(self.entry, 4),
            "stop_loss": round(self.stop_loss, 4),
            "targets": [round(t, 4) for t in self.targets],
            "current_price": round(self.current_price, 4),
            "conviction": round(self.conviction, 2),
            "risk_reward": round(self.risk_reward, 2),
            "reasoning": self.reasoning,
            "citations": [
                {"document": c.document, "page": c.page, "snippet": c.snippet}
                for c in self.citations
            ],
            "detected_at": self.detected_at.isoformat(),
        }


def _risk_reward(entry: float, stop: float, targets: List[float]) -> float:
    if not targets:
        return 0.0
    risk = abs(entry - stop)
    reward = abs(targets[0] - entry)
    return reward / risk if risk > 0 else 0.0


def make_setup(
    *,
    symbol: str,
    timeframe: str,
    name: str,
    direction: SetupDirection,
    entry: float,
    stop_loss: float,
    targets: List[float],
    current_price: float,
    conviction: float,
    reasoning: str,
    citations: List[Citation],
) -> Setup:
    return Setup(
        symbol=symbol,
        timeframe=timeframe,
        name=name,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        targets=targets,
        current_price=current_price,
        conviction=max(0.0, min(1.0, conviction)),
        risk_reward=_risk_reward(entry, stop_loss, targets),
        reasoning=reasoning,
        citations=citations,
    )
