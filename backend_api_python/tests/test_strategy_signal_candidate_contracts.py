"""Pure tests for explicit signal-to-candidate conversion."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module


def _contracts() -> SimpleNamespace:
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts", "app.domain.canonical_entry_v2_contracts",
        "app.domain.entrypoint_v2_binding_contracts", "app.domain.strategy_v2_candidate_contracts",
        "app.domain.strategy_library_contracts", "app.domain.strategy_signal_candidate_contracts",
    )
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        decimals = _load(names[2], ROOT / "app" / "domain" / "decimal_values.py")
        order = _load(names[3], ROOT / "app" / "domain" / "order_contracts.py")
        entry = _load(names[4], ROOT / "app" / "domain" / "canonical_entry_contracts.py")
        entry_v2 = _load(names[5], ROOT / "app" / "domain" / "canonical_entry_v2_contracts.py")
        binding = _load(names[6], ROOT / "app" / "domain" / "entrypoint_v2_binding_contracts.py")
        candidate = _load(names[7], ROOT / "app" / "domain" / "strategy_v2_candidate_contracts.py")
        library = _load(names[8], ROOT / "app" / "domain" / "strategy_library_contracts.py")
        converter = _load(names[9], ROOT / "app" / "domain" / "strategy_signal_candidate_contracts.py")
        return SimpleNamespace(decimals=decimals, order=order, entry=entry, library=library, converter=converter)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = original


C = _contracts()


def _signal(direction):
    strategy = C.library.StrategyDefinition("smc-v1", "1", C.library.StrategyFamily.SMC, "a" * 64, "data-v1", (C.library.StrategyParameterFact("lookback", "3"),))
    prices = (None, None, None) if direction is C.library.SignalDirection.FLAT else (Decimal("100"), Decimal("98"), Decimal("104"))
    return C.library.StrategySignalFact("signal-1", strategy, "BTC_USDT", direction, Decimal("0") if direction is C.library.SignalDirection.FLAT else Decimal("1"), UTC, 2, "dataset-1", *prices)


class StrategySignalCandidateContractTests(unittest.TestCase):
    def test_non_flat_signal_maps_with_explicit_execution_facts(self):
        plan = C.converter.candidate_from_strategy_signal(_signal(C.library.SignalDirection.BUY), strategy_id=7, strategy_run_id=8, action=C.order.OrderAction.OPEN, execution_kind=C.entry.ExecutionKind.MARKET, quantity="1", market_type="SPOT")
        self.assertEqual(plan.side, C.entry.OrderSide.BUY)
        self.assertEqual(plan.market_type, "spot")
        self.assertEqual(plan.signal_id, "signal-1")

    def test_flat_signal_and_missing_execution_facts_fail_closed(self):
        with self.assertRaises(C.converter.StrategySignalCandidateError):
            C.converter.candidate_from_strategy_signal(_signal(C.library.SignalDirection.FLAT), strategy_id=7, strategy_run_id=8, action=C.order.OrderAction.OPEN, execution_kind=C.entry.ExecutionKind.MARKET, quantity="1", market_type="SPOT")
        with self.assertRaises(C.converter.StrategySignalCandidateError):
            C.converter.candidate_from_strategy_signal(_signal(C.library.SignalDirection.BUY), strategy_id=7, strategy_run_id=8, action=C.order.OrderAction.OPEN, execution_kind="MARKET", quantity="1", market_type="SPOT")

    def test_reducing_conversion_keeps_explicit_close_facts(self):
        plan = C.converter.candidate_from_strategy_signal(_signal(C.library.SignalDirection.SELL), strategy_id=7, strategy_run_id=8, action=C.order.OrderAction.CLOSE, execution_kind=C.entry.ExecutionKind.MARKET, quantity=None, market_type="SPOT", reduce_only=True, position_side=C.entry.PositionSide.LONG, target_position_id="position-1", close_all=True)
        self.assertTrue(plan.close_all)
        self.assertIsNone(plan.quantity)
        self.assertEqual(plan.side, C.entry.OrderSide.SELL)


if __name__ == "__main__": unittest.main()
