from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.backtest_cost_contracts import cost_policy_fingerprint
from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot
from app.domain.backtest_metrics_contracts import BacktestEquityPoint, calculate_backtest_metrics
from app.domain.backtest_report_codec import deserialize_backtest_report, serialize_backtest_report
from app.domain.backtest_report_contracts import build_backtest_report
from app.domain.deterministic_backtest_contracts import BacktestBar, BacktestRunFacts
from app.domain.market_data_quality_contracts import MarketDataEventFact, assess_point_in_time
from app.domain.backtest_cost_contracts import BacktestCostPolicySnapshot


UTC = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _report():
    bars = (
        BacktestBar("BTC_USDT", UTC, UTC + timedelta(minutes=1), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("2"), 0, "dataset-cost"),
        BacktestBar("BTC_USDT", UTC + timedelta(minutes=1), UTC + timedelta(minutes=2), Decimal("100.5"), Decimal("102"), Decimal("100"), Decimal("101"), Decimal("2"), 1, "dataset-cost"),
    )
    events = tuple(MarketDataEventFact(f"event-{i}", "gate", "BTC_USDT", item.open_time, item.close_time, i, "dataset-cost", "rules-v1", f"payload-{i}") for i, item in enumerate(bars))
    quality = assess_point_in_time(events, as_of=UTC + timedelta(minutes=2))
    dataset = BacktestDatasetSnapshot("dataset-cost", "gate", "spot", "BTC_USDT", "rules-v1", bars, quality, UTC + timedelta(minutes=2))
    policy = BacktestCostPolicySnapshot("cost-v1", "USDT", Decimal("0.0002"), Decimal("0.0005"), Decimal("2"), Decimal("3"), Decimal("0.0001"), 28800, "evidence-v1")
    run = BacktestRunFacts("run-cost", "dataset-cost", "rules-v1", "fees-v1", "slippage-v1", Decimal("1000"), "USDT", UTC, UTC + timedelta(minutes=2), cost_policy_fingerprint(policy))
    metrics = calculate_backtest_metrics((BacktestEquityPoint(UTC, Decimal("1000")), BacktestEquityPoint(UTC + timedelta(minutes=2), Decimal("1010"))))
    return build_backtest_report(run, dataset, metrics, report_created_at=UTC + timedelta(minutes=3))


def test_cost_policy_fingerprint_round_trips_as_run_identity():
    report = _report()
    encoded = serialize_backtest_report(report)
    assert encoded["run"]["cost_policy_fingerprint"] == report.run.cost_policy_fingerprint
    decoded = deserialize_backtest_report(encoded)
    assert decoded.run.cost_policy_fingerprint == report.run.cost_policy_fingerprint
    assert decoded.report_fingerprint == report.report_fingerprint
