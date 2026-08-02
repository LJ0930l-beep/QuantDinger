from __future__ import annotations

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
helper_path = ROOT / "tests" / "test_deterministic_backtest_service.py"
helper_spec = importlib.util.spec_from_file_location("backtest_service_fixture", helper_path)
assert helper_spec and helper_spec.loader
helper = importlib.util.module_from_spec(helper_spec)
sys.modules[helper_spec.name] = helper
helper_spec.loader.exec_module(helper)

for name, relative in (
    ("app.domain.backtest_metrics_contracts", "domain/backtest_metrics_contracts.py"),
    ("app.domain.backtest_report_contracts", "domain/backtest_report_contracts.py"),
    ("app.services.deterministic_backtest_report_service", "services/deterministic_backtest_report_service.py"),
):
    path = ROOT / "app" / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

M = sys.modules["app.domain.backtest_metrics_contracts"]
S = sys.modules["app.services.deterministic_backtest_report_service"]
START = helper.START


class DeterministicBacktestReportServiceTests(unittest.TestCase):
    def test_builds_report_from_explicit_equity_and_trade_facts(self):
        result = helper.SVC.DeterministicBacktestService().run(
            helper._run(), helper._dataset(), helper._strategy(), order_quantity=Decimal("1")
        )
        points = tuple(
            M.BacktestEquityPoint(START + timedelta(minutes=index), Decimal(value))
            for index, value in enumerate((1000, 1005, 1010))
        )
        trades = (M.BacktestTradeResult(START + timedelta(minutes=4), Decimal("10"), Decimal("1"), Decimal("0")),)
        report = S.DeterministicBacktestReportService().build(
            result, points, trades, report_created_at=START + timedelta(hours=2)
        )
        self.assertEqual(report.metrics.net_pnl, Decimal("9"))
        self.assertEqual(report.dataset.dataset_snapshot_id, "dataset-1")
        self.assertEqual(len(report.report_fingerprint), 64)

    def test_does_not_accept_untyped_equity_or_trade_facts(self):
        result = helper.SVC.DeterministicBacktestService().run(
            helper._run(), helper._dataset(), helper._strategy(), order_quantity=Decimal("1")
        )
        with self.assertRaises(S.DeterministicBacktestReportError):
            S.DeterministicBacktestReportService().build(
                result, (object(),), report_created_at=START + timedelta(hours=2)
            )


if __name__ == "__main__":
    unittest.main()
