"""Pure fill, economic-order, position, PnL, and fee reducers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import Iterable

from .decimal_values import (
    CALCULATION_POLICY_VERSION,
    CALCULATION_PRECISION,
    FeeAmount,
    PnLAmount,
    Price,
    Quantity,
    QuoteAmount,
    SignedQuantity,
    fit_calculated_decimal,
    validate_numeric_38_18,
)


class ReducerContractError(ValueError):
    """Raised when reducer input cannot be applied deterministically."""


class DuplicateFillEventError(ReducerContractError):
    """Raised when the same immutable fill event is presented more than once."""


class FillSequenceConflictError(ReducerContractError):
    """Raised when two different fill events claim the same sequence."""


class ReducerScopeMismatchError(ReducerContractError):
    """Raised when a fill is outside the reducer's declared scope."""


class FillEconomicOrderMismatchError(ReducerScopeMismatchError):
    """Raised when a fill belongs to another economic order."""


class FillInstrumentMismatchError(ReducerScopeMismatchError):
    """Raised when a fill belongs to another instrument."""


class FillAccountScopeMismatchError(ReducerScopeMismatchError):
    """Raised when a fill belongs to another account scope."""


class FillSideMismatchError(ReducerScopeMismatchError):
    """Raised when a fill side conflicts with an economic order."""


class FillSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


def _required_text(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ReducerContractError(f"{field_name} is required")
    return normalized


def _coerce_fill_side(value: FillSide | str) -> FillSide:
    if isinstance(value, FillSide):
        return value
    if isinstance(value, str):
        try:
            return FillSide(value.strip().upper())
        except ValueError as exc:
            raise ReducerContractError("unknown fill side") from exc
    raise ReducerContractError("unknown fill side")


def _coerce_position_side(value: PositionSide | str) -> PositionSide:
    if isinstance(value, PositionSide):
        return value
    if isinstance(value, str):
        try:
            return PositionSide(value.strip().upper())
        except ValueError as exc:
            raise ReducerContractError("unknown position side") from exc
    raise ReducerContractError("unknown position side")


@dataclass(frozen=True, slots=True)
class EconomicOrderScope:
    economic_order_id: str
    instrument_id: str
    account_scope: str
    expected_side: FillSide

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "economic_order_id", _required_text(self.economic_order_id, "economic_order_id")
        )
        object.__setattr__(
            self, "instrument_id", _required_text(self.instrument_id, "instrument_id")
        )
        object.__setattr__(
            self, "account_scope", _required_text(self.account_scope, "account_scope")
        )
        object.__setattr__(self, "expected_side", _coerce_fill_side(self.expected_side))

    def to_canonical_dict(self) -> dict[str, str]:
        return {
            "economic_order_id": self.economic_order_id,
            "instrument_id": self.instrument_id,
            "account_scope": self.account_scope,
            "expected_side": self.expected_side.value,
        }


@dataclass(frozen=True, slots=True)
class PositionScope:
    instrument_id: str
    account_scope: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _required_text(self.instrument_id, "instrument_id")
        )
        object.__setattr__(
            self, "account_scope", _required_text(self.account_scope, "account_scope")
        )

    def to_canonical_dict(self) -> dict[str, str]:
        return {
            "instrument_id": self.instrument_id,
            "account_scope": self.account_scope,
        }


@dataclass(frozen=True, slots=True)
class Fee:
    asset: str
    amount: FeeAmount

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", _required_text(self.asset, "fee asset").upper())
        if not isinstance(self.amount, FeeAmount):
            raise ReducerContractError("fee amount must be FeeAmount")


@dataclass(frozen=True, slots=True)
class FeeTotal:
    asset: str
    amount: FeeAmount

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", _required_text(self.asset, "fee asset").upper())
        if not isinstance(self.amount, FeeAmount):
            raise ReducerContractError("fee total amount must be FeeAmount")

    def to_canonical_dict(self) -> dict[str, str]:
        return {"asset": self.asset, "amount": self.amount.to_string()}


