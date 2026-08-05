"""Read-only backtest result service tests."""

from datetime import datetime, timedelta, timezone
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
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contracts() -> SimpleNamespace:
    names = (
        "app",
        "app.domain",
        "app.services",
        "app.domain.deterministic_backtest_contracts",
        "app.domain.market_data_quality_contracts",
        "app.domain.backtest_dataset_contracts",
        "app.domain.backtest_metrics_contracts",
        "app.domain.backtest_report_contracts",
        "app.domain.backtest_result_contracts",
        "app.services.backtest_result_service",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services")
        services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app
        sys.modules["app.domain"] = domain
        sys.modules["app.services"] = services
        backtest = _load(names[3], ROOT / "app" / "domain" / "deterministic_backtest_contracts.py")
        quality = _load(names[4], ROOT / "app" / "domain" / "market_data_quality_contracts.py")
        dataset = _load(names[5], ROOT / "app" / "domain" / "backtest_dataset_contracts.py")
        metrics = _load(names[6], ROOT / "app" / "domain" / "backtest_metrics_contracts.py")
        report = _load(names[7], ROOT / "app" / "domain" / "backtest_report_contracts.py")
        result = _load(names[8], ROOT / "app" / "domain" / "backtest_result_contracts.py")
        service = _load(names[9], ROOT / "app" / "services" / "backtest_result_service.py")
        return SimpleNamespace(backtest=backtest, quality=quality, dataset=dataset, metrics=metrics, report=report, result=result, service=service)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


C = _contracts()


def _report():
    bars = (
        C.backtest.BacktestBar("BTC_USDT", UTC, UTC + timedelta(minutes=1), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("2"), 0, "dataset-result"),
        C.backtest.BacktestBar("BTC_USDT", UTC + timedelta(minutes=1), UTC + timedelta(minutes=2), Decimal("100.5"), Decimal("102"), Decimal("100"), Decimal("101"), Decimal("2"), 1, "dataset-result"),
    )
    events = tuple(
        C.quality.MarketDataEventFact(f"event-{i}", "gate", "BTC_USDT", bar.open_time, bar.close_time, i, "dataset-result", "rules-v1", f"payload-{i}")
        for i, bar in enumerate(bars)
    )
    quality = C.quality.assess_point_in_time(events, as_of=UTC + timedelta(minutes=2))
    dataset = C.dataset.BacktestDatasetSnapshot("dataset-result", "gate", "spot", "BTC_USDT", "rules-v1", bars, quality, UTC + timedelta(minutes=2))
    run = C.backtest.BacktestRunFacts("run-result", "dataset-result", "rules-v1", "fees-v1", "slippage-v1", Decimal("1000"), "USDT", UTC, UTC + timedelta(minutes=2))
    metrics = C.metrics.calculate_backtest_metrics((C.metrics.BacktestEquityPoint(UTC, Decimal("1000")), C.metrics.BacktestEquityPoint(UTC + timedelta(minutes=2), Decimal("1010"))))
    return C.report.build_backtest_report(run, dataset, metrics, report_created_at=UTC + timedelta(minutes=3))


class BacktestResultServiceTests(unittest.TestCase):
    def test_missing_provider_is_unavailable_without_facts(self):
        response = C.service.BacktestResultService().read_response()
        self.assertEqual(response.http_status, 503)
        self.assertEqual(response.body["status"], C.result.BacktestResultStatus.UNAVAILABLE.value)
        self.assertNotIn("report", response.body)

    def test_unauthorized_view_carries_no_report_facts(self):
        response = C.service.BacktestResultService(lambda: _report()).read_response(authorized=False)
        self.assertEqual(response.http_status, 401)
        self.assertEqual(response.body["status"], C.result.BacktestResultStatus.UNAUTHORIZED.value)
        self.assertNotIn("report", response.body)

    def test_ready_result_is_deterministic_and_decimal_safe(self):
        report = _report()
        first = C.service.BacktestResultService(lambda: report).read_response()
        second = C.service.BacktestResultService(lambda: report).read_response()
        self.assertEqual(first.http_status, 200)
        self.assertEqual(first.body, second.body)
        self.assertEqual(first.body["status"], C.result.BacktestResultStatus.READY.value)
        self.assertEqual(first.body["report"]["final_equity"], "1010")
        self.assertNotIn("e+", repr(first.body))

    def test_provider_failure_is_typed_and_does_not_leak_payload(self):
        def broken():
            raise RuntimeError("secret provider payload")

        with self.assertRaisesRegex(C.service.BacktestResultServiceError, "provider failed"):
            C.service.BacktestResultService(broken).read_view()

    def test_provider_must_return_immutable_report(self):
        with self.assertRaisesRegex(C.service.BacktestResultServiceError, "invalid backtest facts"):
            C.service.BacktestResultService(lambda: {"run_id": "not-a-report"}).read_view()


if __name__ == "__main__":
    unittest.main()
