from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.backtest_cost_contracts import BacktestCostPolicySnapshot, BacktestLiquidityRole, cost_policy_fingerprint
from app.domain.deterministic_backtest_contracts import BacktestBar, BacktestExecutionKind, BacktestOrderIntent, BacktestRunFacts, BacktestSide
from app.domain.deterministic_backtest_cost_trace import DeterministicBacktestCostTraceError, build_backtest_cost_trace
from app.domain.deterministic_backtest_runner_contracts import run_deterministic_backtest


UTC = datetime(2026, 8, 1, tzinfo=timezone.utc)


def policy(**changes):
    values = dict(policy_version="cost-v1", valuation_ccy="USDT", maker_fee_rate=Decimal("0.0002"), taker_fee_rate=Decimal("0.0005"), buy_slippage_bps=Decimal("2"), sell_slippage_bps=Decimal("3"), funding_rate=Decimal("0.0001"), funding_interval_seconds=28800, evidence_hash="evidence-v1")
    values.update(changes)
    return BacktestCostPolicySnapshot(**values)


def run_with_policy(policy_value):
    return BacktestRunFacts("run-cost-trace", "dataset-cost-trace", "rules", "fees", "slippage", Decimal("1000"), "USDT", UTC, UTC + timedelta(hours=1), cost_policy_fingerprint(policy_value))


def test_cost_trace_binds_exact_policy_and_keeps_fee_funding_separate():
    current = policy()
    run = run_with_policy(current)
    bars = (BacktestBar("BTC_USDT", UTC + timedelta(minutes=1), UTC + timedelta(minutes=2), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("5"), 1, "dataset-cost-trace"),)
    order = BacktestOrderIntent("order-1", "BTC_USDT", BacktestSide.BUY, BacktestExecutionKind.MARKET, Decimal("2"), UTC)
    trace = run_deterministic_backtest(run, bars, (order,))
    costs = build_backtest_cost_trace(run, (order,), trace, current, liquidity_role=BacktestLiquidityRole.TAKER)
    assert len(costs.costs) == 1
    assert costs.costs[0].fee == Decimal("0.1000")
    assert costs.costs[0].funding == Decimal("-0.0200")
    assert costs.costs[0].order_id == "order-1"


def test_cost_trace_rejects_unbound_or_changed_policy():
    current = policy()
    run = run_with_policy(current)
    bars = (BacktestBar("BTC_USDT", UTC + timedelta(minutes=1), UTC + timedelta(minutes=2), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("5"), 1, "dataset-cost-trace"),)
    order = BacktestOrderIntent("order-1", "BTC_USDT", BacktestSide.BUY, BacktestExecutionKind.MARKET, Decimal("2"), UTC)
    trace = run_deterministic_backtest(run, bars, (order,))
    with pytest.raises(DeterministicBacktestCostTraceError):
        build_backtest_cost_trace(run, (order,), trace, policy(taker_fee_rate=Decimal("0.0006")))
    unbound = BacktestRunFacts("run-cost-trace", "dataset-cost-trace", "rules", "fees", "slippage", Decimal("1000"), "USDT", UTC, UTC + timedelta(hours=1))
    with pytest.raises(DeterministicBacktestCostTraceError):
        build_backtest_cost_trace(unbound, (order,), trace, current)
