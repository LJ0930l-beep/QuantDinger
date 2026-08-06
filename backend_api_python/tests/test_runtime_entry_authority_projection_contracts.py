"""Pure contract coverage for Runtime Entry authority projection."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from tests.pr12c_admission_loader import load_pr12c_admission


def _build_snapshot(modules, *, account_scope="account-1", positions=(), instruments=(), market_type=None):
    """Construct a valid GateReadSnapshot via the loader's own factories."""

    from app.domain.gate_read_snapshot_contracts import build_gate_read_snapshot
    from app.domain.gate_vertical_read_contracts import (
        GateAuthFacts,
        GateBalanceFact,
        GateInstrumentRuleSnapshot,
        GatePermission,
    )
    from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment

    observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
    market = market_type or (AssetMarketType.PERPETUAL if positions else AssetMarketType.SPOT)
    auth = GateAuthFacts(
        venue_id="gate",
        market_type=market,
        environment=CapabilityEnvironment.TESTNET,
        account_scope=account_scope,
        credential_ref="credential-3896",
        permissions=(GatePermission.READ_ACCOUNT, GatePermission.READ_ORDER, GatePermission.READ_FILL),
        evidence_version="gate-private-read-v1",
        observed_at=observed,
    )
    if not instruments:
        instruments = (
            GateInstrumentRuleSnapshot(
                venue_id="gate",
                market_type=market,
                instrument_id="BTC_USDT",
                tick_size=Decimal("0.1"),
                quantity_step=Decimal("0.000001"),
                minimum_quantity=Decimal("0.00001"),
                minimum_notional=Decimal("3"),
                rule_version="gate-private-read-instrument-v1",
                observed_at=observed,
            ),
        )
    return build_gate_read_snapshot(
        auth,
        balances=(),
        instruments=instruments,
        positions=positions,
        observed_at=observed,
    )


class RuntimeEntryAuthorityProjectionContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_pr12c_admission()

    def test_scope_binding_facts_use_snapshot_auth(self):
        from app.domain.runtime_entry_authority_projection_contracts import (
            PROJECTION_CONTRACT_VERSION,
            SOURCE_IDENTITY,
            build_scope_binding_facts,
        )

        snapshot = _build_snapshot(self.modules)
        facts = build_scope_binding_facts(snapshot, tenant_id=3, credential_id=3896)
        self.assertEqual(facts["tenant_id"], 3)
        self.assertEqual(facts["credential_id"], 3896)
        self.assertEqual(facts["account_scope"], "account-1")
        self.assertEqual(facts["exchange_id"], "gate")
        self.assertEqual(facts["contract_version"], PROJECTION_CONTRACT_VERSION)
        self.assertEqual(facts["source_identity"], SOURCE_IDENTITY)
        self.assertEqual(facts["source_fingerprint"], snapshot.snapshot_fingerprint)
        self.assertEqual(len(facts["source_fingerprint"]), 64)

    def test_scope_binding_id_is_deterministic(self):
        from app.domain.runtime_entry_authority_projection_contracts import scope_binding_id

        first = scope_binding_id(3, 3896)
        second = scope_binding_id(3, 3896)
        self.assertEqual(first, second)
        self.assertNotEqual(scope_binding_id(3, 3897), first)

    def test_instrument_rule_snapshot_maps_fields_and_normalizes_case(self):
        from app.domain.runtime_entry_authority_projection_contracts import build_instrument_rule_snapshot_facts

        snapshot = _build_snapshot(self.modules)
        rows = build_instrument_rule_snapshot_facts(snapshot)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["exchange"], "gate")
        self.assertEqual(row["instrument_id"], "BTC_USDT")
        self.assertEqual(row["market_type"], "spot")
        self.assertEqual(row["tick_size"], Decimal("0.1"))
        self.assertEqual(row["quantity_step"], Decimal("0.000001"))
        self.assertEqual(row["minimum_quantity"], Decimal("0.00001"))
        self.assertEqual(row["minimum_notional"], Decimal("3"))
        self.assertEqual(row["rule_version"], "gate-private-read-instrument-v1")
        self.assertEqual(row["price_scale"], 1)  # 0.1 -> exponent -1
        self.assertEqual(row["quantity_scale"], 6)  # 0.000001 -> exponent -6

    def test_instrument_authority_facts_reference_rule_and_scope(self):
        from app.domain.runtime_entry_authority_projection_contracts import (
            build_instrument_authority_facts,
            build_instrument_rule_snapshot_facts,
            build_scope_binding_facts,
            instrument_authority_id,
            scope_binding_id,
        )

        snapshot = _build_snapshot(self.modules)
        rule_rows = build_instrument_rule_snapshot_facts(snapshot)
        scope = build_scope_binding_facts(snapshot, tenant_id=3, credential_id=3896)
        authorities = build_instrument_authority_facts(snapshot, rule_rows, tenant_id=3, credential_id=3896, account_scope="account-1")
        self.assertEqual(len(authorities), 1)
        row = authorities[0]
        self.assertEqual(row["scope_binding_id"], scope_binding_id(3, 3896))
        self.assertEqual(row["instrument_rule_snapshot_id"], rule_rows[0]["id"])
        self.assertEqual(row["id"], instrument_authority_id(3, 3896, "account-1", "BTC_USDT", "spot"))
        self.assertEqual(row["source_fingerprint"], snapshot.snapshot_fingerprint)

    def test_position_projection_excludes_zero_quantity(self):
        from app.domain.gate_vertical_read_contracts import (
            GateAuthFacts,
            GateInstrumentRuleSnapshot,
            GateMarginMode,
            GatePermission,
            GatePositionFact,
            GatePositionSide,
        )
        from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
        from app.domain.runtime_entry_authority_projection_contracts import (
            POSITION_POLICY_VERSION,
            build_position_projection_facts,
        )

        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        positions = (
            GatePositionFact(
                venue_id="gate", market_type=AssetMarketType.PERPETUAL, account_scope="account-1",
                instrument_id="BTC_USDT", side=GatePositionSide.LONG,
                quantity=Decimal("0.01"), average_entry_price=Decimal("60000"),
                mark_price=Decimal("60100"), leverage=Decimal("10"),
                margin_mode=GateMarginMode.ISOLATED, observed_at=observed,
                source_event_id="event-1", realized_pnl=Decimal("5"),
            ),
            GatePositionFact(
                venue_id="gate", market_type=AssetMarketType.PERPETUAL, account_scope="account-1",
                instrument_id="BTC_USDT", side=GatePositionSide.SHORT,
                quantity=Decimal("0"), average_entry_price=Decimal("60000"),
                mark_price=Decimal("60100"), leverage=Decimal("10"),
                margin_mode=GateMarginMode.ISOLATED, observed_at=observed,
                source_event_id="event-2",
            ),
        )
        snapshot = _build_snapshot(self.modules, positions=positions)
        rows = build_position_projection_facts(snapshot, tenant_id=3, credential_id=3896, account_scope="account-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], "LONG")
        self.assertEqual(rows[0]["quantity"], Decimal("0.01"))
        self.assertEqual(rows[0]["average_cost"], Decimal("60000"))
        self.assertEqual(rows[0]["realized_pnl"], Decimal("5"))
        self.assertEqual(rows[0]["policy_version"], POSITION_POLICY_VERSION)
        self.assertEqual(rows[0]["projection_version"], 1)

    def test_position_side_mapping(self):
        from app.domain.gate_vertical_read_contracts import GatePositionSide
        from app.domain.runtime_entry_authority_projection_contracts import position_side

        self.assertEqual(position_side(GatePositionSide.LONG), "LONG")
        self.assertEqual(position_side(GatePositionSide.SHORT), "SHORT")

    def test_fingerprint_validation_rejects_short_value(self):
        from app.domain.runtime_entry_authority_projection_contracts import (
            RuntimeEntryAuthorityProjectionError,
            build_scope_binding_facts,
        )

        snapshot = _build_snapshot(self.modules)
        object.__setattr__(snapshot, "snapshot_fingerprint", "abc")  # bypass frozen validation for the failure path
        with self.assertRaises(RuntimeEntryAuthorityProjectionError):
            build_scope_binding_facts(snapshot, tenant_id=3, credential_id=3896)


if __name__ == "__main__":
    unittest.main()
