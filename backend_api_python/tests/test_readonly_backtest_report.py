"""Unit coverage for the canonical SELECT-only backtest report reader."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
import json
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


def _modules() -> SimpleNamespace:
    names = (
        "app", "app.domain", "app.domain.deterministic_backtest_contracts",
        "app.domain.market_data_quality_contracts", "app.domain.backtest_dataset_contracts",
        "app.domain.backtest_metrics_contracts", "app.domain.backtest_report_contracts",
        "app.domain.backtest_report_codec", "app.services", "app.services.readonly_backtest_report_repository",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        backtest = _load(names[2], ROOT / "app/domain/deterministic_backtest_contracts.py")
        quality = _load(names[3], ROOT / "app/domain/market_data_quality_contracts.py")
        dataset = _load(names[4], ROOT / "app/domain/backtest_dataset_contracts.py")
        metrics = _load(names[5], ROOT / "app/domain/backtest_metrics_contracts.py")
        report = _load(names[6], ROOT / "app/domain/backtest_report_contracts.py")
        codec = _load(names[7], ROOT / "app/domain/backtest_report_codec.py")
        repository = _load(names[9], ROOT / "app/services/readonly_backtest_report_repository.py")
        return SimpleNamespace(backtest=backtest, quality=quality, dataset=dataset, metrics=metrics, report=report, codec=codec, repository=repository)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


C = _modules()


def _report():
    bars = (
        C.backtest.BacktestBar("BTC_USDT", UTC, UTC + timedelta(minutes=1), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("2"), 0, "dataset-1"),
        C.backtest.BacktestBar("BTC_USDT", UTC + timedelta(minutes=1), UTC + timedelta(minutes=2), Decimal("100.5"), Decimal("102"), Decimal("100"), Decimal("101"), Decimal("2"), 1, "dataset-1"),
    )
    events = tuple(C.quality.MarketDataEventFact(f"event-{i}", "gate", "BTC_USDT", bar.open_time, bar.close_time, i, "dataset-1", "rules-v1", f"payload-{i}") for i, bar in enumerate(bars))
    quality = C.quality.assess_point_in_time(events, as_of=UTC + timedelta(minutes=2))
    dataset = C.dataset.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-v1", bars, quality, UTC + timedelta(minutes=2))
    run = C.backtest.BacktestRunFacts("run-1", "dataset-1", "rules-v1", "fees-v1", "slippage-v1", Decimal("1000"), "USDT", UTC, UTC + timedelta(minutes=2))
    metrics = C.metrics.calculate_backtest_metrics((C.metrics.BacktestEquityPoint(UTC, Decimal("1000")), C.metrics.BacktestEquityPoint(UTC + timedelta(minutes=2), Decimal("1010"))))
    return C.report.build_backtest_report(run, dataset, metrics, report_created_at=UTC + timedelta(minutes=3))


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.query = None
        self.closed = False

    def execute(self, query, params=()):
        self.query = (query, params)

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)

    def cursor(self):
        return self.cursor_value


class ReadonlyBacktestRepositoryTests(unittest.TestCase):
    def test_canonical_report_is_read_and_cursor_closes(self):
        report = _report()
        connection = _Connection((7, 11, "success", json.dumps(C.codec.serialize_backtest_report(report)), UTC + timedelta(minutes=4)))
        actual = C.repository.ReadonlyBacktestReportRepository().read(connection, user_id=11, run_id=7)
        self.assertEqual(actual.report_fingerprint, report.report_fingerprint)
        self.assertTrue(connection.cursor_value.closed)
        self.assertIn("SELECT", connection.cursor_value.query[0])

    def test_legacy_or_tampered_result_fails_closed(self):
        connection = _Connection((7, 11, "success", json.dumps({"closedTrades": []}), UTC))
        with self.assertRaises(C.repository.ReadonlyBacktestReportRepositoryError):
            C.repository.ReadonlyBacktestReportRepository().read(connection, user_id=11, run_id=7)


if __name__ == "__main__":
    unittest.main()
