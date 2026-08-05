"""Pure cost trace reducer for deterministic backtest executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from .backtest_cost_contracts import (
    BacktestCostContractError,
    BacktestCostPolicySnapshot,
    BacktestExecutionCostFacts,
    BacktestLiquidityRole,
    calculate_realized_costs,
    coerce_backtest_liquidity_role,
    cost_policy_fingerprint,
    execution_cost_fingerprint,
)
from .deterministic_backtest_contracts import BacktestDecision, BacktestOrderIntent, BacktestRunFacts, backtest_fingerprint
from .deterministic_backtest_runner_contracts import BacktestExecutionTrace


DETERMINISTIC_BACKTEST_COST_TRACE_VERSION = "backtest-cost-trace-v1"


class DeterministicBacktestCostTraceError(BacktestCostContractError):
    """The execution trace cannot be costed without guessing."""


@dataclass(frozen=True, slots=True)
class BacktestExecutionCostTrace:
    run_id: str
    dataset_snapshot_id: str
    policy_fingerprint: str
    costs: Tuple[BacktestExecutionCostFacts, ...]
    cost_trace_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id or self.run_id.strip() != self.run_id:
            raise DeterministicBacktestCostTraceError("run_id must be canonical text")
        if not isinstance(self.dataset_snapshot_id, str) or not self.dataset_snapshot_id or self.dataset_snapshot_id.strip() != self.dataset_snapshot_id:
            raise DeterministicBacktestCostTraceError("dataset_snapshot_id must be canonical text")
        if len(self.policy_fingerprint) != 64 or self.policy_fingerprint != self.policy_fingerprint.lower() or any(char not in "0123456789abcdef" for char in self.policy_fingerprint):
            raise DeterministicBacktestCostTraceError("policy_fingerprint must be lowercase sha256 text")
        if not isinstance(self.costs, tuple) or any(not isinstance(item, BacktestExecutionCostFacts) for item in self.costs):
            raise DeterministicBacktestCostTraceError("costs must be an explicit typed tuple")
        ids = [item.order_id for item in self.costs]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise DeterministicBacktestCostTraceError("each cost must identify one unique order")
        object.__setattr__(self, "cost_trace_fingerprint", backtest_fingerprint({
            "version": DETERMINISTIC_BACKTEST_COST_TRACE_VERSION,
            "run_id": self.run_id,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "policy_fingerprint": self.policy_fingerprint,
            "costs": tuple(execution_cost_fingerprint(item) for item in self.costs),
        }))


def build_backtest_cost_trace(
    run: BacktestRunFacts,
    orders: Tuple[BacktestOrderIntent, ...],
    execution_trace: BacktestExecutionTrace,
    policy: BacktestCostPolicySnapshot,
    *,
    liquidity_role: BacktestLiquidityRole = BacktestLiquidityRole.TAKER,
) -> BacktestExecutionCostTrace:
    """Cost only explicit executed decisions; never infer or default a fill."""
    if not isinstance(run, BacktestRunFacts) or not isinstance(execution_trace, BacktestExecutionTrace):
        raise DeterministicBacktestCostTraceError("run and execution_trace must be typed")
    if not isinstance(policy, BacktestCostPolicySnapshot):
        raise DeterministicBacktestCostTraceError("policy must be typed")
    try:
        liquidity_role = coerce_backtest_liquidity_role(liquidity_role)
    except BacktestCostContractError as exc:
        raise DeterministicBacktestCostTraceError("liquidity_role must be typed") from exc
    if run.run_id != execution_trace.run_id or run.dataset_snapshot_id != execution_trace.dataset_snapshot_id:
        raise DeterministicBacktestCostTraceError("run and execution trace scope mismatch")
    expected_policy = cost_policy_fingerprint(policy)
    if run.cost_policy_fingerprint != expected_policy or execution_trace.cost_policy_fingerprint != expected_policy:
        raise DeterministicBacktestCostTraceError("run and execution trace must bind the exact cost policy")
    if not isinstance(orders, tuple) or any(not isinstance(item, BacktestOrderIntent) for item in orders):
        raise DeterministicBacktestCostTraceError("orders must be an explicit typed tuple")
    by_id = {item.order_id: item for item in orders}
    if len(by_id) != len(orders):
        raise DeterministicBacktestCostTraceError("orders must be unique")
    costs = []
    for decision in execution_trace.decisions:
        if decision.decision is not BacktestDecision.EXECUTED:
            continue
        order = by_id.get(decision.order_id)
        if order is None or decision.fill_price is None:
            raise DeterministicBacktestCostTraceError("executed decision is missing order or fill price")
        costs.append(calculate_realized_costs(
            policy,
            side=order.side,
            liquidity_role=liquidity_role,
            executed_price=decision.fill_price,
            notional=order.quantity * decision.fill_price,
            order_id=order.order_id,
        ))
    return BacktestExecutionCostTrace(run.run_id, run.dataset_snapshot_id, expected_policy, tuple(costs))


__all__ = [
    "DETERMINISTIC_BACKTEST_COST_TRACE_VERSION",
    "BacktestExecutionCostTrace",
    "DeterministicBacktestCostTraceError",
    "build_backtest_cost_trace",
]
