from __future__ import annotations

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys
import types
import unittest
from contextlib import contextmanager


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _isolated_app_modules():
    prefix = lambda name: name == "app" or name.startswith("app.")
    previous = {name: module for name, module in sys.modules.items() if prefix(name)}
    try:
        yield
    finally:
        for name in list(sys.modules):
            if prefix(name) and name not in previous:
                sys.modules.pop(name, None)
        for name, module in previous.items():
            sys.modules[name] = module


def _bootstrap_fixture_packages():
    app = types.ModuleType("app")
    app.__path__ = [str(ROOT / "app")]
    domain = types.ModuleType("app.domain")
    domain.__path__ = [str(ROOT / "app" / "domain")]
    services = types.ModuleType("app.services")
    services.__path__ = [str(ROOT / "app" / "services")]
    sys.modules.update({"app": app, "app.domain": domain, "app.services": services})


with _isolated_app_modules():
    _bootstrap_fixture_packages()
    helper_path = ROOT / "tests" / "test_deterministic_backtest_service.py"
    helper_spec = importlib.util.spec_from_file_location("backtest_service_fixture", helper_path)
    assert helper_spec and helper_spec.loader
    helper = importlib.util.module_from_spec(helper_spec)
    sys.modules[helper_spec.name] = helper
    helper_spec.loader.exec_module(helper)

    # The nested fixture loader restores the package namespace after loading,
    # while the report service must see the exact same class objects as the
    # helper result.  Rebind those retained module objects before importing
    # the service under test.
    sys.modules.update({
        "app.domain.deterministic_backtest_contracts": helper.BT,
        "app.domain.deterministic_backtest_runner_contracts": helper.BT_RUNNER,
        "app.domain.market_data_quality_contracts": helper.Q,
        "app.domain.backtest_dataset_contracts": helper.DS,
        "app.domain.strategy_library_contracts": helper.SL,
        "app.domain.strategy_signal_contracts": helper.SIGNAL,
        "app.services.deterministic_backtest_service": helper.SVC,
    })

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
        self.assertIsNotNone(report.execution_evidence)
        self.assertEqual(report.execution_evidence.execution_trace_fingerprint, result.trace.trace_fingerprint)
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