@dataclass(frozen=True, slots=True)
class FillEvent:
    """Immutable normalized fill input ordered solely by unique sequence."""

    event_id: str
    sequence: int
    economic_order_id: str
    instrument_id: str
    account_scope: str
    side: FillSide
    price: Price
    quantity: Quantity
    quote_quantity: QuoteAmount | None = None
    fees: tuple[Fee, ...] = ()
    instrument_rule_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(
            self, "economic_order_id", _required_text(self.economic_order_id, "economic_order_id")
        )
        object.__setattr__(
            self, "instrument_id", _required_text(self.instrument_id, "instrument_id")
        )
        object.__setattr__(
            self, "account_scope", _required_text(self.account_scope, "account_scope")
        )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise ReducerContractError("fill sequence must be an integer")
        if self.sequence < 0:
            raise ReducerContractError("fill sequence cannot be negative")
        object.__setattr__(self, "side", _coerce_fill_side(self.side))
        if not isinstance(self.price, Price):
            raise ReducerContractError("fill price must be Price")
        if not isinstance(self.quantity, Quantity) or self.quantity.value <= 0:
            raise ReducerContractError("fill quantity must be a positive Quantity")
        if self.quote_quantity is not None:
            if not isinstance(self.quote_quantity, QuoteAmount):
                raise ReducerContractError("quote_quantity must be QuoteAmount")
            if self.quote_quantity.value <= 0:
                raise ReducerContractError("quote_quantity must be positive")
        fees = tuple(self.fees)
        if any(not isinstance(fee, Fee) for fee in fees):
            raise ReducerContractError("fees must contain only Fee values")
        object.__setattr__(self, "fees", fees)
        object.__setattr__(
            self,
            "instrument_rule_version",
            _required_text(self.instrument_rule_version, "instrument_rule_version"),
        )


def _canonicalize_fills(fills: Iterable[FillEvent]) -> tuple[FillEvent, ...]:
    materialized = tuple(fills)
    if any(not isinstance(fill, FillEvent) for fill in materialized):
        raise ReducerContractError("reducers accept only FillEvent values")
    seen_event_ids: set[str] = set()
    seen_sequences: dict[int, str] = {}
    for fill in materialized:
        if fill.event_id in seen_event_ids:
            raise DuplicateFillEventError(f"duplicate fill event_id: {fill.event_id}")
        seen_event_ids.add(fill.event_id)
        prior_event_id = seen_sequences.get(fill.sequence)
        if prior_event_id is not None:
            raise FillSequenceConflictError(
                f"fill sequence {fill.sequence} is shared by {prior_event_id} and {fill.event_id}"
            )
        seen_sequences[fill.sequence] = fill.event_id
    return tuple(sorted(materialized, key=lambda fill: fill.sequence))


def _stable_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def aggregate_fees_by_asset(fees: Iterable[Fee]) -> tuple[FeeTotal, ...]:
    """Aggregate fees without valuing or combining different assets."""

    totals: dict[str, Decimal] = {}
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        for fee in fees:
            if not isinstance(fee, Fee):
                raise ReducerContractError("aggregate_fees_by_asset requires Fee values")
            totals[fee.asset] = totals.get(fee.asset, Decimal(0)) + fee.amount.value
    return tuple(
        FeeTotal(asset=asset, amount=FeeAmount(validate_numeric_38_18(amount)))
        for asset, amount in sorted(totals.items())
    )


