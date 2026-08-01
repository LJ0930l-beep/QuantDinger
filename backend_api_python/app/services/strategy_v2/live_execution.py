"""Order queue boundary for Strategy API V2 live sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiveOrderRequest:
    strategy_id: int
    strategy_run_id: int
    user_id: int
    symbol: str
    action: str
    quantity: float
    reference_price: float
    signal_timestamp: int
    market_type: str
    execution_mode: str
    leverage: float = 1.0
    reason: str = ""
    notification_config: dict[str, Any] | None = None
    order_type: str = "market"
    execution_algo: str = "market"
    limit_price: float = 0.0
    maker_wait_sec: float = 0.0
    maker_offset_bps: float = 0.0
    protection: dict[str, Any] | None = None
    sizing: dict[str, Any] | None = None


class StrategyV2OrderGateway:
    """Retired legacy queue boundary for Strategy V2 live sessions."""

    def submit(self, request: LiveOrderRequest) -> int | None:
        raise RuntimeError("strategyV2.legacyQueueDisabled")
