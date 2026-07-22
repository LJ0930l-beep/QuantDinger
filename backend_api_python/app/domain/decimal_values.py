"""Immutable decimal value objects for trading-domain calculations.

The values in this module are deliberately independent from Flask, database
models, and exchange clients.  They enforce the future ``NUMERIC(38,18)``
storage contract without creating or changing any database schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import ClassVar, TypeAlias


DecimalInput: TypeAlias = Decimal | str | int

NUMERIC_PRECISION = 38
NUMERIC_SCALE = 18
NUMERIC_INTEGER_DIGITS = NUMERIC_PRECISION - NUMERIC_SCALE
NUMERIC_QUANTUM = Decimal("0.000000000000000001")
NUMERIC_MAX_ABS = Decimal("99999999999999999999.999999999999999999")
CALCULATION_PRECISION = 80
CALCULATION_POLICY_VERSION = "numeric-38-18-half-even-v1"


class DecimalValueError(ValueError):
    """Raised when a value violates the trading decimal contract."""


class DecimalInputTypeError(TypeError):
    """Raised when a value could introduce binary floating-point ambiguity."""


def _reject_binary_float(value: object) -> None:
    if isinstance(value, float):
        raise DecimalInputTypeError(
            "binary float input is forbidden; use Decimal, str, or int"
        )
    if isinstance(value, bool):
        raise DecimalInputTypeError("bool is not a valid decimal input")


def _parse_decimal(value: DecimalInput) -> Decimal:
    _reject_binary_float(value)
    if not isinstance(value, (Decimal, str, int)):
        raise DecimalInputTypeError(
            f"unsupported decimal input type: {type(value).__name__}"
        )
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise DecimalValueError("invalid decimal value") from exc
    if not parsed.is_finite():
        raise DecimalValueError("NaN and Infinity are forbidden")
    return Decimal(0) if parsed.is_zero() else parsed


def decimal_scale(value: Decimal) -> int:
    """Return significant fractional scale, ignoring insignificant zeros."""

    if value.is_zero():
        return 0
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        normalized = value.normalize()
    return max(-normalized.as_tuple().exponent, 0)


def validate_numeric_38_18(value: DecimalInput) -> Decimal:
    """Parse and validate a value against ``NUMERIC(38,18)`` exactly."""

    parsed = _parse_decimal(value)
    if parsed.copy_abs() > NUMERIC_MAX_ABS:
        raise DecimalValueError("value exceeds NUMERIC(38,18) magnitude")
    if decimal_scale(parsed) > NUMERIC_SCALE:
        raise DecimalValueError("value exceeds NUMERIC(38,18) scale")
    return parsed


def fit_calculated_decimal(
    value: Decimal,
    *,
    rounding: str = ROUND_HALF_EVEN,
) -> Decimal:
    """Fit a calculated Decimal to the versioned 38,18 calculation policy.

    Inputs here must already be Decimal results from domain arithmetic.  The
    function never accepts binary floats and rejects magnitude overflow before
    rounding.  Calculation results are rounded to scale 18 using the supplied
    explicit Decimal rounding mode.
    """

    _reject_binary_float(value)
    if not isinstance(value, Decimal):
        raise DecimalInputTypeError("calculation result must be Decimal")
    if not value.is_finite():
        raise DecimalValueError("NaN and Infinity are forbidden")
    if value.copy_abs() > NUMERIC_MAX_ABS:
        raise DecimalValueError("calculation exceeds NUMERIC(38,18) magnitude")
    with localcontext() as context:
        context.prec = CALCULATION_PRECISION
        try:
            fitted = value.quantize(NUMERIC_QUANTUM, rounding=rounding)
        except InvalidOperation as exc:
            raise DecimalValueError("calculation cannot fit NUMERIC(38,18)") from exc
    return validate_numeric_38_18(fitted)


def canonical_decimal_string(value: Decimal) -> str:
    """Serialize a finite Decimal without scientific notation."""

    parsed = validate_numeric_38_18(value)
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


@dataclass(frozen=True, slots=True)
class DecimalValue:
    """Base class for immutable, storage-safe decimal domain values."""

    value: Decimal

    allow_negative: ClassVar[bool] = True
    allow_zero: ClassVar[bool] = True

    def __post_init__(self) -> None:
        parsed = validate_numeric_38_18(self.value)
        if not self.allow_negative and parsed < 0:
            raise DecimalValueError(f"{type(self).__name__} cannot be negative")
        if not self.allow_zero and parsed == 0:
            raise DecimalValueError(f"{type(self).__name__} must be greater than zero")
        object.__setattr__(self, "value", parsed)

    def to_decimal(self) -> Decimal:
        return self.value

    def to_string(self) -> str:
        return canonical_decimal_string(self.value)

    def __str__(self) -> str:
        return self.to_string()


@dataclass(frozen=True, slots=True)
class Quantity(DecimalValue):
    allow_negative: ClassVar[bool] = False


@dataclass(frozen=True, slots=True)
class SignedQuantity(DecimalValue):
    """Signed quantity: positive is long, negative is short."""


@dataclass(frozen=True, slots=True)
class Price(DecimalValue):
    allow_negative: ClassVar[bool] = False
    allow_zero: ClassVar[bool] = False


@dataclass(frozen=True, slots=True)
class QuoteAmount(DecimalValue):
    allow_negative: ClassVar[bool] = False


@dataclass(frozen=True, slots=True)
class FeeAmount(DecimalValue):
    allow_negative: ClassVar[bool] = False


@dataclass(frozen=True, slots=True)
class PnLAmount(DecimalValue):
    """Signed profit/loss amount in an explicitly named valuation asset."""
