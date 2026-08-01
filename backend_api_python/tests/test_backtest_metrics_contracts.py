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
    names = ["app", "app.domain", "app.domain.deterministic_backtest_contracts", "app.domain.backtest_metrics_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/deterministic_backtest_contracts.py"), (names[3], ROOT / "app/domain/backtest_metrics_contracts.py")):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[3]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M = load()


class BacktestMetricsTests(unittest.TestCase):
    def test_metrics_keep_gross_fee_and_funding_separate(self):
        points = tuple(M.BacktestEquityPoint(UTC + timedelta(days=i), Decimal(value)) for i, value in enumerate((100, 110, 105)))
        trades = (M.BacktestTradeResult(UTC + timedelta(days=1), Decimal("12"), Decimal("1"), Decimal("-0.5")), M.BacktestTradeResult(UTC + timedelta(days=2), Decimal("-5"), Decimal("0.5"), Decimal("0.2")))
        result = M.calculate_backtest_metrics(points, trades)
        self.assertEqual(result.gross_pnl, Decimal("7")); self.assertEqual(result.fees, Decimal("1.5")); self.assertEqual(result.funding, Decimal("-0.3")); self.assertEqual(result.net_pnl, Decimal("5.2"))
        self.assertEqual(result.total_return, Decimal("0.05")); self.assertEqual(result.max_drawdown, Decimal("5") / Decimal("110"))

    def test_metrics_reject_float_and_unsorted_points(self):
        with self.assertRaises(M.BacktestMetricsError): M.BacktestEquityPoint(UTC, 1.0)
        points = (M.BacktestEquityPoint(UTC + timedelta(days=1), Decimal("101")), M.BacktestEquityPoint(UTC, Decimal("100")))
        with self.assertRaises(M.BacktestMetricsError): M.calculate_backtest_metrics(points)

    def test_sharpe_is_explicitly_unavailable_for_zero_variance(self):
        points = (M.BacktestEquityPoint(UTC, Decimal("100")), M.BacktestEquityPoint(UTC + timedelta(days=1), Decimal("100")), M.BacktestEquityPoint(UTC + timedelta(days=2), Decimal("100")))
        self.assertIsNone(M.calculate_backtest_metrics(points).sharpe_ratio)

    def test_walk_forward_windows_are_train_then_oos(self):
        windows = M.build_walk_forward_windows(UTC, UTC + timedelta(days=10), train_days=3, test_days=2)
        self.assertEqual(len(windows), 4); self.assertFalse(windows[0].out_of_sample); self.assertTrue(windows[1].out_of_sample); self.assertLessEqual(windows[0].end_at, windows[1].start_at)
        with self.assertRaises(M.BacktestMetricsError): M.build_walk_forward_windows(UTC, UTC + timedelta(days=2), train_days=3, test_days=2)


if __name__ == "__main__": unittest.main()
