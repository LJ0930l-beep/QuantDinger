from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import uuid4
import importlib.util
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules():
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_vertical_read_contracts", "app.domain.venue_order_contracts",
        "app.domain.immutable_fill_ledger", "app.domain.gate_testnet_execution_contracts",
        "app.domain.gate_testnet_ledger_contracts",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        _load(names[2], ROOT / "app" / "domain" / "decimal_values.py")
        multi = _load(names[3], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        vertical = _load(names[4], ROOT / "app" / "domain" / "gate_vertical_read_contracts.py")
        _load(names[5], ROOT / "app" / "domain" / "venue_order_contracts.py")
        ledger = _load(names[6], ROOT / "app" / "domain" / "immutable_fill_ledger.py")
        execution = _load(names[7], ROOT / "app" / "domain" / "gate_testnet_execution_contracts.py")
        bridge = _load(names[8], ROOT / "app" / "domain" / "gate_testnet_ledger_contracts.py")
        return multi, vertical, ledger, execution, bridge
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


MULTI, VERTICAL, LEDGER, EXECUTION, BRIDGE = _modules()


class GateTestnetLedgerContractTests(unittest.TestCase):
    def _request(self, **changes):
        values = dict(
            instrument_id="BTC_USDT", market_type=MULTI.AssetMarketType.SPOT,
            account_scope="paper-gate-testnet", side=VERTICAL.GateOrderSide.BUY,
            quantity=Decimal("0.1"), reference_price=Decimal("65000"),
            execution_kind=EXECUTION.GateExecutionKind.MARKET,
            client_order_id="case-gate-ledger-1",
        )
        values.update(changes)
        return EXECUTION.GateTestnetExecutionRequest(**values)

    def _scope(self, *, economic_order_id=None, assets=None, fee_prices=None):
        return BRIDGE.GateTestnetLedgerScope(
            economic_order_id=economic_order_id or str(uuid4()),
            assets=assets or LEDGER.InstrumentAssetScope("BTC_USDT", "BTC", "USDT"),
            valuation_ccy="USDT", fee_valuation_prices=fee_prices,
        )

    def test_receipt_maps_to_immutable_fill_and_fee_facts(self):
        receipt = EXECUTION.simulate_gate_testnet_execution(self._request())
        fills = BRIDGE.build_gate_testnet_ledger_inputs(receipt, scope=self._scope())
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].quote_quantity.origin.value, "DERIVED")
        self.assertEqual(fills[0].fee_components[0].valuation_evidence.source.value, "IDENTITY")

    def test_cancelled_receipt_has_no_synthetic_fill(self):
        receipt = EXECUTION.simulate_gate_testnet_execution(self._request(fill_ratio=Decimal("0")))
        self.assertEqual(BRIDGE.build_gate_testnet_ledger_inputs(receipt, scope=self._scope()), ())

    def test_cross_scope_is_rejected_before_persistence(self):
        receipt = EXECUTION.simulate_gate_testnet_execution(self._request())
        with self.assertRaises(BRIDGE.GateTestnetLedgerContractError):
            BRIDGE.build_gate_testnet_ledger_inputs(receipt, scope=self._scope(
                assets=LEDGER.InstrumentAssetScope("ETH_USDT", "ETH", "USDT")))

    def test_non_quote_fee_requires_explicit_valuation(self):
        receipt = EXECUTION.simulate_gate_testnet_execution(self._request(fee_asset="GATE"))
        with self.assertRaises(BRIDGE.GateTestnetLedgerContractError):
            BRIDGE.build_gate_testnet_ledger_inputs(receipt, scope=self._scope())

    def test_non_quote_fee_accepts_explicit_price(self):
        receipt = EXECUTION.simulate_gate_testnet_execution(self._request(fee_asset="GATE"))
        fills = BRIDGE.build_gate_testnet_ledger_inputs(
            receipt, scope=self._scope(fee_prices={"GATE": LEDGER.Price("0.5")})
        )
        self.assertEqual(fills[0].fee_components[0].valuation_evidence.price.to_string(), "0.5")


if __name__ == "__main__":
    unittest.main()