@dataclass(frozen=True, slots=True)
class EconomicOrderReduction:
    scope: EconomicOrderScope
    target_quantity: Quantity
    quantity_tolerance: Quantity
    cumulative_filled_quantity: Quantity
    cumulative_quote_quantity: QuoteAmount
    weighted_average_fill_price: Price | None
    cumulative_fee: tuple[FeeTotal, ...]
    remaining_quantity: Quantity
    overfill_quantity: Quantity
    reached_target_within_tolerance: bool
    applied_event_ids: tuple[str, ...]
    derived_quote_event_ids: tuple[str, ...]
    calculation_policy_version: str = CALCULATION_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scope, EconomicOrderScope):
            raise ReducerContractError("scope must be EconomicOrderScope")
        decimal_fields = (
            ("target_quantity", self.target_quantity, Quantity),
            ("quantity_tolerance", self.quantity_tolerance, Quantity),
            ("cumulative_filled_quantity", self.cumulative_filled_quantity, Quantity),
            ("cumulative_quote_quantity", self.cumulative_quote_quantity, QuoteAmount),
            ("remaining_quantity", self.remaining_quantity, Quantity),
            ("overfill_quantity", self.overfill_quantity, Quantity),
        )
        for field_name, value, expected_type in decimal_fields:
            if not isinstance(value, expected_type):
                raise ReducerContractError(
                    f"{field_name} must be {expected_type.__name__}"
                )
        if self.weighted_average_fill_price is not None and not isinstance(
            self.weighted_average_fill_price, Price
        ):
            raise ReducerContractError(
                "weighted_average_fill_price must be Price or None"
            )
        cumulative_fee = tuple(self.cumulative_fee)
        if any(not isinstance(item, FeeTotal) for item in cumulative_fee):
            raise ReducerContractError("cumulative_fee must contain FeeTotal values")
        object.__setattr__(self, "cumulative_fee", cumulative_fee)
        object.__setattr__(self, "applied_event_ids", tuple(self.applied_event_ids))
        object.__setattr__(
            self, "derived_quote_event_ids", tuple(self.derived_quote_event_ids)
        )
        if self.calculation_policy_version != CALCULATION_POLICY_VERSION:
            raise ReducerContractError("unsupported calculation policy version")

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope.to_canonical_dict(),
            "target_quantity": self.target_quantity.to_string(),
            "quantity_tolerance": self.quantity_tolerance.to_string(),
            "cumulative_filled_quantity": self.cumulative_filled_quantity.to_string(),
            "cumulative_quote_quantity": self.cumulative_quote_quantity.to_string(),
            "weighted_average_fill_price": (
                self.weighted_average_fill_price.to_string()
                if self.weighted_average_fill_price is not None
                else None
            ),
            "cumulative_fee": [fee.to_canonical_dict() for fee in self.cumulative_fee],
            "remaining_quantity": self.remaining_quantity.to_string(),
            "overfill_quantity": self.overfill_quantity.to_string(),
            "reached_target_within_tolerance": self.reached_target_within_tolerance,
            "applied_event_ids": list(self.applied_event_ids),
            "derived_quote_event_ids": list(self.derived_quote_event_ids),
            "calculation_policy_version": self.calculation_policy_version,
        }

    def stable_hash(self) -> str:
        return _stable_hash(self.to_canonical_dict())


