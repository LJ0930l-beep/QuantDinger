"""Deterministic strategy-to-execution backtest rehearsal.

This service connects the existing Strategy Factory to the existing next-open
execution runner.  It remains a research-only boundary: it does not calculate
portfolio state, persist a report, call a venue, or create an order outside
the in-memory typed trace returned to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot, coerce_backtest_dataset
from app.domain.deterministic_backtest_contracts import (
    BacktestContractError,
    BacktestExecutionKind,
    BacktestExecutionDecision,
    BacktestOrderIntent,
    BacktestRunFacts,
    BacktestSide,
    backtest_fingerprint,
)
from app.domain.deterministic_backtest_runner_contracts import (
    BacktestExecutionTrace,
    run_deterministic_backtest,
)
from app.domain.strategy_library_contracts import (
    SignalDirection,
    StrategyDefinition,
    StrategySignalFact,
)
from app.services.strategy_factory import StrategyFactory, StrategyFactoryError


DETERMINISTIC_BACKTEST_SERVICE_VERSION = "deterministic-backtest-service-v1"


class DeterministicBacktestServiceError(BacktestContractError):
    """The dataset, strategy, or execution policy cannot form a safe trace."""


@dataclass(frozen=True, slots=True)
class DeterministicStrategyBacktest:
    run: BacktestRunFacts
    dataset: BacktestDatasetSnapshot
    strategy: StrategyDefinition
    execution_kind: BacktestExecutionKind
    order_quantity: Decimal
    signals: tuple[StrategySignalFact, ...]
    orders: tuple[BacktestOrderIntent, ...]
    trace: BacktestExecutionTrace
    result_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, BacktestRunFacts) or not isinstance(self.dataset, BacktestDatasetSnapshot):
            raise DeterministicBacktestServiceError("run and dataset must be typed")
        if self.run.dataset_snapshot_id != self.dataset.dataset_snapshot_id:
            raise DeterministicBacktestServiceError("run and dataset snapshot identities differ")
        if not isinstance(self.strategy, StrategyDefinition):
            raise DeterministicBacktestServiceError("strategy must be typed")
        if not isinstance(self.execution_kind, BacktestExecutionKind):
            raise DeterministicBacktestServiceError("execution_kind must be typed")
        if isinstance(self.order_quantity, (float, bool)) or not isinstance(self.order_quantity, Decimal):
            raise DeterministicBacktestServiceError("order_quantity must use Decimal")
        if not self.order_quantity.is_finite() or self.order_quantity <= 0:
            raise DeterministicBacktestServiceError("order_quantity must be positive and finite")
        if not isinstance(self.signals, tuple) or any(not isinstance(item, StrategySignalFact) for item in self.signals):
            raise DeterministicBacktestServiceError("signals must be typed")
        if not isinstance(self.orders, tuple) or any(not isinstance(item, BacktestOrderIntent) for item in self.orders):
            raise DeterministicBacktestServiceError("orders must be typed")
        if not isinstance(self.trace, BacktestExecutionTrace):
            raise DeterministicBacktestServiceError("trace must be typed")
        object.__setattr__(self, "result_fingerprint", backtest_fingerprint({
            "version": DETERMINISTIC_BACKTEST_SERVICE_VERSION,
            "run": self.run,
            "dataset": self.dataset.dataset_fingerprint,
            "strategy": self.strategy,
            "execution_kind": self.execution_kind,
            "order_quantity": self.order_quantity,
            "signals": self.signals,
            "orders": self.orders,
            "trace": self.trace.trace_fingerprint,
        }))

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": DETERMINISTIC_BACKTEST_SERVICE_VERSION,
            "run_id": self.run.run_id,
            "dataset_snapshot_id": self.dataset.dataset_snapshot_id,
            "instrument_id": self.dataset.instrument_id,
            "strategy_id": self.strategy.strategy_id,
            "strategy_version": self.strategy.version,
            "signal_count": len(self.signals),
            "order_count": len(self.orders),
            "orders": [
                {
                    "order_id": order.order_id,
                    "side": order.side.value,
                    "execution_kind": order.execution_kind.value,
                    "quantity": format(order.quantity.normalize(), "f"),
                    "submitted_at": order.submitted_at.isoformat(),
                    "limit_price": None if order.limit_price is None else format(order.limit_price.normalize(), "f"),
                }
                for order in self.orders
            ],
            "decisions": [
                {
                    "order_id": decision.order_id,
                    "decision": decision.decision.value,
                    "fill_time": None if decision.fill_time is None else decision.fill_time.isoformat(),
                    "fill_price": None if decision.fill_price is None else format(decision.fill_price.normalize(), "f"),
                    "reason": decision.reason,
                }
                for decision in self.trace.decisions
            ],
            "trace_fingerprint": self.trace.trace_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "live_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class DeterministicBacktestService:
    strategy_factory: StrategyFactory = StrategyFactory()

    def run(
        self,
        run: BacktestRunFacts,
        dataset: BacktestDatasetSnapshot,
        strategy: StrategyDefinition,
        *,
        order_quantity: Decimal,
        execution_kind: BacktestExecutionKind = BacktestExecutionKind.MARKET,
    ) -> DeterministicStrategyBacktest:
        try:
            dataset = coerce_backtest_dataset(dataset)
        except Exception as exc:
            raise DeterministicBacktestServiceError("dataset must be typed") from exc
        if not isinstance(run, BacktestRunFacts) or not isinstance(dataset, BacktestDatasetSnapshot):
            raise DeterministicBacktestServiceError("run and dataset must be typed")
        if run.dataset_snapshot_id != dataset.dataset_snapshot_id:
            raise DeterministicBacktestServiceError("run and dataset snapshot identities differ")
        if not isinstance(strategy, StrategyDefinition):
            raise DeterministicBacktestServiceError("strategy must be typed")
        if isinstance(order_quantity, (float, bool)) or not isinstance(order_quantity, Decimal):
            raise DeterministicBacktestServiceError("order_quantity must use Decimal")
        if not order_quantity.is_finite() or order_quantity <= 0:
            raise DeterministicBacktestServiceError("order_quantity must be positive and finite")
        if not isinstance(execution_kind, BacktestExecutionKind):
            raise DeterministicBacktestServiceError("execution_kind must be typed")
        if dataset.bars[0].open_time < run.clock_start or dataset.bars[-1].close_time > run.clock_end:
            raise DeterministicBacktestServiceError("dataset bars must fit inside the run clock")

        signals: list[StrategySignalFact] = []
        orders: list[BacktestOrderIntent] = []
        # The strategy sees only bars up to the current closed bar.  The
        # execution runner then fills at a later bar open, preventing future
        # leakage by construction.
        for index in range(len(dataset.bars)):
            window = dataset.bars[: index + 1]
            if len(window) < 4:
                continue
            bar = window[-1]
            signal_id = f"{run.run_id}:signal:{bar.sequence}"
            try:
                signal = self.strategy_factory.generate_signal(
                    strategy,
                    window,
                    signal_id=signal_id,
                    data_snapshot_id=dataset.dataset_snapshot_id,
                )
            except StrategyFactoryError as exc:
                raise DeterministicBacktestServiceError("strategy signal generation failed") from exc
            signals.append(signal)
            if signal.direction is SignalDirection.FLAT:
                continue
            limit_price = signal.entry_price if execution_kind is BacktestExecutionKind.LIMIT else None
            if execution_kind is BacktestExecutionKind.LIMIT and limit_price is None:
                raise DeterministicBacktestServiceError("limit execution requires an entry price")
            side = BacktestSide.BUY if signal.direction is SignalDirection.BUY else BacktestSide.SELL
            orders.append(BacktestOrderIntent(
                f"{run.run_id}:order:{bar.sequence}",
                dataset.instrument_id,
                side,
                execution_kind,
                order_quantity,
                signal.occurred_at,
                limit_price,
            ))

        try:
            trace = run_deterministic_backtest(run, dataset.bars, tuple(orders))
            return DeterministicStrategyBacktest(
                run, dataset, strategy, execution_kind, order_quantity,
                tuple(signals), tuple(orders), trace,
            )
        except (BacktestContractError, DeterministicBacktestServiceError):
            raise
        except Exception as exc:
            raise DeterministicBacktestServiceError("backtest execution trace failed closed") from exc


__all__ = [
    "DETERMINISTIC_BACKTEST_SERVICE_VERSION",
    "DeterministicBacktestService",
    "DeterministicBacktestServiceError",
    "DeterministicStrategyBacktest",
]
