from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_contracts() -> SimpleNamespace:
    root = Path(__file__).resolve().parents[1]
    names = ("app", "app.domain", "app.domain.multi_asset_capability_contracts", "app.domain.gate_vertical_read_contracts")
    missing = object()
    original = {name: sys.modules.get(name, missing) for name in names}
    try:
        app_package = ModuleType("app")
        app_package.__path__ = [str(root / "app")]
        domain_package = ModuleType("app.domain")
        domain_package.__path__ = [str(root / "app" / "domain")]
        sys.modules["app"] = app_package
        sys.modules["app.domain"] = domain_package
        multi = _load("app.domain.multi_asset_capability_contracts", root / "app" / "domain" / "multi_asset_capability_contracts.py")
        gate = _load("app.domain.gate_vertical_read_contracts", root / "app" / "domain" / "gate_vertical_read_contracts.py")
        return SimpleNamespace(multi=multi, gate=gate)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


CONTRACTS = _load_contracts()
MODULE = CONTRACTS.gate
MULTI = CONTRACTS.multi


UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


class GateVerticalReadContractTests(unittest.TestCase):
    def test_auth_is_typed_and_write_permission_is_rejected(self):
        with self.assertRaises(MODULE.GateVerticalContractError):
            MODULE.GateAuthFacts(
                venue_id="gate",
                market_type=MULTI.AssetMarketType.SPOT,
                environment=MULTI.CapabilityEnvironment.TESTNET,
                account_scope="paper-a",
                credential_ref="opaque-ref",
                permissions=(MODULE.GatePermission.READ_MARKET, MODULE.GatePermission.WRITE_ORDER),
                evidence_version="auth-v1",
                observed_at=UTC,
            )

    def test_market_permission_requires_exact_profile(self):
        auth = MODULE.GateAuthFacts(
            venue_id="gate",
            market_type=MULTI.AssetMarketType.SPOT,
            environment=MULTI.CapabilityEnvironment.TESTNET,
            account_scope="paper-a",
            credential_ref="opaque-ref",
            permissions=(MODULE.GatePermission.READ_MARKET,),
            evidence_version="auth-v1",
            observed_at=UTC,
        )
        MODULE.require_gate_capability(MULTI.gate_testnet_capability_matrix(), auth, MODULE.GatePermission.READ_MARKET)
        with self.assertRaises(MULTI.UnsupportedCapability):
            MODULE.require_gate_capability(MULTI.gate_testnet_capability_matrix(), auth, MODULE.GatePermission.READ_ACCOUNT)

    def test_balance_requires_decimal_and_balances(self):
        fact = MODULE.GateBalanceFact(
            venue_id="gate",
            market_type=MULTI.AssetMarketType.SPOT,
            account_scope="paper-a",
            asset="usdt",
            total=Decimal("12.50"),
            available=Decimal("10"),
            locked=Decimal("2.50"),
            valuation_ccy="usdt",
            observed_at=UTC,
            source_event_id="balance-1",
            evidence_hash="hash-1",
        )
        self.assertEqual(fact.asset, "USDT")
        with self.assertRaises(MODULE.GateVerticalContractError):
            MODULE.GateBalanceFact(
                venue_id="gate", market_type=MULTI.AssetMarketType.SPOT, account_scope="paper-a",
                asset="USDT", total=1.0, available=Decimal("1"), locked=Decimal("0"),
                valuation_ccy="USDT", observed_at=UTC, source_event_id="balance-2", evidence_hash="hash-2"
            )

    def test_instrument_rules_are_versioned_and_positive(self):
        snapshot = MODULE.GateInstrumentRuleSnapshot(
            venue_id="gate", market_type=MULTI.AssetMarketType.PERPETUAL, instrument_id="BTC_USDT",
            tick_size=Decimal("0.1"), quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"), minimum_notional=Decimal("5"),
            rule_version="rules-v1", observed_at=UTC,
        )
        self.assertEqual(snapshot.rule_version, "rules-v1")
        with self.assertRaises(MODULE.GateVerticalContractError):
            MODULE.GateInstrumentRuleSnapshot(
                venue_id="gate", market_type=MULTI.AssetMarketType.PERPETUAL, instrument_id="BTC_USDT",
                tick_size=Decimal("0"), quantity_step=Decimal("0.001"),
                minimum_quantity=Decimal("0"), minimum_notional=Decimal("0"),
                rule_version="rules-v1", observed_at=UTC,
            )

    def test_position_scope_and_decimal_values_are_immutable(self):
        position = MODULE.GatePositionFact(
            venue_id="gate", market_type=MULTI.AssetMarketType.PERPETUAL, account_scope="paper-a",
            instrument_id="BTC_USDT", side=MODULE.GatePositionSide.LONG, quantity=Decimal("0.1"),
            average_entry_price=Decimal("100"), mark_price=Decimal("101"), leverage=Decimal("2"),
            margin_mode=MODULE.GateMarginMode.CROSS, observed_at=UTC, source_event_id="position-1",
        )
        with self.assertRaises((AttributeError, TypeError)):
            position.quantity = Decimal("1")

    def test_fingerprint_is_deterministic_and_scope_sensitive(self):
        left = MODULE.GateInstrumentRuleSnapshot(
            venue_id="gate", market_type=MULTI.AssetMarketType.SPOT, instrument_id="ETH_USDT",
            tick_size=Decimal("0.01"), quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"), minimum_notional=Decimal("5"),
            rule_version="rules-v1", observed_at=UTC,
        )
        right = MODULE.GateInstrumentRuleSnapshot(
            venue_id="gate", market_type=MULTI.AssetMarketType.SPOT, instrument_id="ETH_USDT",
            tick_size=Decimal("0.0100"), quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"), minimum_notional=Decimal("5"),
            rule_version="rules-v1", observed_at=UTC,
        )
        self.assertEqual(MODULE.gate_read_fingerprint(left), MODULE.gate_read_fingerprint(right))
        self.assertEqual(MODULE.gate_read_fingerprint(left), MODULE.gate_read_fingerprint(left))


if __name__ == "__main__":
    unittest.main()
