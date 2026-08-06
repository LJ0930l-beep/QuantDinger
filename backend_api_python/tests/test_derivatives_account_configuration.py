"""Derivative account configuration safety checks."""

import pytest

from app.services.live_trading.account_configuration import configure_derivatives_account
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.okx import OkxClient


def test_okx_spot_account_mode_is_rejected_before_leverage_change():
    client = OkxClient.__new__(OkxClient)
    leverage_calls = []
    client.get_account_config = lambda: {"acctLv": "1", "posMode": "net_mode"}
    client.set_leverage = lambda **kwargs: leverage_calls.append(kwargs) or True

    with pytest.raises(LiveTradingError, match="OKX_SWAP_ACCOUNT_MODE_REQUIRED"):
        configure_derivatives_account(
            client,
            exchange_id="okx",
            symbol="BTC/USDT",
            leverage=5,
            margin_mode="cross",
        )

    assert leverage_calls == []


def test_bybit_unchanged_leverage_is_success():
    client = BybitClient.__new__(BybitClient)
    client.category = "linear"

    def unchanged(*_args, **_kwargs):
        raise LiveTradingError("Bybit error: {'retCode': 110043, 'retMsg': 'leverage not modified'}")

    client._signed_request = unchanged

    assert client.set_leverage(symbol="BTC/USDT", leverage=1) is True


def test_bybit_unchanged_margin_mode_is_success():
    client = BybitClient.__new__(BybitClient)
    client.category = "linear"

    def unchanged(*_args, **_kwargs):
        raise LiveTradingError("Bybit error: {'retCode': 110026, 'retMsg': 'margin mode not modified'}")

    client._signed_request = unchanged

    assert client.set_margin_mode("cross") is True


def test_gate_receives_strategy_leverage_only_after_contract_bounds_are_verified():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    calls = []
    client.get_contract = lambda **kwargs: {
        "leverage_min": "1",
        "leverage_max": "100",
    }
    client.set_leverage = lambda **kwargs: calls.append(kwargs) or True

    details = configure_derivatives_account(
        client,
        exchange_id="gate",
        symbol="BTC/USDT",
        leverage=5,
        margin_mode="cross",
    )

    assert details["leverage"] == 5
    assert details["contract"] == "BTC_USDT"
    assert details["exchange_leverage_min"] == "1"
    assert details["exchange_leverage_max"] == "100"
    assert details["leverage_verified"] is True
    assert calls == [{"contract": "BTC_USDT", "leverage": 5, "margin_mode": "cross"}]


def test_gate_rejects_requested_leverage_above_contract_maximum_before_mutation():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    calls = []
    client.get_contract = lambda **kwargs: {
        "leverage_min": "50",
        "leverage_max": "100",
    }
    client.set_leverage = lambda **kwargs: calls.append(kwargs) or True

    with pytest.raises(LiveTradingError, match="outside contract bounds"):
        configure_derivatives_account(
            client,
            exchange_id="gate",
            symbol="BTC/USDT",
            leverage=5,
            margin_mode="cross",
        )

    assert calls == []


def test_gate_accepts_requested_leverage_inside_contract_50_to_100_interval():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    calls = []
    client.get_contract = lambda **kwargs: {
        "leverage_min": "50",
        "leverage_max": "100",
    }
    client.set_leverage = lambda **kwargs: calls.append(kwargs) or True

    details = configure_derivatives_account(
        client,
        exchange_id="gate",
        symbol="BTC/USDT",
        leverage=50,
        margin_mode="cross",
    )

    assert details["exchange_leverage_min"] == "50"
    assert details["exchange_leverage_max"] == "100"
    assert details["leverage_verified"] is True
    assert calls == [{"contract": "BTC_USDT", "leverage": 50, "margin_mode": "cross"}]


def test_gate_rejects_missing_contract_bounds_fail_closed():
    client = GateUsdtFuturesClient.__new__(GateUsdtFuturesClient)
    client.get_contract = lambda **kwargs: {"leverage_max": "100"}
    client.set_leverage = lambda **kwargs: True

    with pytest.raises(LiveTradingError, match="Invalid exchange leverage bound"):
        configure_derivatives_account(
            client,
            exchange_id="gate",
            symbol="BTC/USDT",
            leverage=5,
            margin_mode="cross",
        )
