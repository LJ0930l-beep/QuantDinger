"""Explicit conversion from deterministic signal evidence to a candidate plan.

The converter requires every execution and position fact from the caller.  It
never infers order type, quantity, trigger rules, account scope, or a live
mode from a signal and never persists or submits anything.
"""

from __future__ import annotations

from decimal import Decimal

from .canonical_entry_contracts import ExecutionKind, OrderSide, PositionSide
from .decimal_values import Price, Quantity
from .order_contracts import OrderAction
from .strategy_library_contracts import SignalDirection, StrategySignalFact
from .strategy_v2_candidate_contracts import StrategyV2CandidateError, StrategyV2CandidateTradePlan


STRATEGY_SIGNAL_CANDIDATE_CONTRACT_VERSION = "strategy-signal-candidate-v1"


class StrategySignalCandidateError(StrategyV2CandidateError):
    """The supplied signal lacks explicit candidate conversion facts."""


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StrategySignalCandidateError(f"{field} must be a positive integer")
    return value


def candidate_from_strategy_signal(
    signal: StrategySignalFact,
    *,
    strategy_id: int,
    strategy_run_id: int,
    action: OrderAction,
    execution_kind: ExecutionKind,
    quantity: Decimal | str | int | Quantity | None,
    market_type: str,
    reduce_only: bool = False,
    position_side: PositionSide = PositionSide.NET,
    limit_price: Decimal | str | int | Price | None = None,
    trigger_price: Decimal | str | int | Price | None = None,
    trigger_direction=None,
    trigger_price_type=None,
    target_position_id: str | None = None,
    close_quantity: Decimal | str | int | Quantity | None = None,
    close_all: bool = False,
) -> StrategyV2CandidateTradePlan:
    """Convert one non-flat signal using only explicit caller-owned facts."""

    if not isinstance(signal, StrategySignalFact):
        raise StrategySignalCandidateError("signal must use StrategySignalFact")
    _positive_int(strategy_id, "strategy_id")
    _positive_int(strategy_run_id, "strategy_run_id")
    if signal.direction is SignalDirection.FLAT:
        raise StrategySignalCandidateError("flat signal cannot become a trade candidate")
    if not isinstance(action, OrderAction) or action is OrderAction.CANCEL:
        raise StrategySignalCandidateError("candidate conversion requires a non-CANCEL action")
    if not isinstance(execution_kind, ExecutionKind):
        raise StrategySignalCandidateError("execution_kind must be explicit")
    if not isinstance(market_type, str) or not market_type or market_type.strip() != market_type or not market_type.isascii() or any(ch.isspace() for ch in market_type):
        raise StrategySignalCandidateError("market_type must be canonical ASCII text")
    side = OrderSide.BUY if signal.direction is SignalDirection.BUY else OrderSide.SELL
    try:
        return StrategyV2CandidateTradePlan(
            strategy_id=strategy_id,
            strategy_run_id=strategy_run_id,
            signal_id=signal.signal_id,
            instrument_id=signal.instrument_id,
            market_type=market_type.lower(),
            action=action,
            side=side,
            quantity=quantity,
            execution_kind=execution_kind,
            limit_price=limit_price,
            trigger_price=trigger_price,
            trigger_direction=trigger_direction,
            trigger_price_type=trigger_price_type,
            reduce_only=reduce_only,
            position_side=position_side,
            target_position_id=target_position_id,
            close_quantity=close_quantity,
            close_all=close_all,
        )
    except StrategyV2CandidateError:
        raise
    except Exception as exc:
        raise StrategySignalCandidateError("signal cannot become a candidate plan") from exc


__all__ = ["STRATEGY_SIGNAL_CANDIDATE_CONTRACT_VERSION", "StrategySignalCandidateError", "candidate_from_strategy_signal"]
