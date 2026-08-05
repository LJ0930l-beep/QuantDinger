from decimal import Decimal

import pytest

from app.domain.backtest_cost_contracts import (
    BacktestCostContractError,
    BacktestCostPolicySnapshot,
    BacktestLiquidityRole,
    calculate_execution_costs,
    cost_policy_fingerprint,
    execution_cost_fingerprint,
)
from app.domain.deterministic_backtest_contracts import BacktestSide


def policy(**changes):
    values = dict(
        policy_version="cost-v1",
        valuation_ccy="USDT",
        maker_fee_rate=Decimal("0.0002"),
        taker_fee_rate=Decimal("0.0005"),
        buy_slippage_bps=Decimal("2"),
        sell_slippage_bps=Decimal("3"),
        funding_rate=Decimal("0.0001"),
        funding_interval_seconds=8 * 60 * 60,
        evidence_hash="evidence-v1",
    )
    values.update(changes)
    return BacktestCostPolicySnapshot(**values)


def test_policy_is_decimal_only_and_fingerprint_is_stable():
    first, second = policy(), policy(buy_slippage_bps=Decimal("2.00"))
    assert first == second
    assert cost_policy_fingerprint(first) == cost_policy_fingerprint(second)
    with pytest.raises(BacktestCostContractError):
        policy(taker_fee_rate=0.0005)
    with pytest.raises(BacktestCostContractError):
        policy(funding_rate=Decimal("NaN"))


def test_cost_policy_changes_are_identity_material():
    assert cost_policy_fingerprint(policy()) != cost_policy_fingerprint(policy(taker_fee_rate=Decimal("0.0006")))
    assert cost_policy_fingerprint(policy()) != cost_policy_fingerprint(policy(evidence_hash="evidence-v2"))


def test_execution_costs_are_explicit_and_directional():
    result = calculate_execution_costs(
        policy(), side=BacktestSide.BUY, liquidity_role=BacktestLiquidityRole.TAKER,
        reference_price=Decimal("100"), notional=Decimal("1000"),
    )
    assert result.executed_price == Decimal("100.02")
    assert result.fee == Decimal("0.5000")
    assert result.funding == Decimal("-0.1000")
    assert len(execution_cost_fingerprint(result)) == 64
    sell = calculate_execution_costs(
        policy(), side=BacktestSide.SELL, liquidity_role=BacktestLiquidityRole.MAKER,
        reference_price=Decimal("100"), notional=Decimal("1000"),
    )
    assert sell.executed_price == Decimal("99.97")
    assert sell.fee == Decimal("0.2000")
    assert sell.funding == Decimal("0.1000")


def test_cost_calculation_rejects_untypeable_or_invalid_facts():
    with pytest.raises(BacktestCostContractError):
        calculate_execution_costs(policy(), side="buy", liquidity_role=BacktestLiquidityRole.TAKER, reference_price="100", notional="10")
    with pytest.raises(BacktestCostContractError):
        calculate_execution_costs(policy(), side=BacktestSide.BUY, liquidity_role=BacktestLiquidityRole.TAKER, reference_price=1.0, notional="10")
    with pytest.raises(BacktestCostContractError):
        policy(buy_slippage_bps=Decimal("10000"))
