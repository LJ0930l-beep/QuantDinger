"""Strategy V2 legacy live queue boundary tests (SC-15 retired)."""

import pytest

from app.services.strategy_v2 import live_execution
from app.services.strategy_v2.live_execution import LiveOrderRequest, StrategyV2OrderGateway


def _request(action="open_long"):
    return LiveOrderRequest(
        strategy_id=7,
        strategy_run_id=42,
        user_id=12,
        symbol="BTC/USDT",
        action=action,
        quantity=0.01,
        reference_price=60_000.0,
        signal_timestamp=123,
        market_type="swap",
        execution_mode="live",
    )


def test_gateway_submit_fails_closed_after_sc15_retirement():
    # SC-15 retired the legacy Strategy V2 live queue; submit must raise.
    with pytest.raises(RuntimeError, match="strategyV2.legacyQueueDisabled"):
        StrategyV2OrderGateway().submit(_request("open_long"))


def test_gateway_has_no_inflight_after_sc15_retirement():
    # has_inflight was removed with the legacy live queue (SC-15).
    assert not hasattr(StrategyV2OrderGateway(), "has_inflight")


def test_live_order_request_still_carries_sizing_facts():
    request = _request("open_long")
    assert request.strategy_id == 7
    assert request.execution_mode == "live"
    assert request.sizing is None or isinstance(request.sizing, dict)
