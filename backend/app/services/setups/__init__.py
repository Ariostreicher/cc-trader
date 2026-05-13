"""Chart Champions technical setup detectors.

Each detector is a pure function `(symbol, df) -> Optional[Setup]` that
applies a specific rule from the Chart Champions cheatsheets. The Setup
object carries entry/stop/take-profit levels grounded in the methodology
plus a citation to the source page.
"""

from .types import Setup, SetupDirection  # noqa: F401
from .detectors import (  # noqa: F401
    detect_all,
    detect_ema_alignment_pullback,
    detect_cc_region_pullback,
    detect_sr_flip,
    detect_sr_breakout,
    detect_3rd_touch,
    detect_inside_day_breakout,
    detect_rsi_reversal,
    detect_volume_spike_breakout,
    detect_bollinger_squeeze,
)
