"""Offline Gate TestNet execution lifecycle tests."""

from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import TestCase
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contracts() -> SimpleNamespace:
    names = (
        "app", "app.domain", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_vertical_read_contracts", "app.domain.gate_testnet_execution_contracts",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        multi = _load(names[2], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        vertical = _load(names[3], ROOT / "app" / "domain" / "gate_vertical_read_contracts.py")
        execution = _load(names[4], ROOT / "app" / "domain" / "gate_testnet_execution_contracts.py")
        return SimpleNamespace(multi=multi, vertical=vertical, execution=execution)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


C = _contracts()


def _service_class():
    names = ("app", "app.domain", "app.services", "app.domain.gate_testnet_execution_contracts", "app.domain.gate_vertical_read_contracts", "app.domain.multi_asset_capability_contracts", "app.services.gate_testnet_execution_rehearsal_service")
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        sys.modules[names[3]] = C.execution
        sys.modules[names[4]] = C.vertical
        sys.modules[names[5]] = C.multi
        module = _load(names[6], ROOT / "app" / "services" / "gate_testnet_execution_rehearsal_service.py")
        return module.GateTestnetExecutionRehearsalService
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class GateTestnetExecutionContractTests(TestCase):
    def _request(self, **changes):
        values = dict(
            instrument_id="BTC_USDT",
            market_type=C.multi.AssetMarketType.PERPETUAL,
            account_scope="fixture-testnet",
            side=C.vertical.GateOrderSide.BUY,
            quantity=Decimal("1"),
            reference_price=Decimal("100"),
            execution_kind=C.execution.GateExecutionKind.MARKET,
            fill_ratio=Decimal("1"),
            fee_rate=Decimal("0.001"),
            fee_asset="USDT",
            client_order_id="fixture-client-order-1",
            environment=C.multi.CapabilityEnvironment.TESTNET,
        )
        values.update(changes)
        return C.execution.GateTestnetExecutionRequest(**values)

    def test_full_fill_is_deterministic_and_explicitly_non_live(self):
        first = C.execution.simulate_gate_testnet_execution(self._request())
        second = C.execution.simulate_gate_testnet_execution(self._request())
        self.assertEqual(first.lifecycle_fingerprint, second.lifecycle_fingerprint)
        self.assertEqual(first.order.status, C.vertical.GateOrderStatus.FILLED)
        self.assertEqual(first.order.filled_quantity, Decimal("1"))
        self.assertEqual(first.fee_amount, Decimal("0.100"))
        self.assertFalse(first.network_access)
        self.assertFalse(first.writes_enabled)
        self.assertFalse(first.live_enabled)

    def test_partial_and_zero_fill_states_are_typed(self):
        partial = C.execution.simulate_gate_testnet_execution(self._request(fill_ratio=Decimal("0.4")))
        empty = C.execution.simulate_gate_testnet_execution(self._request(fill_ratio=Decimal("0")))
        self.assertEqual(partial.order.status, C.vertical.GateOrderStatus.PARTIALLY_FILLED)
        self.assertEqual(partial.order.filled_quantity, Decimal("0.4"))
        self.assertEqual(len(partial.fills), 1)
        self.assertEqual(empty.order.status, C.vertical.GateOrderStatus.CANCELLED)
        self.assertEqual(empty.order.filled_quantity, Decimal("0"))
        self.assertEqual(empty.fills, ())

    def test_contract_rejects_float_and_wrong_environment(self):
        with self.assertRaises(C.execution.GateTestnetExecutionContractError):
            self._request(quantity=1.0)
        with self.assertRaises(C.execution.GateTestnetExecutionContractError):
            self._request(environment=C.multi.CapabilityEnvironment.PAPER)

    def test_limit_requires_price_and_market_rejects_price(self):
        with self.assertRaises(C.execution.GateTestnetExecutionContractError):
            self._request(execution_kind=C.execution.GateExecutionKind.LIMIT)
        with self.assertRaises(C.execution.GateTestnetExecutionContractError):
            self._request(limit_price=Decimal("99"))

    def test_stop_orders_require_typed_trigger_facts_and_replay_deterministically(self):
        with self.assertRaises(C.execution.GateTestnetExecutionContractError):
            self._request(execution_kind=C.execution.GateExecutionKind.STOP_MARKET)
        triggered = self._request(
            execution_kind=C.execution.GateExecutionKind.STOP_MARKET,
            trigger_price=Decimal("99"),
            trigger_direction=C.execution.GateTriggerDirection.AT_OR_ABOVE,
            trigger_price_type=C.execution.GateTriggerPriceType.LAST,
        )
        first = C.execution.simulate_gate_testnet_execution(triggered)
        second = C.execution.simulate_gate_testnet_execution(triggered)
        self.assertEqual(first.lifecycle_fingerprint, second.lifecycle_fingerprint)
        self.assertEqual(first.order.status, C.vertical.GateOrderStatus.FILLED)
        self.assertEqual(first.to_public_dict()["trigger_direction"], "AT_OR_ABOVE")

    def test_untriggered_stop_remains_open_without_fill(self):
        pending = self._request(
            execution_kind=C.execution.GateExecutionKind.STOP_LIMIT,
            limit_price=Decimal("98"),
            trigger_price=Decimal("101"),
            trigger_direction=C.execution.GateTriggerDirection.AT_OR_ABOVE,
            trigger_price_type=C.execution.GateTriggerPriceType.MARK,
        )
        receipt = C.execution.simulate_gate_testnet_execution(pending)
        self.assertEqual(receipt.order.status, C.vertical.GateOrderStatus.OPEN)
        self.assertEqual(receipt.order.filled_quantity, Decimal("0"))
        self.assertEqual(receipt.fills, ())

    def test_service_returns_fixture_only_receipt(self):
        receipt = _service_class()().run(fill_ratio="0.5")
        public = receipt.to_public_dict()
        self.assertEqual(public["environment"], "testnet")
        self.assertFalse(public["network_access"])
        self.assertFalse(public["writes_enabled"])
        self.assertFalse(public["live_enabled"])
        self.assertEqual(public["order"]["status"], "partially_filled")

    def test_replayed_fill_scope_cannot_be_attached_to_another_order(self):
        request = self._request(fill_ratio=Decimal("0.5"))
        receipt = C.execution.simulate_gate_testnet_execution(request)
        bad_fill = C.vertical.GateFillFact(
            "gate", request.market_type, request.account_scope, "ETH_USDT",
            receipt.order.exchange_order_id, "fixture-fill-other-scope", request.side,
            Decimal("0.5"), Decimal("100"), request.fee_asset, Decimal("0.05"),
            request.observed_at, "fixture-fill-event-other-scope",
        )
        with self.assertRaises(C.execution.GateTestnetExecutionContractError):
            C.execution.GateTestnetExecutionReceipt(
                request, C.execution.GateExecutionDisposition.ACCEPTED,
                receipt.order, (bad_fill,), receipt.fee_amount,
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