def reduce_economic_order(
    target_quantity: Quantity,
    fills: Iterable[FillEvent],
    *,
    scope: EconomicOrderScope,
    quantity_tolerance: Quantity,
) -> EconomicOrderReduction:
    if not isinstance(scope, EconomicOrderScope):
        raise ReducerContractError("scope must be EconomicOrderScope")
    if not isinstance(target_quantity, Quantity) or target_quantity.value <= 0:
        raise ReducerContractError("target_quantity must be a positive Quantity")
    if not isinstance(quantity_tolerance, Quantity):
        raise ReducerContractError("quantity_tolerance must be Quantity")
    if quantity_tolerance.value > target_quantity.value:
        raise ReducerContractError("quantity_tolerance cannot exceed target_quantity")

    ordered = _canonicalize_fills(fills)
    for fill in ordered:
        if fill.economic_order_id != scope.economic_order_id:
            raise FillEconomicOrderMismatchError(
                f"fill {fill.event_id} economic_order_id does not match reducer scope"
            )
        if fill.instrument_id != scope.instrument_id:
            raise FillInstrumentMismatchError(
                f"fill {fill.event_id} instrument_id does not match reducer scope"
            )
        if fill.account_scope != scope.account_scope:
            raise FillAccountScopeMismatchError(
                f"fill {fill.event_id} account_scope does not match reducer scope"
            )
        if fill.side is not scope.expected_side:
            raise FillSideMismatchError(
                f"fill {fill.event_id} side does not match reducer expected_side"
            )
    cumulative_quantity = Decimal(0)
    cumulative_quote = Decimal(0)
    weighted_price_numerator = Decimal(0)
    all_fees: list[Fee] = []
    derived_quote_event_ids: list[str] = []

    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        for fill in ordered:
            cumulative_quantity += fill.quantity.value
            weighted_price_numerator += fill.price.value * fill.quantity.value
            if fill.quote_quantity is None:
                derived_quote = fit_calculated_decimal(
                    fill.price.value * fill.quantity.value
                )
                cumulative_quote += derived_quote
                derived_quote_event_ids.append(fill.event_id)
            else:
                cumulative_quote += fill.quote_quantity.value
            all_fees.extend(fill.fees)

        cumulative_quantity = validate_numeric_38_18(cumulative_quantity)
        cumulative_quote = validate_numeric_38_18(cumulative_quote)
        average = (
            fit_calculated_decimal(weighted_price_numerator / cumulative_quantity)
            if cumulative_quantity > 0
            else None
        )
        remaining = max(target_quantity.value - cumulative_quantity, Decimal(0))
        overfill = max(cumulative_quantity - target_quantity.value, Decimal(0))

    return EconomicOrderReduction(
        scope=scope,
        target_quantity=target_quantity,
        quantity_tolerance=quantity_tolerance,
        cumulative_filled_quantity=Quantity(cumulative_quantity),
        cumulative_quote_quantity=QuoteAmount(cumulative_quote),
        weighted_average_fill_price=Price(average) if average is not None else None,
        cumulative_fee=aggregate_fees_by_asset(all_fees),
        remaining_quantity=Quantity(remaining),
        overfill_quantity=Quantity(overfill),
        reached_target_within_tolerance=remaining <= quantity_tolerance.value,
        applied_event_ids=tuple(fill.event_id for fill in ordered),
        derived_quote_event_ids=tuple(derived_quote_event_ids),
    )


def calculate_realized_pnl(
    side: PositionSide | str,
    entry_price: Price,
    exit_price: Price,
    quantity: Quantity,
) -> PnLAmount:
    position_side = _coerce_position_side(side)
    if not isinstance(entry_price, Price) or not isinstance(exit_price, Price):
        raise ReducerContractError("entry_price and exit_price must be Price")
    if not isinstance(quantity, Quantity):
        raise ReducerContractError("quantity must be Quantity")
    direction = Decimal(1) if position_side is PositionSide.LONG else Decimal(-1)
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        raw = (exit_price.value - entry_price.value) * quantity.value * direction
    return PnLAmount(fit_calculated_decimal(raw))


@dataclass(frozen=True, slots=True)
class PositionState:
    """Rebuildable one-way position; positive quantity is long, negative short."""

    scope: PositionScope
    signed_quantity: SignedQuantity
    average_entry_price: Price | None
    realized_pnl: PnLAmount
    closed_quantity: Quantity
    applied_event_ids: tuple[str, ...]
    calculation_policy_version: str = CALCULATION_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scope, PositionScope):
            raise ReducerContractError("scope must be PositionScope")
        if not isinstance(self.signed_quantity, SignedQuantity):
            raise ReducerContractError("signed_quantity must be SignedQuantity")
        if not isinstance(self.realized_pnl, PnLAmount):
            raise ReducerContractError("realized_pnl must be PnLAmount")
        if not isinstance(self.closed_quantity, Quantity):
            raise ReducerContractError("closed_quantity must be Quantity")
        if self.average_entry_price is not None and not isinstance(
            self.average_entry_price, Price
        ):
            raise ReducerContractError("average_entry_price must be Price or None")
        object.__setattr__(self, "applied_event_ids", tuple(self.applied_event_ids))
        if self.calculation_policy_version != CALCULATION_POLICY_VERSION:
            raise ReducerContractError("unsupported calculation policy version")
        if self.signed_quantity.value == 0 and self.average_entry_price is not None:
            raise ReducerContractError("flat position cannot have average_entry_price")
        if self.signed_quantity.value != 0 and self.average_entry_price is None:
            raise ReducerContractError("open position requires average_entry_price")

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope.to_canonical_dict(),
            "signed_quantity": self.signed_quantity.to_string(),
            "average_entry_price": (
                self.average_entry_price.to_string()
                if self.average_entry_price is not None
                else None
            ),
            "realized_pnl": self.realized_pnl.to_string(),
            "closed_quantity": self.closed_quantity.to_string(),
            "applied_event_ids": list(self.applied_event_ids),
            "calculation_policy_version": self.calculation_policy_version,
        }

    def stable_hash(self) -> str:
        return _stable_hash(self.to_canonical_dict())


