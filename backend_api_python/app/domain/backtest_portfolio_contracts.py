"""Deterministic portfolio projection for BT-01 backtest fills.

The projection is deliberately smaller than an execution engine.  It consumes
already validated fills and cost facts, keeps gross realized PnL, fees and
funding separate, and can be rebuilt from the same ordered fill facts after a
restart.  It never reads a database, contacts a venue, or decides an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .deterministic_backtest_contracts import (
    BacktestDecision,
    BacktestOrderIntent,
    BacktestSide,
    backtest_fingerprint,
)
from .deterministic_backtest_cost_trace import BacktestExecutionCostTrace
from .deterministic_backtest_runner_contracts import BacktestExecutionTrace


BACKTEST_PORTFOLIO_CONTRACT_VERSION = "backtest-portfolio-v1"


class BacktestPortfolioError(ValueError):
    """The supplied backtest portfolio facts are invalid or conflicting."""


class BacktestPortfolioDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"
    CONFLICT = "CONFLICT"


def _text(value: Any, name: str, *, upper: bool = False) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise BacktestPortfolioError(f"{name} must be canonical ASCII text")
    result = value.upper() if upper else value
    return result


def _decimal(value: Any, name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (bool, float)):
        raise BacktestPortfolioError(f"{name} rejects float/bool input")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BacktestPortfolioError(f"{name} must be Decimal-compatible") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (non_negative and parsed < 0):
        raise BacktestPortfolioError(f"{name} has invalid numeric bounds")
    return parsed


def _utc(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise BacktestPortfolioError(f"{name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class BacktestPortfolioFill:
    """One fully priced fill with independently preserved cost facts."""

    fill_id: str
    instrument_id: str
    side: BacktestSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    funding: Decimal
    policy_version: str
    occurred_at: datetime
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        fill_id = _text(self.fill_id, "fill_id")
        instrument = _text(self.instrument_id, "instrument_id")
        if not isinstance(self.side, BacktestSide):
            raise BacktestPortfolioError("side must be typed")
        quantity = _decimal(self.quantity, "quantity", positive=True)
        price = _decimal(self.price, "price", positive=True)
        fee = _decimal(self.fee, "fee", non_negative=True)
        fee_asset = _text(self.fee_asset, "fee_asset", upper=True)
        funding = _decimal(self.funding, "funding")
        policy = _text(self.policy_version, "policy_version")
        occurred = _utc(self.occurred_at, "occurred_at")
        material = {
            "version": BACKTEST_PORTFOLIO_CONTRACT_VERSION,
            "fill_id": fill_id,
            "instrument_id": instrument,
            "side": self.side.value,
            "quantity": format(quantity.normalize(), "f"),
            "price": format(price.normalize(), "f"),
            "fee": format(fee.normalize(), "f"),
            "fee_asset": fee_asset,
            "funding": format(funding.normalize(), "f"),
            "policy_version": policy,
            "occurred_at": occurred.isoformat(),
        }
        object.__setattr__(self, "fill_id", fill_id)
        object.__setattr__(self, "instrument_id", instrument)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "fee", fee)
        object.__setattr__(self, "fee_asset", fee_asset)
        object.__setattr__(self, "funding", funding)
        object.__setattr__(self, "policy_version", policy)
        object.__setattr__(self, "occurred_at", occurred)
        object.__setattr__(self, "fingerprint", backtest_fingerprint(material))


@dataclass(frozen=True, slots=True)
class BacktestPortfolioState:
    instrument_id: str
    valuation_ccy: str
    signed_quantity: Decimal = Decimal("0")
    average_entry_price: Decimal | None = None
    realized_gross_pnl: Decimal = Decimal("0")
    fees_by_asset: tuple[tuple[str, Decimal], ...] = ()
    funding: Decimal = Decimal("0")
    applied_fills: tuple[BacktestPortfolioFill, ...] = ()
    state_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        instrument = _text(self.instrument_id, "instrument_id")
        valuation = _text(self.valuation_ccy, "valuation_ccy", upper=True)
        quantity = _decimal(self.signed_quantity, "signed_quantity")
        average = None if self.average_entry_price is None else _decimal(self.average_entry_price, "average_entry_price", positive=True)
        realized = _decimal(self.realized_gross_pnl, "realized_gross_pnl")
        funding = _decimal(self.funding, "funding")
        fees = tuple(self.fees_by_asset)
        if any(not isinstance(item, tuple) or len(item) != 2 for item in fees):
            raise BacktestPortfolioError("fees_by_asset must contain asset/value pairs")
        normalized_fees: list[tuple[str, Decimal]] = []
        for asset, amount in fees:
            normalized_fees.append((_text(asset, "fee_asset", upper=True), _decimal(amount, "fee", non_negative=True)))
        if tuple(sorted(normalized_fees)) != tuple(normalized_fees):
            raise BacktestPortfolioError("fees_by_asset must be sorted by canonical asset")
        if len({item[0] for item in normalized_fees}) != len(normalized_fees):
            raise BacktestPortfolioError("fees_by_asset must not contain duplicate assets")
        fills = tuple(self.applied_fills)
        if any(not isinstance(item, BacktestPortfolioFill) for item in fills):
            raise BacktestPortfolioError("applied_fills must be typed")
        if len({item.fill_id for item in fills}) != len(fills):
            raise BacktestPortfolioError("applied_fills must have unique fill ids")
        if any(item.instrument_id != instrument for item in fills):
            raise BacktestPortfolioError("fill instrument does not match portfolio")
        if quantity == 0 and average is not None:
            raise BacktestPortfolioError("empty position cannot have an entry price")
        if quantity != 0 and average is None:
            raise BacktestPortfolioError("non-empty position requires an entry price")
        object.__setattr__(self, "instrument_id", instrument)
        object.__setattr__(self, "valuation_ccy", valuation)
        object.__setattr__(self, "signed_quantity", quantity)
        object.__setattr__(self, "average_entry_price", average)
        object.__setattr__(self, "realized_gross_pnl", realized)
        object.__setattr__(self, "fees_by_asset", tuple(normalized_fees))
        object.__setattr__(self, "funding", funding)
        object.__setattr__(self, "applied_fills", fills)
        object.__setattr__(self, "state_fingerprint", backtest_fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "version": BACKTEST_PORTFOLIO_CONTRACT_VERSION,
            "instrument_id": self.instrument_id,
            "valuation_ccy": self.valuation_ccy,
            "signed_quantity": format(self.signed_quantity.normalize(), "f"),
            "average_entry_price": None if self.average_entry_price is None else format(self.average_entry_price.normalize(), "f"),
            "realized_gross_pnl": format(self.realized_gross_pnl.normalize(), "f"),
            "fees_by_asset": [(asset, format(amount.normalize(), "f")) for asset, amount in self.fees_by_asset],
            "funding": format(self.funding.normalize(), "f"),
            "fills": [item.fingerprint for item in self.applied_fills],
        }

    @property
    def total_fee(self) -> Decimal:
        """Return a scalar fee only when the portfolio has one fee asset.

        Multi-asset fee facts must remain separate; silently summing unlike
        assets would create an invented valuation.
        """

        if len(self.fees_by_asset) > 1:
            raise BacktestPortfolioError("multi-asset fees have no scalar total")
        return self.fees_by_asset[0][1] if self.fees_by_asset else Decimal("0")


@dataclass(frozen=True, slots=True)
class BacktestPortfolioResult:
    disposition: BacktestPortfolioDisposition
    state: BacktestPortfolioState


def _same_fill(left: BacktestPortfolioFill, right: BacktestPortfolioFill) -> bool:
    return left.fingerprint == right.fingerprint


def apply_backtest_portfolio_fill(
    state: BacktestPortfolioState,
    fill: BacktestPortfolioFill,
) -> BacktestPortfolioResult:
    """Apply one fill, returning exact replay or typed conflict."""

    if not isinstance(state, BacktestPortfolioState) or not isinstance(fill, BacktestPortfolioFill):
        raise BacktestPortfolioError("typed portfolio state and fill are required")
    if fill.instrument_id != state.instrument_id:
        raise BacktestPortfolioError("fill instrument does not match portfolio")
    if state.applied_fills and (fill.occurred_at, fill.fill_id) < (
        state.applied_fills[-1].occurred_at,
        state.applied_fills[-1].fill_id,
    ):
        raise BacktestPortfolioError("fills must be applied in canonical occurred_at/fill_id order")
    existing = next((item for item in state.applied_fills if item.fill_id == fill.fill_id), None)
    if existing is not None:
        return BacktestPortfolioResult(
            BacktestPortfolioDisposition.REPLAYED if _same_fill(existing, fill) else BacktestPortfolioDisposition.CONFLICT,
            state,
        )

    signed_fill = fill.quantity if fill.side is BacktestSide.BUY else -fill.quantity
    quantity = state.signed_quantity
    average = state.average_entry_price
    realized = state.realized_gross_pnl
    if quantity == 0:
        quantity, average = signed_fill, fill.price
    else:
        same_direction = (quantity > 0 and signed_fill > 0) or (quantity < 0 and signed_fill < 0)
        if same_direction:
            average = ((abs(quantity) * (average or fill.price)) + (abs(signed_fill) * fill.price)) / (abs(quantity) + abs(signed_fill))
            quantity += signed_fill
        else:
            closing = min(abs(quantity), abs(signed_fill))
            realized += (fill.price - (average or fill.price)) * closing if quantity > 0 else ((average or fill.price) - fill.price) * closing
            remaining = quantity + signed_fill
            quantity = remaining
            average = None if remaining == 0 else (average if (quantity > 0 and signed_fill < 0) or (quantity < 0 and signed_fill > 0) else fill.price)

    fee_totals = dict(state.fees_by_asset)
    fee_totals[fill.fee_asset] = fee_totals.get(fill.fee_asset, Decimal("0")) + fill.fee
    updated = BacktestPortfolioState(
        state.instrument_id,
        state.valuation_ccy,
        quantity,
        average,
        realized,
        tuple(sorted(fee_totals.items())),
        state.funding + fill.funding,
        state.applied_fills + (fill,),
    )
    return BacktestPortfolioResult(BacktestPortfolioDisposition.CREATED, updated)


def calculate_backtest_unrealized_pnl(state: BacktestPortfolioState, mark_price: Any) -> Decimal:
    """Calculate mark-to-market PnL without changing the stored state."""

    if not isinstance(state, BacktestPortfolioState):
        raise BacktestPortfolioError("state must be typed")
    mark = _decimal(mark_price, "mark_price", positive=True)
    if state.signed_quantity == 0:
        return Decimal("0")
    assert state.average_entry_price is not None
    return (mark - state.average_entry_price) * state.signed_quantity


def build_backtest_portfolio_projection(
    *,
    instrument_id: str,
    valuation_ccy: str,
    orders: tuple[BacktestOrderIntent, ...],
    execution_trace: BacktestExecutionTrace,
    cost_trace: BacktestExecutionCostTrace,
) -> BacktestPortfolioState:
    """Rebuild a deterministic position projection from explicit executed facts.

    The helper accepts only the already priced cost trace.  It never infers a
    fill for rejected/invalid decisions and verifies the cost notional against
    the order quantity before applying any fact.
    """

    if not isinstance(execution_trace, BacktestExecutionTrace) or not isinstance(cost_trace, BacktestExecutionCostTrace):
        raise BacktestPortfolioError("execution and cost traces must be typed")
    if not isinstance(orders, tuple) or any(not isinstance(item, BacktestOrderIntent) for item in orders):
        raise BacktestPortfolioError("orders must be typed")
    if cost_trace.run_id != execution_trace.run_id or cost_trace.dataset_snapshot_id != execution_trace.dataset_snapshot_id:
        raise BacktestPortfolioError("execution and cost trace scope mismatch")
    by_order = {item.order_id: item for item in orders}
    if len(by_order) != len(orders):
        raise BacktestPortfolioError("orders must be unique")
    by_cost = {item.order_id: item for item in cost_trace.costs}
    if len(by_cost) != len(cost_trace.costs):
        raise BacktestPortfolioError("cost facts must be unique")
    decisions = {item.order_id: item for item in execution_trace.decisions}
    fills: list[BacktestPortfolioFill] = []
    for order_id, cost in by_cost.items():
        order = by_order.get(order_id)
        decision = decisions.get(order_id)
        if order is None or decision is None or decision.decision is not BacktestDecision.EXECUTED or decision.fill_time is None or decision.fill_price is None:
            raise BacktestPortfolioError("cost fact is not backed by an executed decision")
        expected_notional = order.quantity * cost.executed_price
        if expected_notional != cost.notional:
            raise BacktestPortfolioError("cost notional does not match order quantity")
        fills.append(BacktestPortfolioFill(
            order_id,
            order.instrument_id,
            order.side,
            order.quantity,
            cost.executed_price,
            cost.fee,
            valuation_ccy,
            cost.funding,
            cost.policy_version,
            decision.fill_time,
        ))
    state = BacktestPortfolioState(instrument_id, valuation_ccy)
    for fill in sorted(fills, key=lambda item: (item.occurred_at, item.fill_id)):
        result = apply_backtest_portfolio_fill(state, fill)
        if result.disposition is not BacktestPortfolioDisposition.CREATED:
            raise BacktestPortfolioError("projection contains duplicate fill identity")
        state = result.state
    return state


__all__ = [
    "BACKTEST_PORTFOLIO_CONTRACT_VERSION",
    "BacktestPortfolioDisposition",
    "BacktestPortfolioError",
    "BacktestPortfolioFill",
    "BacktestPortfolioResult",
    "BacktestPortfolioState",
    "apply_backtest_portfolio_fill",
    "build_backtest_portfolio_projection",
    "calculate_backtest_unrealized_pnl",
]
