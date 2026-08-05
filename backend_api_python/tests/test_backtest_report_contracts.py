"""Pure tests for immutable backtest report assembly."""

from __future__ import annotations

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
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module


def _contracts() -> SimpleNamespace:
    names = (
        "app", "app.domain", "app.domain.deterministic_backtest_contracts", "app.domain.market_data_quality_contracts",
        "app.domain.backtest_dataset_contracts", "app.domain.backtest_metrics_contracts", "app.domain.backtest_report_contracts",
        "app.domain.backtest_report_codec",
    )
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        backtest = _load(names[2], ROOT / "app" / "domain" / "deterministic_backtest_contracts.py")
        quality = _load(names[3], ROOT / "app" / "domain" / "market_data_quality_contracts.py")
        dataset = _load(names[4], ROOT / "app" / "domain" / "backtest_dataset_contracts.py")
        metrics = _load(names[5], ROOT / "app" / "domain" / "backtest_metrics_contracts.py")
        report = _load(names[6], ROOT / "app" / "domain" / "backtest_report_contracts.py")
        codec = _load(names[7], ROOT / "app" / "domain" / "backtest_report_codec.py")
        return SimpleNamespace(backtest=backtest, quality=quality, dataset=dataset, metrics=metrics, report=report, codec=codec)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = original


C = _contracts()


def _dataset():
    bars = (
        C.backtest.BacktestBar("BTC_USDT", UTC, UTC + timedelta(minutes=1), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("2"), 0, "dataset-1"),
        C.backtest.BacktestBar("BTC_USDT", UTC + timedelta(minutes=1), UTC + timedelta(minutes=2), Decimal("100.5"), Decimal("102"), Decimal("100"), Decimal("101"), Decimal("2"), 1, "dataset-1"),
    )
    events = tuple(C.quality.MarketDataEventFact(f"event-{i}", "gate", "BTC_USDT", bar.open_time, bar.close_time, i, "dataset-1", "rules-v1", f"payload-{i}") for i, bar in enumerate(bars))
    quality = C.quality.assess_point_in_time(events, as_of=UTC + timedelta(minutes=2))
    return C.dataset.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-v1", bars, quality, UTC + timedelta(minutes=2), "1m")


def _report():
    run = C.backtest.BacktestRunFacts("run-1", "dataset-1", "rules-v1", "fees-v1", "slippage-v1", Decimal("1000"), "USDT", UTC, UTC + timedelta(minutes=2))
    metrics = C.metrics.calculate_backtest_metrics((C.metrics.BacktestEquityPoint(UTC, Decimal("1000")), C.metrics.BacktestEquityPoint(UTC + timedelta(minutes=2), Decimal("1010"))))
    return C.report.build_backtest_report(run, _dataset(), metrics, report_created_at=UTC + timedelta(minutes=3))


class BacktestReportContractTests(unittest.TestCase):
    def test_report_is_deterministic_and_serializes_decimal_as_text(self):
        first, second = _report(), _report()
        self.assertEqual(first.report_fingerprint, second.report_fingerprint)
        payload = first.to_public_dict()
        self.assertEqual(payload["final_equity"], "1010")
        self.assertNotIn("e+", repr(payload))

    def test_dataset_identity_and_clock_are_bound(self):
        report = _report()
        bad_run = C.backtest.BacktestRunFacts("run-2", "other-dataset", "rules-v1", "fees-v1", "slippage-v1", Decimal("1000"), "USDT", UTC, UTC + timedelta(minutes=2))
        with self.assertRaises(C.report.BacktestReportError):
            C.report.build_backtest_report(bad_run, report.dataset, report.metrics, report_created_at=UTC + timedelta(minutes=3))
        late_run = C.backtest.BacktestRunFacts("run-3", "dataset-1", "rules-v1", "fees-v1", "slippage-v1", Decimal("1000"), "USDT", UTC, UTC + timedelta(minutes=3))
        with self.assertRaises(C.report.BacktestReportError):
            C.report.build_backtest_report(late_run, report.dataset, report.metrics, report_created_at=UTC + timedelta(minutes=4))

    def test_windows_must_fit_run_clock(self):
        report = _report()
        window = C.metrics.WalkForwardWindow("w1", UTC, UTC + timedelta(minutes=3), True)
        with self.assertRaises(C.report.BacktestReportError):
            C.report.build_backtest_report(report.run, report.dataset, report.metrics, (window,), report_created_at=UTC + timedelta(minutes=4))

    def test_canonical_codec_round_trips_and_keeps_decimal_text(self):
        report = _report()
        encoded = C.codec.serialize_backtest_report(report)
        self.assertEqual(encoded["contract_version"], "backtest-report-v1")
        self.assertEqual(encoded["metrics"]["final_equity"], "1010")
        self.assertEqual(encoded["dataset"]["timeframe"], "1m")
        self.assertEqual(C.codec.deserialize_backtest_report(encoded).report_fingerprint, report.report_fingerprint)
        self.assertNotIn("e+", repr(encoded))

    def test_execution_evidence_round_trips_without_collapsing_fee_assets(self):
        report = _report()
        evidence = C.report.BacktestExecutionEvidence(
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "USDT",
            (("BTC", Decimal("0.001")), ("USDT", Decimal("1.25"))),
            Decimal("-0.25"),
            ("fill-1", "fill-2"),
        )
        report = C.report.build_backtest_report(
            report.run,
            report.dataset,
            report.metrics,
            report.walk_forward_windows,
            report_created_at=report.report_created_at,
            execution_evidence=evidence,
        )
        encoded = C.codec.serialize_backtest_report(report)
        decoded = C.codec.deserialize_backtest_report(encoded)
        self.assertEqual(decoded.report_fingerprint, report.report_fingerprint)
        self.assertEqual(decoded.execution_evidence.fees_by_asset, evidence.fees_by_asset)
        self.assertEqual(decoded.execution_evidence.applied_fill_ids, ("fill-1", "fill-2"))

    def test_codec_rejects_float_and_tampered_fingerprint(self):
        report = _report()
        encoded = C.codec.serialize_backtest_report(report)
        encoded["metrics"]["final_equity"] = 1010.0
        with self.assertRaises(C.codec.BacktestReportCodecError):
            C.codec.deserialize_backtest_report(encoded)
        encoded = C.codec.serialize_backtest_report(report)
        encoded["report_fingerprint"] = "0" * 64
        with self.assertRaises(C.codec.BacktestReportCodecError):
            C.codec.deserialize_backtest_report(encoded)


if __name__ == "__main__": unittest.main()
