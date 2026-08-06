import pytest

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.gate import GateUsdtFuturesClient


def _client(calls):
    client = GateUsdtFuturesClient(api_key="key", secret_key="secret")
    client._signed_request = lambda *args, **kwargs: calls.append((args, kwargs)) or {}
    return client


def test_gate_futures_adapter_rejects_legacy_leverage_before_request():
    calls = []
    with pytest.raises(LiveTradingError, match="between 50x and 100x"):
        _client(calls).set_leverage(contract="BTC_USDT", leverage=5)
    assert calls == []


def test_gate_futures_adapter_sends_only_valid_contract_leverage():
    calls = []
    assert _client(calls).set_leverage(contract="BTC_USDT", leverage=50, margin_mode="cross") is True
    assert calls[0][1]["params"] == {"leverage": "0", "cross_leverage_limit": "50"}
