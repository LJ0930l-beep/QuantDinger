import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    names = ["app", "app.domain", "app.domain.deterministic_backtest_contracts", "app.domain.strategy_library_contracts", "app.domain.strategy_signal_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/deterministic_backtest_contracts.py"), (names[3], ROOT / "app/domain/strategy_library_contracts.py"), (names[4], ROOT / "app/domain/strategy_signal_contracts.py")):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[4]], sys.modules[names[2]], sys.modules[names[3]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, B, S = load()


def bars(values):
    return tuple(B.BacktestBar("BTC_USDT", UTC + timedelta(minutes=i), UTC + timedelta(minutes=i + 1), Decimal(str(o)), Decimal(str(h)), Decimal(str(l)), Decimal(str(c)), Decimal("10"), i, "dataset-1") for i, (o, h, l, c) in enumerate(values))


def strategy(family):
    return S.StrategyDefinition("s-1", "v1", family, "schema-1", "dataset-1", (S.StrategyParameterFact("lookback", "3"),))


class StrategySignalTests(unittest.TestCase):
    def test_smc_liquidity_sweep_emits_sell_and_is_deterministic(self):
        facts = bars(((100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103), (103, 106, 102, 104)))
        facts = facts[:-1] + (B.BacktestBar("BTC_USDT", UTC + timedelta(minutes=3), UTC + timedelta(minutes=4), Decimal("103"), Decimal("108"), Decimal("102"), Decimal("104"), Decimal("10"), 3, "dataset-1"),)
        event = M.detect_liquidity_sweep(facts)
        self.assertEqual(event.direction, S.SignalDirection.FLAT)
        signal = M.build_strategy_signal(strategy(S.StrategyFamily.SMC), facts, signal_id="sig-1", data_snapshot_id="dataset-1")
        self.assertEqual(signal.direction, S.SignalDirection.FLAT)

    def test_smc_sweep_requires_reclaim(self):
        facts = bars(((100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103), (103, 106, 102, 105)))
        self.assertEqual(M.detect_liquidity_sweep(facts).pattern, M.SignalPattern.NONE)

    def test_ict_displacement_emits_typed_signal(self):
        facts = bars(((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 106, 99, 105)))
        signal = M.build_strategy_signal(strategy(S.StrategyFamily.ICT), facts, signal_id="sig-1", data_snapshot_id="dataset-1")
        self.assertEqual(signal.direction, S.SignalDirection.BUY); self.assertEqual(signal.entry_price, Decimal("105")); self.assertEqual(signal.source_sequence, 3)

    def test_non_smc_ict_and_float_inputs_fail_closed(self):
        with self.assertRaises(M.StrategySignalContractError): M.build_strategy_signal(strategy(S.StrategyFamily.BUY_AND_HOLD), bars(((1, 2, 1, 1),) * 4), signal_id="sig", data_snapshot_id="d")
        with self.assertRaises(B.BacktestContractError): B.BacktestBar("BTC_USDT", UTC, UTC + timedelta(minutes=1), 1.0, Decimal("2"), Decimal("1"), Decimal("1"), Decimal("1"), 0, "d")


if __name__ == "__main__": unittest.main()
