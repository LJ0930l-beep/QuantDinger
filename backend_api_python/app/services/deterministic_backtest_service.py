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
from app.domain.backtest_cost_contracts import (
    BacktestCostPolicySnapshot,
    BacktestLiquidityRole,
    coerce_backtest_liquidity_role,
    cost_policy_fingerprint,
)
from app.domain.deterministic_backtest_cost_trace import BacktestExecutionCostTrace, build_backtest_cost_trace
from app.domain.deterministic_backtest_contracts import (
    BacktestContractError,
    BacktestExecutionKind,
    BacktestExecutionDecision,
    BacktestOrderIntent,
    BacktestRunFacts,
    BacktestSide,
    BacktestTriggerDirection,
    BacktestTriggerPriceType,
    backtest_fingerprint,
)
from app.domain.deterministic_backtest_runner_contracts import (
    BacktestExecutionTrace,
    run_deterministic_backtest,
)
from app.domain.backtest_portfolio_contracts import (
    BACKTEST_PORTFOLIO_CONTRACT_VERSION,
    BacktestPortfolioError,
    BacktestPortfolioState,
    build_backtest_portfolio_projection,
)
from app.domain.strategy_library_contracts import (
    SignalDirection,
    StrategyDefinition,
    StrategyLibraryError,
    StrategySignalFact,
    coerce_strategy_definition,
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
    cost_trace: BacktestExecutionCostTrace | None = None
    portfolio: BacktestPortfolioState | None = None
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
        if self.cost_trace is not None:
            if not isinstance(self.cost_trace, BacktestExecutionCostTrace):
                raise DeterministicBacktestServiceError("cost_trace must be typed")
            if (
                self.cost_trace.run_id != self.run.run_id
                or self.cost_trace.dataset_snapshot_id != self.dataset.dataset_snapshot_id
                or self.run.cost_policy_fingerprint != self.cost_trace.policy_fingerprint
            ):
                raise DeterministicBacktestServiceError("cost trace policy or scope mismatch")
        if self.portfolio is not None:
            if not isinstance(self.portfolio, BacktestPortfolioState):
                raise DeterministicBacktestServiceError("portfolio must be typed")
            if self.portfolio.instrument_id != self.dataset.instrument_id:
                raise DeterministicBacktestServiceError("portfolio instrument scope mismatch")
            if self.cost_trace is None:
                raise DeterministicBacktestServiceError("portfolio requires a cost trace")
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
            "cost_trace": None if self.cost_trace is None else self.cost_trace.cost_trace_fingerprint,
            "portfolio": None if self.portfolio is None else self.portfolio.state_fingerprint,
        }))

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": DETERMINISTIC_BACKTEST_SERVICE_VERSION,
            "run_id": self.run.run_id,
            "dataset_snapshot_id": self.dataset.dataset_snapshot_id,
            "fee_policy_version": self.run.fee_policy_version,
            "slippage_policy_version": self.run.slippage_policy_version,
            "cost_policy_fingerprint": self.run.cost_policy_fingerprint,
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
                    "trigger_price": None if order.trigger_price is None else format(order.trigger_price.normalize(), "f"),
                    "trigger_direction": None if order.trigger_direction is None else order.trigger_direction.value,
                    "trigger_price_type": None if order.trigger_price_type is None else order.trigger_price_type.value,
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
            "cost_trace_fingerprint": None if self.cost_trace is None else self.cost_trace.cost_trace_fingerprint,
            "costs": [] if self.cost_trace is None else [
                {
                    "order_id": item.order_id,
                    "policy_version": item.policy_version,
                    "side": item.side.value,
                    "liquidity_role": item.liquidity_role.value,
                    "reference_price": format(item.reference_price.normalize(), "f"),
                    "executed_price": format(item.executed_price.normalize(), "f"),
                    "notional": format(item.notional.normalize(), "f"),
                    "fee": format(item.fee.normalize(), "f"),
                    "funding": format(item.funding.normalize(), "f"),
                }
                for item in self.cost_trace.costs
            ],
            "portfolio_projection": None if self.portfolio is None else {
                "contract_version": BACKTEST_PORTFOLIO_CONTRACT_VERSION,
                "instrument_id": self.portfolio.instrument_id,
                "valuation_ccy": self.portfolio.valuation_ccy,
                "signed_quantity": format(self.portfolio.signed_quantity.normalize(), "f"),
                "average_entry_price": None if self.portfolio.average_entry_price is None else format(self.portfolio.average_entry_price.normalize(), "f"),
                "realized_gross_pnl": format(self.portfolio.realized_gross_pnl.normalize(), "f"),
                "fees_by_asset": [
                    {"asset": asset, "amount": format(amount.normalize(), "f")}
                    for asset, amount in self.portfolio.fees_by_asset
                ],
                "funding": format(self.portfolio.funding.normalize(), "f"),
                "applied_fill_ids": [item.fill_id for item in self.portfolio.applied_fills],
                "state_fingerprint": self.portfolio.state_fingerprint,
            },
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
        cost_policy: BacktestCostPolicySnapshot | None = None,
        liquidity_role: BacktestLiquidityRole = BacktestLiquidityRole.TAKER,
        trigger_direction: BacktestTriggerDirection | None = None,
        trigger_price_type: BacktestTriggerPriceType | None = None,
    ) -> DeterministicStrategyBacktest:
        try:
            dataset = coerce_backtest_dataset(dataset)
        except Exception as exc:
            raise DeterministicBacktestServiceError("dataset must be typed") from exc
        if not isinstance(run, BacktestRunFacts) or not isinstance(dataset, BacktestDatasetSnapshot):
            raise DeterministicBacktestServiceError("run and dataset must be typed")
        if run.dataset_snapshot_id != dataset.dataset_snapshot_id:
            raise DeterministicBacktestServiceError("run and dataset snapshot identities differ")
        try:
            strategy = coerce_strategy_definition(strategy)
        except StrategyLibraryError as exc:
            raise DeterministicBacktestServiceError("strategy must be typed") from exc
        if isinstance(order_quantity, (float, bool)) or not isinstance(order_quantity, Decimal):
            raise DeterministicBacktestServiceError("order_quantity must use Decimal")
        if not order_quantity.is_finite() or order_quantity <= 0:
            raise DeterministicBacktestServiceError("order_quantity must be positive and finite")
        if not isinstance(execution_kind, BacktestExecutionKind):
            raise DeterministicBacktestServiceError("execution_kind must be typed")
        if cost_policy is not None and not isinstance(cost_policy, BacktestCostPolicySnapshot):
            raise DeterministicBacktestServiceError("cost_policy must be typed")
        try:
            liquidity_role = coerce_backtest_liquidity_role(liquidity_role)
        except Exception as exc:
            raise DeterministicBacktestServiceError("liquidity_role must be typed") from exc
        if cost_policy is not None and run.cost_policy_fingerprint != cost_policy_fingerprint(cost_policy):
            raise DeterministicBacktestServiceError("run must bind the exact cost policy")
        if cost_policy is None and run.cost_policy_fingerprint is not None:
            raise DeterministicBacktestServiceError("bound cost policy facts are required")
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
                scope = {}
                if dataset.timeframe is not None:
                    scope = {"timeframe": dataset.timeframe, "market_type": dataset.market_type}
                signal = self.strategy_factory.generate_signal(
                    strategy,
                    window,
                    signal_id=signal_id,
                    data_snapshot_id=dataset.dataset_snapshot_id,
                    **scope,
                )
            except StrategyFactoryError as exc:
                raise DeterministicBacktestServiceError("strategy signal generation failed") from exc
            signals.append(signal)
            if signal.direction is SignalDirection.FLAT:
                continue
            limit_price = signal.entry_price if execution_kind in (BacktestExecutionKind.LIMIT, BacktestExecutionKind.STOP_LIMIT) else None
            trigger_price = signal.stop_price if execution_kind in (BacktestExecutionKind.STOP_MARKET, BacktestExecutionKind.STOP_LIMIT) else None
            if execution_kind in (BacktestExecutionKind.LIMIT, BacktestExecutionKind.STOP_LIMIT) and limit_price is None:
                raise DeterministicBacktestServiceError("limit execution requires an entry price")
            if execution_kind in (BacktestExecutionKind.STOP_MARKET, BacktestExecutionKind.STOP_LIMIT) and (trigger_price is None or trigger_direction is None or trigger_price_type is None):
                raise DeterministicBacktestServiceError("stop execution requires signal stop price and explicit trigger facts")
            side = BacktestSide.BUY if signal.direction is SignalDirection.BUY else BacktestSide.SELL
            orders.append(BacktestOrderIntent(
                f"{run.run_id}:order:{bar.sequence}",
                dataset.instrument_id,
                side,
                execution_kind,
                order_quantity,
                signal.occurred_at,
                limit_price,
                trigger_price,
                trigger_direction,
                trigger_price_type,
            ))

        try:
            trace = run_deterministic_backtest(run, dataset.bars, tuple(orders))
            cost_trace = None
            portfolio = None
            if cost_policy is not None:
                cost_trace = build_backtest_cost_trace(
                    run, tuple(orders), trace, cost_policy, liquidity_role=liquidity_role,
                )
                try:
                    portfolio = build_backtest_portfolio_projection(
                        instrument_id=dataset.instrument_id,
                        valuation_ccy=cost_policy.valuation_ccy,
                        orders=tuple(orders),
                        execution_trace=trace,
                        cost_trace=cost_trace,
                    )
                except BacktestPortfolioError as exc:
                    raise DeterministicBacktestServiceError("backtest portfolio projection failed closed") from exc
            return DeterministicStrategyBacktest(
                run, dataset, strategy, execution_kind, order_quantity,
                tuple(signals), tuple(orders), trace, cost_trace, portfolio,
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
