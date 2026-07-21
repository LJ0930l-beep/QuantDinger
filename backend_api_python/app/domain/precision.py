"""Versioned, immutable instrument precision snapshots and pure quantizers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, Decimal, localcontext
from enum import Enum

from .decimal_values import (
    CALCULATION_PRECISION,
    Price,
    Quantity,
    QuoteAmount,
    decimal_scale,
    fit_calculated_decimal,
    validate_numeric_38_18,
)


SUPPORTED_ROUNDING_POLICY_VERSIONS = frozenset({"v1"})


class PrecisionContractError(ValueError):
    """Raised when a precision snapshot or operation is unsafe or unknown."""


class RoundingPolicy(str, Enum):
    ROUND_DOWN = "ROUND_DOWN"
    ROUND_UP = "ROUND_UP"
    ROUND_HALF_EVEN = "ROUND_HALF_EVEN"


_DECIMAL_ROUNDING = {
    RoundingPolicy.ROUND_DOWN: ROUND_DOWN,
    RoundingPolicy.ROUND_UP: ROUND_UP,
    RoundingPolicy.ROUND_HALF_EVEN: ROUND_HALF_EVEN,
}


def _require_non_empty(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise PrecisionContractError(f"{field_name} is required")
    return normalized


def _require_scale(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PrecisionContractError(f"{field_name} must be an integer")
    if not 0 <= value <= 18:
        raise PrecisionContractError(f"{field_name} must be between 0 and 18")
    return value


@dataclass(frozen=True, slots=True)
class InstrumentPrecisionSnapshot:
    instrument_id: str
    exchange_id: str
    market_type: str
    tick_size: Price
    quantity_step: Quantity
    minimum_quantity: Quantity
    minimum_notional: QuoteAmount
    price_scale: int
    quantity_scale: int
    rounding_policy_version: str
    instrument_rule_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _require_non_empty(self.instrument_id, "instrument_id")
        )
        object.__setattr__(
            self, "exchange_id", _require_non_empty(self.exchange_id, "exchange_id").lower()
        )
        market_type = _require_non_empty(self.market_type, "market_type").lower()
        if market_type not in {"spot", "swap"}:
            raise PrecisionContractError("market_type must be spot or swap")
        object.__setattr__(self, "market_type", market_type)
        if not isinstance(self.tick_size, Price):
            raise PrecisionContractError("tick_size must be Price")
        if not isinstance(self.quantity_step, Quantity) or self.quantity_step.value <= 0:
            raise PrecisionContractError("quantity_step must be a positive Quantity")
        if not isinstance(self.minimum_quantity, Quantity):
            raise PrecisionContractError("minimum_quantity must be Quantity")
        if not isinstance(self.minimum_notional, QuoteAmount):
            raise PrecisionContractError("minimum_notional must be QuoteAmount")
        price_scale = _require_scale(self.price_scale, "price_scale")
        quantity_scale = _require_scale(self.quantity_scale, "quantity_scale")
        if decimal_scale(self.tick_size.value) > price_scale:
            raise PrecisionContractError("tick_size exceeds price_scale")
        if decimal_scale(self.quantity_step.value) > quantity_scale:
            raise PrecisionContractError("quantity_step exceeds quantity_scale")
        if decimal_scale(self.minimum_quantity.value) > quantity_scale:
            raise PrecisionContractError("minimum_quantity exceeds quantity_scale")
        object.__setattr__(self, "price_scale", price_scale)
        object.__setattr__(self, "quantity_scale", quantity_scale)
        object.__setattr__(
            self,
            "rounding_policy_version",
            _require_non_empty(self.rounding_policy_version, "rounding_policy_version"),
        )
        object.__setattr__(
            self,
            "instrument_rule_version",
            _require_non_empty(self.instrument_rule_version, "instrument_rule_version"),
        )


def _ensure_supported_snapshot(snapshot: InstrumentPrecisionSnapshot) -> None:
    if not isinstance(snapshot, InstrumentPrecisionSnapshot):
        raise PrecisionContractError("unknown precision snapshot")
    if snapshot.rounding_policy_version not in SUPPORTED_ROUNDING_POLICY_VERSIONS:
        raise PrecisionContractError(
            f"unsupported rounding policy version: {snapshot.rounding_policy_version}"
        )


def _coerce_rounding_policy(policy: RoundingPolicy | str | None) -> RoundingPolicy:
    if isinstance(policy, RoundingPolicy):
        return policy
    if isinstance(policy, str):
        try:
            return RoundingPolicy(policy.strip().upper())
        except ValueError as exc:
            raise PrecisionContractError("unknown rounding policy") from exc
    raise PrecisionContractError("rounding policy must be explicit")


def _quantize_to_step(
    value: Decimal,
    step: Decimal,
    policy: RoundingPolicy,
) -> Decimal:
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        units = (value / step).to_integral_value(rounding=_DECIMAL_ROUNDING[policy])
        quantized = units * step
    return validate_numeric_38_18(quantized)


def quantize_price(
    snapshot: InstrumentPrecisionSnapshot,
    price: Price,
    *,
    policy: RoundingPolicy | str | None = None,
) -> Price:
    _ensure_supported_snapshot(snapshot)
    if not isinstance(price, Price):
        raise PrecisionContractError("price must be Price")
    rounding = _coerce_rounding_policy(policy)
    return Price(_quantize_to_step(price.value, snapshot.tick_size.value, rounding))


def quantize_quantity(
    snapshot: InstrumentPrecisionSnapshot,
    quantity: Quantity,
    *,
    policy: RoundingPolicy | str | None = None,
) -> Quantity:
    _ensure_supported_snapshot(snapshot)
    if not isinstance(quantity, Quantity):
        raise PrecisionContractError("quantity must be Quantity")
    rounding = _coerce_rounding_policy(policy)
    return Quantity(
        _quantize_to_step(quantity.value, snapshot.quantity_step.value, rounding)
    )


def validate_minimum_quantity(
    snapshot: InstrumentPrecisionSnapshot,
    quantity: Quantity,
) -> bool:
    _ensure_supported_snapshot(snapshot)
    if not isinstance(quantity, Quantity):
        raise PrecisionContractError("quantity must be Quantity")
    return quantity.value >= snapshot.minimum_quantity.value


def validate_minimum_notional(
    snapshot: InstrumentPrecisionSnapshot,
    notional: QuoteAmount,
) -> bool:
    _ensure_supported_snapshot(snapshot)
    if not isinstance(notional, QuoteAmount):
        raise PrecisionContractError("notional must be QuoteAmount")
    return notional.value >= snapshot.minimum_notional.value


def calculate_notional(quantity: Quantity, price: Price) -> QuoteAmount:
    if not isinstance(quantity, Quantity) or not isinstance(price, Price):
        raise PrecisionContractError("calculate_notional requires Quantity and Price")
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        raw = quantity.value * price.value
    return QuoteAmount(fit_calculated_decimal(raw))
