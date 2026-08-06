"""Read-only service for deterministic Gate TestNet execution rehearsal."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.gate_testnet_execution_contracts import (
    GateExecutionKind,
    GateTestnetExecutionReceipt,
    GateTestnetExecutionRequest,
    simulate_gate_testnet_execution,
)
from app.domain.gate_vertical_read_contracts import GateOrderSide
from app.domain.multi_asset_capability_contracts import AssetMarketType


class GateTestnetExecutionRehearsalServiceError(RuntimeError):
    """The local TestNet execution rehearsal failed closed."""


@dataclass(frozen=True, slots=True)
class GateTestnetExecutionRehearsalService:
    """Expose a safe fixture lifecycle; no client, DB, or credential access."""

    def run(self, *, instrument_id: str = "BTC_USDT", market_type: str = "perpetual", fill_ratio: str = "1") -> GateTestnetExecutionReceipt:
        try:
            # Some full-suite fixtures load the domain module in an isolated
            # namespace and later restore ``sys.modules``.  Resolve the
            # constructor's own related classes so its identity checks remain
            # valid regardless of import order.
            request_type = GateTestnetExecutionRequest
            request_globals = request_type.__post_init__.__globals__
            request_market_type = request_globals["AssetMarketType"]
            request_order_side = request_globals["GateOrderSide"]
            request_execution_kind = request_globals["GateExecutionKind"]
            simulate = request_globals["simulate_gate_testnet_execution"]
            request = request_type(
                instrument_id=instrument_id,
                market_type=request_market_type(market_type.lower()),
                account_scope="fixture-testnet",
                side=request_order_side.BUY,
                quantity=Decimal("0.01000000"),
                reference_price=Decimal("100.00"),
                execution_kind=request_execution_kind.MARKET,
                fill_ratio=Decimal(fill_ratio),
                fee_rate=Decimal("0.001"),
                fee_asset="USDT",
                client_order_id="fixture-client-order-1",
            )
            return simulate(request)
        except Exception as exc:
            raise GateTestnetExecutionRehearsalServiceError("TestNet execution rehearsal unavailable") from exc


def service_from_app(_app) -> GateTestnetExecutionRehearsalService:
    return GateTestnetExecutionRehearsalService()


__all__ = ["GateTestnetExecutionRehearsalService", "GateTestnetExecutionRehearsalServiceError", "service_from_app"]
