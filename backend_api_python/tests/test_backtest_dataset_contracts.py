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
    names = ["app", "app.domain", "app.domain.deterministic_backtest_contracts", "app.domain.market_data_quality_contracts", "app.domain.backtest_dataset_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/deterministic_backtest_contracts.py"), (names[3], ROOT / "app/domain/market_data_quality_contracts.py"), (names[4], ROOT / "app/domain/backtest_dataset_contracts.py")):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[4]], sys.modules[names[2]], sys.modules[names[3]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, B, Q = load()


def bar(seq: int, start: datetime):
    return B.BacktestBar("BTC_USDT", start, start + timedelta(minutes=1), Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("2"), seq, "dataset-1")


def assessment(count: int):
    events = tuple(Q.MarketDataEventFact(f"event-{i}", "gate", "BTC_USDT", UTC + timedelta(minutes=i + 1), UTC + timedelta(minutes=i + 1), i, "dataset-1", "rules-1", f"payload-{i}") for i in range(count))
    return Q.DataQualityAssessment(Q.DataQualityStatus.COMPLETE, events, (), UTC + timedelta(hours=1), "assessment")


class BacktestDatasetTests(unittest.TestCase):
    def test_complete_snapshot_binds_quality_and_bars(self):
        bars = (bar(0, UTC + timedelta(minutes=1)), bar(1, UTC + timedelta(minutes=2)))
        dataset = M.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-1", bars, assessment(2), UTC + timedelta(hours=1))
        self.assertEqual(dataset_fingerprint := M.dataset_fingerprint(dataset), dataset.dataset_fingerprint)
        self.assertEqual(len(dataset.bars), 2)
        self.assertEqual(dataset_fingerprint, M.dataset_fingerprint(dataset))

    def test_incomplete_quality_rejected(self):
        bars = (bar(0, UTC + timedelta(minutes=1)),)
        bad = Q.DataQualityAssessment(Q.DataQualityStatus.LATE, (), ("event-0",), UTC + timedelta(hours=1), "assessment")
        with self.assertRaises(M.BacktestDatasetError): M.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-1", bars, bad, UTC + timedelta(hours=1))

    def test_missing_bar_coverage_or_wrong_snapshot_rejected(self):
        bars = (bar(0, UTC + timedelta(minutes=1)), bar(1, UTC + timedelta(minutes=2)))
        with self.assertRaises(M.BacktestDatasetError): M.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-1", bars, assessment(1), UTC + timedelta(hours=1))
        wrong_instrument = B.BacktestBar("ETH_USDT", UTC, UTC + timedelta(minutes=1), Decimal("1"), Decimal("2"), Decimal("1"), Decimal("1"), Decimal("1"), 0, "dataset-1")
        with self.assertRaises(M.BacktestDatasetError): M.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-1", (wrong_instrument,), assessment(1), UTC + timedelta(hours=1))

    def test_future_fact_rejected_and_float_never_enters(self):
        future_bar = bar(0, UTC + timedelta(hours=2))
        with self.assertRaises(M.BacktestDatasetError): M.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-1", (future_bar,), assessment(1), UTC + timedelta(hours=1))
        with self.assertRaises(B.BacktestContractError): B.BacktestBar("BTC_USDT", UTC, UTC + timedelta(minutes=1), 100.0, Decimal("101"), Decimal("99"), Decimal("100"), Decimal("1"), 0, "dataset-1")


if __name__ == "__main__": unittest.main()
