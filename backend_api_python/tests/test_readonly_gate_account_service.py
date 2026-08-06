from datetime import datetime, timezone

import pytest

from app.services.readonly_gate_account_service import ReadonlyGateAccountService


def test_readonly_gate_service_returns_safe_typed_provider_error():
    class ProviderError(RuntimeError):
        code = "GATE_TESTNET_PERMISSION_OR_IP_REJECTED"
        failed_markets = ({"market_type": "spot", "code": code},)

    def provider(*args):
        raise ProviderError("raw secret must not escape")

    status, body = ReadonlyGateAccountService(provider).read_response(
        user_id=1,
        credential_id=2,
        market_type="spot",
        account_scope="gate-testnet",
        instrument_id="BTC_USDT",
        as_of=datetime.now(timezone.utc),
    )

    assert status == 403
    assert body["code"] == "GATE_TESTNET_PERMISSION_OR_IP_REJECTED"
    assert body["data"]["failed_markets"] == [{"market_type": "spot", "code": "GATE_TESTNET_PERMISSION_OR_IP_REJECTED"}]
    assert "raw secret" not in str(body)


def test_readonly_gate_service_keeps_untyped_failures_generic():
    def provider(*args):
        raise RuntimeError("opaque provider detail")

    with pytest.raises(Exception) as captured:
        ReadonlyGateAccountService(provider).read_response(
            user_id=1,
            credential_id=2,
            market_type="spot",
            account_scope="gate-testnet",
            instrument_id="BTC_USDT",
            as_of=datetime.now(timezone.utc),
        )
    assert "opaque provider detail" not in str(captured.value)
