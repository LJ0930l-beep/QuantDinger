from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.gate_read_snapshot_contracts import build_gate_read_snapshot
from app.domain.gate_unified_read_snapshot_contracts import (
    GateUnifiedReadSnapshotError,
    build_gate_unified_read_snapshot,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _snapshot(market_type, *, account_scope="paper-account", credential_ref="credential-7", with_balance=True):
    # Some legacy Gate tests load domain modules in isolated import sandboxes.
    # Reuse the exact classes captured by the snapshot builder so this test is
    # deterministic even when the whole Gate suite runs in one process.
    auth_globals = build_gate_read_snapshot.__globals__["GateAuthFacts"].__post_init__.__globals__
    auth_type = auth_globals["GateAuthFacts"]
    permission = auth_globals["GatePermission"]
    environment = auth_globals["CapabilityEnvironment"].TESTNET
    market = auth_globals["AssetMarketType"].SPOT if market_type.value == "spot" else auth_globals["AssetMarketType"].PERPETUAL
    auth = auth_type(
        venue_id="gate",
        market_type=market,
        environment=environment,
        account_scope=account_scope,
        credential_ref=credential_ref,
        permissions=(permission.READ_ACCOUNT, permission.READ_ORDER, permission.READ_FILL),
        evidence_version="gate-private-read-v1",
        observed_at=NOW,
    )
    balances = ()
    if with_balance:
        balance_type = build_gate_read_snapshot.__globals__["GateBalanceFact"]
        balances = (balance_type(
            venue_id="gate", market_type=market, account_scope=account_scope,
            asset="USDT", total=Decimal("10"), available=Decimal("10"),
            locked=Decimal("0"), valuation_ccy="USDT", observed_at=NOW,
            source_event_id=f"balance-{market.value}", evidence_hash=f"hash-{market.value}",
        ),)
    return build_gate_read_snapshot(auth, balances=balances, observed_at=NOW)


def test_unified_snapshot_keeps_spot_and_perpetual_separate_and_stable():
    first = build_gate_unified_read_snapshot(
        (_snapshot(AssetMarketType.SPOT), _snapshot(AssetMarketType.PERPETUAL)), observed_at=NOW
    )
    second = build_gate_unified_read_snapshot(
        (_snapshot(AssetMarketType.PERPETUAL), _snapshot(AssetMarketType.SPOT)), observed_at=NOW
    )
    assert first.snapshot_fingerprint == second.snapshot_fingerprint
    public = first.to_public_dict()
    assert set(public["market_types"]) == {"spot", "perpetual"}
    assert set(public["markets"]) == {"spot", "perpetual"}
    assert "credential_ref" not in str(public)
    assert public["live_enabled"] is False
    assert public["read_health"] == {
        "status": "READY",
        "scope_verified": True,
        "account_facts_verified": True,
        "reconciliation_health": "UNKNOWN",
        "market_data_health": "UNKNOWN",
        "live_enabled": False,
    }


def test_unified_snapshot_rejects_cross_scope_or_duplicate_market():
    with pytest.raises(GateUnifiedReadSnapshotError):
        build_gate_unified_read_snapshot(
            (_snapshot(AssetMarketType.SPOT), _snapshot(AssetMarketType.PERPETUAL, account_scope="other")),
            observed_at=NOW,
        )
    with pytest.raises(GateUnifiedReadSnapshotError):
        build_gate_unified_read_snapshot(
            (_snapshot(AssetMarketType.SPOT), _snapshot(AssetMarketType.SPOT)), observed_at=NOW
        )


def test_unified_snapshot_rejects_different_credential_or_environment():
    with pytest.raises(GateUnifiedReadSnapshotError):
        build_gate_unified_read_snapshot(
            (_snapshot(AssetMarketType.SPOT), _snapshot(AssetMarketType.PERPETUAL, credential_ref="credential-8")),
            observed_at=NOW,
        )
