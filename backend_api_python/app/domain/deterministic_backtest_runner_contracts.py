"""Pure deterministic backtest execution trace contracts.

The runner only replays caller-owned bars and order intents.  It never
calculates missing fees, connects to a venue, writes positions, or submits an
order.  PnL/fee metrics remain the responsibility of the separate metrics
contract once explicit trade facts exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from app.domain.deterministic_backtest_contracts import (
    BacktestBar,
    BacktestContractError,
    BacktestDecision,
    BacktestExecutionDecision,
    BacktestOrderIntent,
    BacktestRunFacts,
    backtest_fingerprint,
    next_open_execution,
)


DETERMINISTIC_BACKTEST_RUNNER_CONTRACT_VERSION = "backtest-runner-v1"


class DeterministicBacktestRunnerError(BacktestContractError):
    """The replay inputs cannot form a deterministic execution trace."""


@dataclass(frozen=True, slots=True)
class BacktestExecutionTrace:
    run_id: str
    dataset_snapshot_id: str
    decisions: Tuple[BacktestExecutionDecision, ...]
    fee_policy_version: str = ""
    slippage_policy_version: str = ""
    cost_policy_fingerprint: str = ""
    trace_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id or self.run_id.strip() != self.run_id:
            raise DeterministicBacktestRunnerError("run_id must be canonical text")
        if not isinstance(self.dataset_snapshot_id, str) or not self.dataset_snapshot_id or self.dataset_snapshot_id.strip() != self.dataset_snapshot_id:
            raise DeterministicBacktestRunnerError("dataset_snapshot_id must be canonical text")
        if not isinstance(self.decisions, tuple) or any(not isinstance(item, BacktestExecutionDecision) for item in self.decisions):
            raise DeterministicBacktestRunnerError("decisions must be an explicit typed tuple")
        for field_name in ("fee_policy_version", "slippage_policy_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or value.strip() != value:
                raise DeterministicBacktestRunnerError(f"{field_name} must be canonical text")
        if self.cost_policy_fingerprint and (
            len(self.cost_policy_fingerprint) != 64
            or self.cost_policy_fingerprint != self.cost_policy_fingerprint.lower()
            or any(char not in "0123456789abcdef" for char in self.cost_policy_fingerprint)
        ):
            raise DeterministicBacktestRunnerError("cost_policy_fingerprint must be lowercase sha256 text")
        order_ids = [item.order_id for item in self.decisions]
        if len(order_ids) != len(set(order_ids)):
            raise DeterministicBacktestRunnerError("one decision per order_id is required")
        object.__setattr__(self, "trace_fingerprint", backtest_fingerprint({
            "version": DETERMINISTIC_BACKTEST_RUNNER_CONTRACT_VERSION,
            "run_id": self.run_id,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "fee_policy_version": self.fee_policy_version,
            "slippage_policy_version": self.slippage_policy_version,
            "cost_policy_fingerprint": self.cost_policy_fingerprint,
            "decisions": self.decisions,
        }))


def run_deterministic_backtest(
    run: BacktestRunFacts,
    bars: Tuple[BacktestBar, ...],
    orders: Tuple[BacktestOrderIntent, ...],
) -> BacktestExecutionTrace:
    """Replay each order at the first eligible later bar open.

    Orders are sorted by ``submitted_at`` then ``order_id``.  Bars must already
    be an ordered, complete snapshot; the function does not repair gaps or
    infer a bar from future data.
    """

    if not isinstance(run, BacktestRunFacts):
        raise DeterministicBacktestRunnerError("run must be typed")
    if not isinstance(bars, tuple) or not bars or any(not isinstance(item, BacktestBar) for item in bars):
        raise DeterministicBacktestRunnerError("bars must be an explicit typed tuple")
    if not isinstance(orders, tuple) or any(not isinstance(item, BacktestOrderIntent) for item in orders):
        raise DeterministicBacktestRunnerError("orders must be an explicit typed tuple")
    if any(item.snapshot_id != run.dataset_snapshot_id for item in bars):
        raise DeterministicBacktestRunnerError("bar snapshot does not match run dataset")
    if any(left.sequence >= right.sequence or left.open_time >= right.open_time for left, right in zip(bars, bars[1:])):
        raise DeterministicBacktestRunnerError("bars must be strictly ordered")
    ids = [item.order_id for item in orders]
    if len(ids) != len(set(ids)):
        raise DeterministicBacktestRunnerError("order_id must be unique")
    if any(item.submitted_at < run.clock_start or item.submitted_at >= run.clock_end for item in orders):
        raise DeterministicBacktestRunnerError("order submitted_at is outside run clock")
    if any(item.instrument_id != bars[0].instrument_id for item in orders) or bars[0].instrument_id == "":
        raise DeterministicBacktestRunnerError("orders must match the bar instrument")
    decisions = []
    for order in sorted(orders, key=lambda item: (item.submitted_at, item.order_id)):
        eligible = next((bar for bar in bars if bar.instrument_id == order.instrument_id and bar.open_time > order.submitted_at), None)
        if eligible is None:
            decisions.append(BacktestExecutionDecision(order.order_id, BacktestDecision.INVALID, None, None, "no later bar available"))
            continue
        decisions.append(next_open_execution(order, eligible))
    return BacktestExecutionTrace(
        run.run_id,
        run.dataset_snapshot_id,
        tuple(decisions),
        run.fee_policy_version,
        run.slippage_policy_version,
        run.cost_policy_fingerprint or "",
    )


__all__ = ["DETERMINISTIC_BACKTEST_RUNNER_CONTRACT_VERSION", "BacktestExecutionTrace", "DeterministicBacktestRunnerError", "run_deterministic_backtest"]
