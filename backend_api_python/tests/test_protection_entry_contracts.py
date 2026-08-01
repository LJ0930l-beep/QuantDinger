from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def _load_contracts():
    """Load pure contracts without importing Flask application startup."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts", "app.domain.canonical_entry_v2_contracts",
        "app.domain.protection_entry_contracts",
    )
    missing = object()
    original = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = types.ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = types.ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        modules = {}
        for name, relative in (
            ("app.domain.decimal_values", "domain/decimal_values.py"),
            ("app.domain.order_contracts", "domain/order_contracts.py"),
            ("app.domain.canonical_entry_contracts", "domain/canonical_entry_contracts.py"),
            ("app.domain.canonical_entry_v2_contracts", "domain/canonical_entry_v2_contracts.py"),
            ("app.domain.protection_entry_contracts", "domain/protection_entry_contracts.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, app_dir / relative)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name] = module
        return types.SimpleNamespace(
            decimal=modules["app.domain.decimal_values"],
            entry=modules["app.domain.canonical_entry_contracts"],
            entry_v2=modules["app.domain.canonical_entry_v2_contracts"],
            order=modules["app.domain.order_contracts"],
            protection=modules["app.domain.protection_entry_contracts"],
        )
    finally:
        for name in reversed(names):
            if original[name] is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original[name]


m = _load_contracts()
ExecutionKind, OrderSide, PositionSide = m.entry.ExecutionKind, m.entry.OrderSide, m.entry.PositionSide
TriggerDirection, TriggerPriceType = m.entry_v2.TriggerDirection, m.entry_v2.TriggerPriceType
Price, Quantity = m.decimal.Price, m.decimal.Quantity
ProtectionEntryContractError = m.protection.ProtectionEntryContractError
ProtectionEntryFacts = m.protection.ProtectionEntryFacts
map_protection_to_canonical_entry = m.protection.map_protection_to_canonical_entry
OrderAction, RiskEffect = m.order.OrderAction, m.order.RiskEffect


UTC = timezone.utc


def facts(**changes):
    values = dict(
        tenant_id=1,
        credential_id=2,
        account_scope="paper-account",
        instrument_id="BTCUSDT",
        market_type="swap",
        actor_id="protection-engine-1",
        side=OrderSide.SELL,
        execution_kind=ExecutionKind.STOP_MARKET,
        position_side=PositionSide.LONG,
        target_position_id="position-1",
        close_quantity=Quantity("0.25"),
        close_all=False,
        trigger_price=Price("100"),
        trigger_direction=TriggerDirection.AT_OR_BELOW,
        trigger_price_type=TriggerPriceType.MARK,
        idempotency_key="protection-case-1",
        correlation_id="correlation-1",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    values.update(changes)
    return ProtectionEntryFacts(**values)


class ProtectionEntryContractTests(unittest.TestCase):
    def test_partial_protection_maps_to_typed_reducing_request(self):
        request = map_protection_to_canonical_entry(facts())
        self.assertEqual(request.action, OrderAction.PROTECTION)
        self.assertEqual(request.risk_effect, RiskEffect.REDUCE_RISK)
        self.assertTrue(request.economic_intent.reduce_only)
        self.assertEqual(request.economic_intent.close_quantity, Quantity("0.25"))
        self.assertFalse(request.economic_intent.close_all)
        self.assertEqual(request.economic_intent.target_position_id, "position-1")
        self.assertEqual(request.economic_intent.trigger_price, Price("100"))
        self.assertEqual(request.economic_intent.trigger_direction, TriggerDirection.AT_OR_BELOW)
        self.assertEqual(request.economic_intent.trigger_price_type, TriggerPriceType.MARK)

    def test_close_all_has_no_fake_quantity(self):
        request = map_protection_to_canonical_entry(
            facts(close_quantity=None, close_all=True, execution_kind=ExecutionKind.STOP_LIMIT, limit_price=Price("101"))
        )
        self.assertIsNone(request.economic_intent.quantity)
        self.assertIsNone(request.economic_intent.close_quantity)
        self.assertTrue(request.economic_intent.close_all)
        self.assertEqual(request.economic_intent.limit_price, Price("101"))

    def test_missing_or_wrong_trigger_facts_fail_closed(self):
        for changes in (
            {"trigger_price": None},
            {"trigger_direction": None},
            {"trigger_price_type": None},
            {"execution_kind": ExecutionKind.MARKET},
            {"execution_kind": ExecutionKind.STOP_LIMIT, "limit_price": None},
        ):
            with self.assertRaises(ProtectionEntryContractError):
                facts(**changes)

    def test_float_and_quantity_truth_source_fail_closed(self):
        with self.assertRaises(ProtectionEntryContractError):
            facts(close_quantity=0.25)
        with self.assertRaises(ProtectionEntryContractError):
            facts(trigger_price=100.0)
        with self.assertRaises(ProtectionEntryContractError):
            facts(close_quantity=Quantity("1"), close_all=True)

    def test_scope_and_typed_facts_are_required(self):
        with self.assertRaises(ProtectionEntryContractError):
            facts(target_position_id="")
        with self.assertRaises(ProtectionEntryContractError):
            facts(position_side="LONG")
        with self.assertRaises(ProtectionEntryContractError):
            facts(tenant_id=True)
        with self.assertRaises(ProtectionEntryContractError):
            map_protection_to_canonical_entry(object())

    def test_actor_source_and_mode_are_safe(self):
        request = map_protection_to_canonical_entry(facts())
        self.assertEqual(request.actor.entry_source.value, "PROTECTION")
        self.assertEqual(request.actor.actor_type.value, "PROTECTION")
        self.assertEqual(request.actor.actor_id, "protection-engine-1")
        self.assertNotIn("LIVE", {member.value for member in type(request.mode)})


if __name__ == "__main__":
    unittest.main()
