from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.gate_read_snapshot_contracts import build_gate_read_snapshot
from app.domain.gate_unified_read_snapshot_contracts import build_gate_unified_read_snapshot
from app.domain.multi_asset_capability_contracts import AssetMarketType
from app.services.readonly_gate_unified_account_service import ReadonlyGateUnifiedAccountService


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _unified(*, with_balances=True):
    def snapshot(market):
        auth_globals = build_gate_read_snapshot.__globals__["GateAuthFacts"].__post_init__.__globals__
        auth = auth_globals["GateAuthFacts"](
            venue_id="gate", market_type=(auth_globals["AssetMarketType"].SPOT if market.value == "spot" else auth_globals["AssetMarketType"].PERPETUAL), environment=auth_globals["CapabilityEnvironment"].TESTNET,
            account_scope="scope", credential_ref="credential-1",
            permissions=(auth_globals["GatePermission"].READ_ACCOUNT,), evidence_version="v1", observed_at=NOW,
        )
        balances = ()
        if with_balances:
            balance_type = build_gate_read_snapshot.__globals__["GateBalanceFact"]
            balances = (balance_type(
                venue_id="gate", market_type=auth.market_type, account_scope="scope",
                asset="USDT", total=Decimal("10"), available=Decimal("10"),
                locked=Decimal("0"), valuation_ccy="USDT", observed_at=NOW,
                source_event_id=f"balance-{market.value}", evidence_hash=f"hash-{market.value}",
            ),)
        return build_gate_read_snapshot(auth, balances=balances, observed_at=NOW)
    return build_gate_unified_read_snapshot((snapshot(AssetMarketType.SPOT), snapshot(AssetMarketType.PERPETUAL)), observed_at=NOW)


def test_unified_service_returns_sanitized_snapshot():
    status, body = ReadonlyGateUnifiedAccountService(lambda *args: _unified()).read_response(
        user_id=1, credential_id=2, account_scope="scope", instrument_id="BTC_USDT", as_of=NOW
    )
    assert status == 200
    assert body["status"] == "READY"
    assert set(body["markets"]) == {"spot", "perpetual"}
    assert "credential_ref" not in str(body)
    assert body["live_enabled"] is False


def test_unified_service_returns_health_receipt_without_account_payload():
    status, body = ReadonlyGateUnifiedAccountService(lambda *args: _unified()).read_health_response(
        user_id=1, credential_id=2, account_scope="scope", instrument_id="BTC_USDT", as_of=NOW
    )
    assert status == 200
    assert body["status"] == "READY"
    assert body["read_health"]["scope_verified"] is True
    assert body["read_health"]["reconciliation_health"] == "UNKNOWN"
    assert "balances" not in body
    assert "credential_ref" not in str(body)
    assert body["live_enabled"] is False


def test_unified_service_maps_safe_partial_failure():
    class ProviderError(RuntimeError):
        code = "GATE_TESTNET_PARTIAL_READ"
        failed_markets = ({"market_type": "perpetual", "code": code},)

    status, body = ReadonlyGateUnifiedAccountService(lambda *args: (_ for _ in ()).throw(ProviderError("secret"))).read_response(
        user_id=1, credential_id=2, account_scope="scope", instrument_id="BTC_USDT", as_of=NOW
    )
    assert status == 503
    assert body["code"] == "GATE_TESTNET_PARTIAL_READ"
    assert body["data"]["failed_markets"] == [{"market_type": "perpetual", "code": "GATE_TESTNET_PARTIAL_READ"}]
    assert "secret" not in str(body)


def test_unified_service_rejects_incomplete_account_facts():
    status, body = ReadonlyGateUnifiedAccountService(
        lambda *args: _unified(with_balances=False)
    ).read_response(
        user_id=1, credential_id=2, account_scope="scope", instrument_id="BTC_USDT", as_of=NOW
    )
    assert status == 503
    assert body["status"] == "UNAVAILABLE"
    assert body["code"] == "GATE_ACCOUNT_FACTS_INCOMPLETE"
    assert body["data"]["read_health"]["status"] == "INCOMPLETE"
    assert body["data"]["read_health"]["account_facts_verified"] is False


def test_unified_service_rejects_non_utc_input():
    with pytest.raises(Exception):
        ReadonlyGateUnifiedAccountService(lambda *args: _unified()).read_response(
            user_id=1, credential_id=2, account_scope="scope", instrument_id="BTC_USDT",
            as_of=datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc).replace(tzinfo=None),
        )