def reduce_position(
    fills: Iterable[FillEvent], *, scope: PositionScope
) -> PositionState:
    """Rebuild a one-way position from zero using normalized fill events."""

    if not isinstance(scope, PositionScope):
        raise ReducerContractError("scope must be PositionScope")
    ordered = _canonicalize_fills(fills)
    for fill in ordered:
        if fill.instrument_id != scope.instrument_id:
            raise FillInstrumentMismatchError(
                f"fill {fill.event_id} instrument_id does not match reducer scope"
            )
        if fill.account_scope != scope.account_scope:
            raise FillAccountScopeMismatchError(
                f"fill {fill.event_id} account_scope does not match reducer scope"
            )
    signed_quantity = Decimal(0)
    average_entry_price: Decimal | None = None
    realized_pnl = Decimal(0)
    closed_quantity = Decimal(0)

    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        for fill in ordered:
            delta = fill.quantity.value if fill.side is FillSide.BUY else -fill.quantity.value
            if signed_quantity == 0:
                signed_quantity = delta
                average_entry_price = fill.price.value
                continue

            same_direction = (signed_quantity > 0 and delta > 0) or (
                signed_quantity < 0 and delta < 0
            )
            if same_direction:
                new_signed_quantity = signed_quantity + delta
                weighted_cost = (
                    abs(signed_quantity) * average_entry_price
                    + abs(delta) * fill.price.value
                )
                average_entry_price = fit_calculated_decimal(
                    weighted_cost / abs(new_signed_quantity)
                )
                signed_quantity = validate_numeric_38_18(new_signed_quantity)
                continue

            closing_quantity = min(abs(signed_quantity), abs(delta))
            closing_side = (
                PositionSide.LONG if signed_quantity > 0 else PositionSide.SHORT
            )
            fill_realized = calculate_realized_pnl(
                closing_side,
                Price(average_entry_price),
                fill.price,
                Quantity(closing_quantity),
            )
            realized_pnl = fit_calculated_decimal(
                realized_pnl + fill_realized.value
            )
            closed_quantity = validate_numeric_38_18(
                closed_quantity + closing_quantity
            )
            new_signed_quantity = validate_numeric_38_18(
                signed_quantity + delta
            )
            if new_signed_quantity == 0:
                average_entry_price = None
            elif (new_signed_quantity > 0) == (signed_quantity > 0):
                pass
            else:
                average_entry_price = fill.price.value
            signed_quantity = new_signed_quantity

    return PositionState(
        scope=scope,
        signed_quantity=SignedQuantity(signed_quantity),
        average_entry_price=(
            Price(average_entry_price) if average_entry_price is not None else None
        ),
        realized_pnl=PnLAmount(realized_pnl),
        closed_quantity=Quantity(closed_quantity),
        applied_event_ids=tuple(fill.event_id for fill in ordered),
    )


def calculate_unrealized_pnl(
    signed_quantity: SignedQuantity,
    average_entry_price: Price | None,
    mark_price: Price,
) -> PnLAmount:
    if not isinstance(signed_quantity, SignedQuantity):
        raise ReducerContractError("signed_quantity must be SignedQuantity")
    if not isinstance(mark_price, Price):
        raise ReducerContractError("mark_price must be Price")
    if signed_quantity.value == 0:
        if average_entry_price is not None:
            raise ReducerContractError("flat position cannot have average_entry_price")
        return PnLAmount(Decimal(0))
    if not isinstance(average_entry_price, Price):
        raise ReducerContractError("open position requires average_entry_price")
    side = PositionSide.LONG if signed_quantity.value > 0 else PositionSide.SHORT
    return calculate_realized_pnl(
        side,
        average_entry_price,
        mark_price,
        Quantity(abs(signed_quantity.value)),
    )
