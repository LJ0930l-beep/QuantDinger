import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]; UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    name = "app.domain.strategy_library_contracts"; names = ["app", "app.domain", name]; old = {n: sys.modules.get(n) for n in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domain" / "strategy_library_contracts.py"); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
    finally:
        for n in reversed(names):
            if old[n] is None: sys.modules.pop(n, None)
            else: sys.modules[n] = old[n]


M = load()


def definition():
    return M.StrategyDefinition("smc-core", "v1", M.StrategyFamily.SMC, "schema-1", "data-1", (M.StrategyParameterFact("lookback", "20"),))


class StrategyLibraryTests(unittest.TestCase):
    def test_definition_and_signal_are_typed_and_immutable(self):
        signal = M.StrategySignalFact("sig-1", definition(), "BTC_USDT", M.SignalDirection.BUY, Decimal("0.75"), UTC, 1, "snapshot-1", Decimal("100"), Decimal("95"), Decimal("110"))
        with self.assertRaises((AttributeError, TypeError)): signal.confidence = Decimal("1")
        self.assertEqual(M.strategy_fingerprint(signal), M.strategy_fingerprint(signal))

    def test_decimal_and_confidence_fail_closed(self):
        with self.assertRaises(M.StrategyLibraryError): M.StrategySignalFact("s", definition(), "BTC", M.SignalDirection.BUY, 0.5, UTC, 1, "d")
        with self.assertRaises(M.StrategyLibraryError): M.StrategySignalFact("s", definition(), "BTC", M.SignalDirection.BUY, Decimal("1.1"), UTC, 1, "d")

    def test_flat_signal_has_no_trade_prices(self):
        flat = M.StrategySignalFact("s", definition(), "BTC_USDT", M.SignalDirection.FLAT, Decimal("0"), UTC, 1, "d")
        self.assertEqual(flat.direction, M.SignalDirection.FLAT)
        with self.assertRaises(M.StrategyLibraryError): M.StrategySignalFact("s", definition(), "BTC_USDT", M.SignalDirection.FLAT, Decimal("0"), UTC, 1, "d", Decimal("100"))

    def test_parameter_names_unique_and_scope_changes_fingerprint(self):
        with self.assertRaises(M.StrategyLibraryError): M.StrategyDefinition("s", "v", M.StrategyFamily.ICT, "schema", "data", (M.StrategyParameterFact("x", "1"), M.StrategyParameterFact("x", "2")))
        left = M.StrategySignalFact("s", definition(), "BTC_USDT", M.SignalDirection.BUY, Decimal("0.5"), UTC, 1, "d")
        right = M.StrategySignalFact("s", definition(), "ETH_USDT", M.SignalDirection.BUY, Decimal("0.5"), UTC, 1, "d")
        self.assertNotEqual(M.strategy_fingerprint(left), M.strategy_fingerprint(right))


if __name__ == "__main__": unittest.main()
