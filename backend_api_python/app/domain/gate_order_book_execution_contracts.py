"""Decimal-only Gate order-book liquidity-estimate contracts.

This module turns an already verified, healthy public order-book session into
deterministic *execution estimates* for research, backtests, paper trading,
and later shadow comparisons.  It deliberately has no transport, account,
database, order, worker, or runtime authority.  An estimate is evidence about
visible depth; it is never an instruction to submit an order.

Spot quantities are base-asset units.  Perpetual quantities are contract
units, and a supplied immutable instrument-rule ``contract_size`` is required
before this contract derives a quote amount.  That prevents a contract count
from being silently treated as base quantity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from enum import Enum
import hashlib
import json
from typing import Any

from .decimal_values import (
    CALCULATION_PRECISION,
    Price,
    Quantity,
    QuoteAmount,
    canonical_decimal_string,
    fit_calculated_decimal,
)
from .gate_order_book_stream_session_contracts import (
    GateOrderBookEvidenceHealth,
    GateOrderBookStreamSession,
    GateOrderBookStreamSessionError,
    assess_gate_order_book_stream_session_freshness,
)
from .gate_vertical_read_contracts import GateInstrumentRuleSnapshot, gate_read_fingerprint
from .multi_asset_capability_contracts import AssetMarketType


GATE_ORDER_BOOK_EXECUTION_CONTRACT_VERSION = "gate-order-book-execution-estimate-v1"


class GateOrderBookExecutionError(ValueError):
    """An order-book estimate cannot become complete, typed evidence."""


class GateOrderBookExecutionScopeConflict(GateOrderBookExecutionError):
    """A request, instrument rule, or session names different market scope."""


class GateOrderBookExecutionSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class GateOrderBookQuantityUnit(str, Enum):
    BASE_ASSET = "BASE_ASSET"
    CONTRACT = "CONTRACT"


class GateOrderBookExecutionDisposition(str, Enum):
    FULLY_FILLABLE = "FULLY_FILLABLE"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    PRICE_PROTECTION_REJECTED = "PRICE_PROTECTION_REJECTED"
    UNHEALTHY_ORDER_BOOK = "UNHEALTHY_ORDER_BOOK"


def _text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or not value.isascii()
        or any(character.isspace() for character in value)
    ):
        raise GateOrderBookExecutionError(f"{field_name} must be canonical ASCII text")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise GateOrderBookExecutionError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _positive_window(value: object, field_name: str) -> timedelta:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise GateOrderBookExecutionError(f"{field_name} must be a positive timedelta")
    return value


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _fingerprint_text(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise GateOrderBookExecutionError(f"{field_name} must be a lowercase SHA-256 fingerprint")
    return text


def _timedelta_microseconds(value: timedelta) -> int:
    return value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds


def _quote(value: Decimal) -> QuoteAmount:
    return QuoteAmount(fit_calculated_decimal(value))


def _price(value: Decimal) -> Price:
    return Price(fit_calculated_decimal(value))


def _quantity(value: Decimal) -> Quantity:
    return Quantity(fit_calculated_decimal(value))


def _expected_unit(market_type: AssetMarketType) -> GateOrderBookQuantityUnit:
    if market_type is AssetMarketType.SPOT:
        return GateOrderBookQuantityUnit.BASE_ASSET
    if market_type is AssetMarketType.PERPETUAL:
        return GateOrderBookQuantityUnit.CONTRACT
    raise GateOrderBookExecutionError("market_type must be typed spot or perpetual")


@dataclass(frozen=True, slots=True)
class GateOrderBookExecutionPolicy:
    """Immutable freshness policy for one visible-depth estimate."""

    policy_version: str
    max_staleness: timedelta
    policy_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        version = _text(self.policy_version, "policy_version")
        window = _positive_window(self.max_staleness, "max_staleness")
        object.__setattr__(self, "policy_version", version)
        object.__setattr__(self, "max_staleness", window)
        object.__setattr__(self, "policy_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_EXECUTION_CONTRACT_VERSION,
            "policy_version": version,
            "max_staleness_microseconds": _timedelta_microseconds(window),
        }))


@dataclass(frozen=True, slots=True)
class GateOrderBookExecutionRequest:
    """One scope-bound request to estimate visible Gate order-book liquidity."""

    market_type: AssetMarketType
    instrument_id: str
    side: GateOrderBookExecutionSide
    quantity: Quantity
    quantity_unit: GateOrderBookQuantityUnit
    instrument_rule: GateInstrumentRuleSnapshot
    policy: GateOrderBookExecutionPolicy
    as_of: datetime
    price_protection: Price | None = None
    request_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.market_type, AssetMarketType)
            or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL)
        ):
            raise GateOrderBookExecutionError("market_type must be typed spot or perpetual")
        instrument_id = _text(self.instrument_id, "instrument_id")
        if not isinstance(self.side, GateOrderBookExecutionSide):
            raise GateOrderBookExecutionError("side must be typed")
        if not isinstance(self.quantity, Quantity) or self.quantity.value <= 0:
            raise GateOrderBookExecutionError("quantity must be a positive Quantity")
        expected_unit = _expected_unit(self.market_type)
        if self.quantity_unit is not expected_unit:
            raise GateOrderBookExecutionError("quantity_unit does not match market_type")
        if not isinstance(self.instrument_rule, GateInstrumentRuleSnapshot):
            raise GateOrderBookExecutionError("instrument_rule must be typed")
        if (
            self.instrument_rule.market_type is not self.market_type
            or self.instrument_rule.instrument_id != instrument_id
        ):
            raise GateOrderBookExecutionScopeConflict("instrument_rule does not match request scope")
        if not isinstance(self.policy, GateOrderBookExecutionPolicy):
            raise GateOrderBookExecutionError("policy must be typed")
        as_of = _utc(self.as_of, "as_of")
        if as_of < self.instrument_rule.observed_at:
            raise GateOrderBookExecutionError("as_of cannot precede instrument_rule evidence")
        if self.price_protection is not None and not isinstance(self.price_protection, Price):
            raise GateOrderBookExecutionError("price_protection must be Price when supplied")
        _validate_quantity_against_rule(self.quantity, self.instrument_rule)
        if self.market_type is AssetMarketType.PERPETUAL and self.instrument_rule.contract_size is None:
            raise GateOrderBookExecutionError("perpetual estimate requires immutable contract_size evidence")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "request_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_EXECUTION_CONTRACT_VERSION,
            "market_type": self.market_type.value,
            "instrument_id": instrument_id,
            "side": self.side.value,
            "quantity": self.quantity.to_string(),
            "quantity_unit": self.quantity_unit.value,
            "instrument_rule": gate_read_fingerprint(self.instrument_rule),
            "policy": self.policy.policy_fingerprint,
            "as_of": as_of.isoformat(),
            "price_protection": None if self.price_protection is None else self.price_protection.to_string(),
        }))


def _validate_quantity_against_rule(quantity: Quantity, rule: GateInstrumentRuleSnapshot) -> None:
    if quantity.value < rule.minimum_quantity:
        raise GateOrderBookExecutionError("quantity is below immutable instrument minimum")
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        remainder = quantity.value % rule.quantity_step
    if remainder != 0:
        raise GateOrderBookExecutionError("quantity does not match immutable instrument quantity_step")


def _base_multiplier(request: GateOrderBookExecutionRequest) -> Decimal:
    if request.market_type is AssetMarketType.SPOT:
        return Decimal("1")
    contract_size = request.instrument_rule.contract_size
    if contract_size is None:
        raise GateOrderBookExecutionError("perpetual estimate requires immutable contract_size evidence")
    return contract_size


@dataclass(frozen=True, slots=True)
class GateOrderBookLevelConsumption:
    """The non-zero visible portion consumed from one canonical book level."""

    level_index: int
    price: Price
    available_quantity: Quantity
    consumed_quantity: Quantity
    quote_amount: QuoteAmount
    level_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.level_index, bool) or not isinstance(self.level_index, int) or self.level_index < 0:
            raise GateOrderBookExecutionError("level_index must be a non-negative integer")
        for field_name, expected in (
            ("price", Price),
            ("available_quantity", Quantity),
            ("consumed_quantity", Quantity),
            ("quote_amount", QuoteAmount),
        ):
            if not isinstance(getattr(self, field_name), expected):
                raise GateOrderBookExecutionError(f"{field_name} must be typed")
        if self.available_quantity.value <= 0 or self.consumed_quantity.value <= 0:
            raise GateOrderBookExecutionError("level quantities must be positive")
        if self.consumed_quantity.value > self.available_quantity.value:
            raise GateOrderBookExecutionError("consumed_quantity cannot exceed available_quantity")
        if self.quote_amount.value <= 0:
            raise GateOrderBookExecutionError("quote_amount must be positive")
        object.__setattr__(self, "level_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_EXECUTION_CONTRACT_VERSION,
            "level_index": self.level_index,
            "price": self.price.to_string(),
            "available_quantity": self.available_quantity.to_string(),
            "consumed_quantity": self.consumed_quantity.to_string(),
            "quote_amount": self.quote_amount.to_string(),
        }))


@dataclass(frozen=True, slots=True)
class GateOrderBookExecutionEstimate:
    """A deterministic, non-authorizing visible-liquidity estimate."""

    request: GateOrderBookExecutionRequest
    session_fingerprint: str
    order_book_evidence_hash: str
    disposition: GateOrderBookExecutionDisposition
    consumed_levels: tuple[GateOrderBookLevelConsumption, ...]
    filled_quantity: Quantity
    remaining_quantity: Quantity
    quote_amount: QuoteAmount
    weighted_average_price: Price | None
    best_price: Price | None
    worst_price: Price | None
    spread: Price | None
    reason: str
    estimate_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.request, GateOrderBookExecutionRequest):
            raise GateOrderBookExecutionError("request must be typed")
        if not isinstance(self.disposition, GateOrderBookExecutionDisposition):
            raise GateOrderBookExecutionError("disposition must be typed")
        _fingerprint_text(self.session_fingerprint, "session_fingerprint")
        _fingerprint_text(self.order_book_evidence_hash, "order_book_evidence_hash")
        if not isinstance(self.consumed_levels, tuple) or any(not isinstance(item, GateOrderBookLevelConsumption) for item in self.consumed_levels):
            raise GateOrderBookExecutionError("consumed_levels must be typed tuple")
        if len({item.level_index for item in self.consumed_levels}) != len(self.consumed_levels):
            raise GateOrderBookExecutionError("consumed level indices must be unique")
        if tuple(item.level_index for item in self.consumed_levels) != tuple(sorted(item.level_index for item in self.consumed_levels)):
            raise GateOrderBookExecutionError("consumed level indices must be ascending")
        for field_name, expected in (
            ("filled_quantity", Quantity),
            ("remaining_quantity", Quantity),
            ("quote_amount", QuoteAmount),
        ):
            if not isinstance(getattr(self, field_name), expected):
                raise GateOrderBookExecutionError(f"{field_name} must be typed")
        if self.filled_quantity.value + self.remaining_quantity.value != self.request.quantity.value:
            raise GateOrderBookExecutionError("filled and remaining quantity must equal request quantity")
        for field_name in ("weighted_average_price", "best_price", "worst_price", "spread"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, Price):
                raise GateOrderBookExecutionError(f"{field_name} must be Price when supplied")
        _text(self.reason, "reason")
        _validate_estimate_shape(self)
        object.__setattr__(self, "estimate_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_EXECUTION_CONTRACT_VERSION,
            "request": self.request.request_fingerprint,
            "session": self.session_fingerprint,
            "order_book_evidence_hash": self.order_book_evidence_hash,
            "disposition": self.disposition.value,
            "consumed_levels": [item.level_fingerprint for item in self.consumed_levels],
            "filled_quantity": self.filled_quantity.to_string(),
            "remaining_quantity": self.remaining_quantity.to_string(),
            "quote_amount": self.quote_amount.to_string(),
            "weighted_average_price": None if self.weighted_average_price is None else self.weighted_average_price.to_string(),
            "best_price": None if self.best_price is None else self.best_price.to_string(),
            "worst_price": None if self.worst_price is None else self.worst_price.to_string(),
            "spread": None if self.spread is None else self.spread.to_string(),
            "reason": self.reason,
        }))


def _validate_estimate_shape(estimate: GateOrderBookExecutionEstimate) -> None:
    is_unhealthy = estimate.disposition is GateOrderBookExecutionDisposition.UNHEALTHY_ORDER_BOOK
    no_execution_fields = (
        not estimate.consumed_levels
        and estimate.filled_quantity.value == 0
        and estimate.quote_amount.value == 0
        and estimate.weighted_average_price is None
        and estimate.best_price is None
        and estimate.worst_price is None
        and estimate.spread is None
    )
    if is_unhealthy:
        if not no_execution_fields or estimate.remaining_quantity.value != estimate.request.quantity.value:
            raise GateOrderBookExecutionError("unhealthy order book cannot expose execution evidence")
        return
    multiplier = _base_multiplier(estimate.request)
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        expected_filled = sum((item.consumed_quantity.value for item in estimate.consumed_levels), Decimal("0"))
        expected_quote = sum((item.price.value * item.consumed_quantity.value * multiplier for item in estimate.consumed_levels), Decimal("0"))
    if expected_filled != estimate.filled_quantity.value:
        raise GateOrderBookExecutionError("consumed quantities must equal filled_quantity")
    if fit_calculated_decimal(expected_quote) != estimate.quote_amount.value:
        raise GateOrderBookExecutionError("consumed levels must equal quote_amount")
    for item in estimate.consumed_levels:
        expected_level_quote = fit_calculated_decimal(item.price.value * item.consumed_quantity.value * multiplier)
        if expected_level_quote != item.quote_amount.value:
            raise GateOrderBookExecutionError("level quote_amount must match visible price and consumed quantity")
        if not _price_protected(estimate.request, item.price.value):
            raise GateOrderBookExecutionError("consumed level violates explicit price protection")
    if estimate.spread is None:
        raise GateOrderBookExecutionError("healthy book estimate requires spread evidence")
    if estimate.filled_quantity.value == 0:
        if estimate.quote_amount.value != 0 or estimate.consumed_levels or any(
            item is not None for item in (estimate.weighted_average_price, estimate.best_price, estimate.worst_price)
        ):
            raise GateOrderBookExecutionError("zero-fill estimate cannot invent execution evidence")
    else:
        if not estimate.consumed_levels or any(
            item is None for item in (estimate.weighted_average_price, estimate.best_price, estimate.worst_price)
        ):
            raise GateOrderBookExecutionError("non-zero fill requires complete execution evidence")
        expected_vwap = fit_calculated_decimal(expected_quote / (estimate.filled_quantity.value * multiplier))
        if estimate.weighted_average_price.value != expected_vwap:
            raise GateOrderBookExecutionError("weighted_average_price must match consumed levels")
        if estimate.best_price.value != estimate.consumed_levels[0].price.value or estimate.worst_price.value != estimate.consumed_levels[-1].price.value:
            raise GateOrderBookExecutionError("best and worst prices must match consumed level order")
    if estimate.disposition is GateOrderBookExecutionDisposition.FULLY_FILLABLE:
        if estimate.remaining_quantity.value != 0 or estimate.filled_quantity.value != estimate.request.quantity.value:
            raise GateOrderBookExecutionError("fully fillable estimate must consume the requested quantity")
    elif estimate.remaining_quantity.value <= 0:
        raise GateOrderBookExecutionError("non-fillable estimate must retain positive remaining quantity")
    if estimate.disposition is GateOrderBookExecutionDisposition.PRICE_PROTECTION_REJECTED and estimate.request.price_protection is None:
        raise GateOrderBookExecutionError("price protection rejection requires explicit protection")


def _validate_session_scope(session: GateOrderBookStreamSession, request: GateOrderBookExecutionRequest) -> None:
    if not isinstance(session, GateOrderBookStreamSession):
        raise GateOrderBookExecutionError("session must be typed")
    subscription = session.materialized_state.subscription
    if (
        subscription.market_type is not request.market_type
        or subscription.instrument_id != request.instrument_id
        or subscription.rule_version != request.instrument_rule.rule_version
    ):
        raise GateOrderBookExecutionScopeConflict("session, request, and instrument rule scope must match")


def _canonical_levels(session: GateOrderBookStreamSession, side: GateOrderBookExecutionSide):
    snapshot = session.healthy_snapshot()
    values = snapshot.asks if side is GateOrderBookExecutionSide.BUY else snapshot.bids
    if len({item.price for item in values}) != len(values):
        raise GateOrderBookExecutionError("order book contains duplicate price evidence")
    return tuple(sorted(values, key=lambda item: item.price, reverse=side is GateOrderBookExecutionSide.SELL))


def _price_protected(request: GateOrderBookExecutionRequest, price: Decimal) -> bool:
    if request.price_protection is None:
        return True
    if request.side is GateOrderBookExecutionSide.BUY:
        return price <= request.price_protection.value
    return price >= request.price_protection.value


def estimate_gate_order_book_execution(
    session: GateOrderBookStreamSession,
    request: GateOrderBookExecutionRequest,
) -> GateOrderBookExecutionEstimate:
    """Estimate one visible-depth fill without transport or order authority.

    No missing level is inferred.  If visible depth or an explicit price
    protection prevents a full fill, the returned typed disposition preserves
    the partial evidence and positive remaining quantity rather than silently
    presenting it as a completed fill.
    """

    if not isinstance(request, GateOrderBookExecutionRequest):
        raise GateOrderBookExecutionError("request must be typed")
    _validate_session_scope(session, request)
    try:
        freshness = assess_gate_order_book_stream_session_freshness(
            session,
            as_of=request.as_of,
            max_staleness=request.policy.max_staleness,
        )
    except GateOrderBookStreamSessionError as exc:
        raise GateOrderBookExecutionError("order book session cannot be assessed") from exc
    if freshness.session.health is not GateOrderBookEvidenceHealth.HEALTHY:
        return GateOrderBookExecutionEstimate(
            request=request,
            session_fingerprint=freshness.session.session_fingerprint,
            order_book_evidence_hash=session.materialized_state.snapshot.evidence_hash,
            disposition=GateOrderBookExecutionDisposition.UNHEALTHY_ORDER_BOOK,
            consumed_levels=(),
            filled_quantity=Quantity(Decimal("0")),
            remaining_quantity=request.quantity,
            quote_amount=QuoteAmount(Decimal("0")),
            weighted_average_price=None,
            best_price=None,
            worst_price=None,
            spread=None,
            reason=f"order_book_{freshness.disposition.value.lower()}",
        )
    healthy = freshness.session
    snapshot = healthy.healthy_snapshot()
    levels = _canonical_levels(healthy, request.side)
    multiplier = _base_multiplier(request)
    remaining = request.quantity.value
    quote_total = Decimal("0")
    consumed: list[GateOrderBookLevelConsumption] = []
    blocked_by_price = False
    for index, level in enumerate(levels):
        if not _price_protected(request, level.price):
            blocked_by_price = True
            break
        consumed_quantity = min(remaining, level.quantity)
        if consumed_quantity <= 0:
            continue
        quote = level.price * consumed_quantity * multiplier
        consumed.append(GateOrderBookLevelConsumption(
            level_index=index,
            price=_price(level.price),
            available_quantity=_quantity(level.quantity),
            consumed_quantity=_quantity(consumed_quantity),
            quote_amount=_quote(quote),
        ))
        remaining -= consumed_quantity
        quote_total += quote
        if remaining == 0:
            break
    filled = request.quantity.value - remaining
    if remaining == 0:
        disposition = GateOrderBookExecutionDisposition.FULLY_FILLABLE
        reason = "visible_depth_fills_requested_quantity"
    elif blocked_by_price:
        disposition = GateOrderBookExecutionDisposition.PRICE_PROTECTION_REJECTED
        reason = "visible_depth_exceeds_price_protection"
    else:
        disposition = GateOrderBookExecutionDisposition.INSUFFICIENT_LIQUIDITY
        reason = "visible_depth_is_insufficient"
    best_bid = max(item.price for item in snapshot.bids)
    best_ask = min(item.price for item in snapshot.asks)
    spread = _price(best_ask - best_bid)
    if filled == 0:
        weighted_average_price = best_price = worst_price = None
    else:
        base_quantity = filled * multiplier
        weighted_average_price = _price(quote_total / base_quantity)
        best_price = consumed[0].price
        worst_price = consumed[-1].price
    return GateOrderBookExecutionEstimate(
        request=request,
        session_fingerprint=healthy.session_fingerprint,
        order_book_evidence_hash=snapshot.evidence_hash,
        disposition=disposition,
        consumed_levels=tuple(consumed),
        filled_quantity=_quantity(filled),
        remaining_quantity=_quantity(remaining),
        quote_amount=_quote(quote_total),
        weighted_average_price=weighted_average_price,
        best_price=best_price,
        worst_price=worst_price,
        spread=spread,
        reason=reason,
    )


__all__ = [
    "GATE_ORDER_BOOK_EXECUTION_CONTRACT_VERSION",
    "GateOrderBookExecutionDisposition",
    "GateOrderBookExecutionError",
    "GateOrderBookExecutionEstimate",
    "GateOrderBookExecutionPolicy",
    "GateOrderBookExecutionRequest",
    "GateOrderBookExecutionScopeConflict",
    "GateOrderBookExecutionSide",
    "GateOrderBookLevelConsumption",
    "GateOrderBookQuantityUnit",
    "estimate_gate_order_book_execution",
]
