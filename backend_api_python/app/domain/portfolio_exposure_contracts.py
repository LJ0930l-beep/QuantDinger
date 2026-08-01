"""Pure portfolio exposure aggregation and limit contracts.

This module consumes caller-owned, read-only position facts. It does not read
a database, call a venue, reserve margin, or submit an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Tuple


PORTFOLIO_EXPOSURE_CONTRACT_VERSION = "portfolio-exposure-v1"


class PortfolioExposureError(ValueError):
    """Invalid, ambiguous, or incomplete portfolio exposure facts."""


class ExposureSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExposureLimitDisposition(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise PortfolioExposureError(f"{field} must be canonical ASCII text")
    return value


def _decimal(value: Any, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise PortfolioExposureError(f"{field} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PortfolioExposureError(f"{field} must be decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (non_negative and result < 0):
        raise PortfolioExposureError(f"{field} has invalid bounds")
    return result


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise PortfolioExposureError(f"{field} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PositionExposureFact:
    position_id: str
    account_scope: str
    instrument_id: str
    side: ExposureSide
    quantity: Decimal
    mark_price: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        _text(self.position_id, "position_id"); _text(self.account_scope, "account_scope"); _text(self.instrument_id, "instrument_id")
        if not isinstance(self.side, ExposureSide):
            raise PortfolioExposureError("side must be typed")
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", non_negative=True))
        object.__setattr__(self, "mark_price", _decimal(self.mark_price, "mark_price", positive=True))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))

    @property
    def signed_notional(self) -> Decimal:
        value = self.quantity * self.mark_price
        return value if self.side is ExposureSide.LONG else -value

    @property
    def gross_notional(self) -> Decimal:
        return self.quantity * self.mark_price


@dataclass(frozen=True, slots=True)
class PortfolioExposureSnapshot:
    account_scope: str
    as_of: datetime
    positions: Tuple[PositionExposureFact, ...]
    gross_exposure: Decimal = field(init=False)
    net_exposure: Decimal = field(init=False)

    def __post_init__(self) -> None:
        _text(self.account_scope, "account_scope")
        cutoff = _utc(self.as_of, "as_of")
        if not isinstance(self.positions, tuple) or any(not isinstance(item, PositionExposureFact) for item in self.positions):
            raise PortfolioExposureError("positions must be an explicit typed tuple")
        ids = [item.position_id for item in self.positions]
        if len(ids) != len(set(ids)):
            raise PortfolioExposureError("position_id must be unique")
        if any(item.account_scope != self.account_scope for item in self.positions):
            raise PortfolioExposureError("position scope mismatch")
        if any(item.observed_at > cutoff for item in self.positions):
            raise PortfolioExposureError("position fact is after snapshot as_of")
        object.__setattr__(self, "as_of", cutoff)
        object.__setattr__(self, "gross_exposure", sum((item.gross_notional for item in self.positions), Decimal("0")))
        object.__setattr__(self, "net_exposure", sum((item.signed_notional for item in self.positions), Decimal("0")))


@dataclass(frozen=True, slots=True)
class PortfolioExposureLimitDecision:
    disposition: ExposureLimitDisposition
    projected_gross_exposure: Decimal
    projected_net_exposure: Decimal
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, ExposureLimitDisposition):
            raise PortfolioExposureError("disposition must be typed")
        object.__setattr__(self, "projected_gross_exposure", _decimal(self.projected_gross_exposure, "projected_gross_exposure", non_negative=True))
        object.__setattr__(self, "projected_net_exposure", _decimal(self.projected_net_exposure, "projected_net_exposure"))
        _text(self.reason, "reason")


def evaluate_portfolio_exposure_limit(snapshot: PortfolioExposureSnapshot, *, side: ExposureSide, additional_quantity: Decimal, mark_price: Decimal, max_gross_exposure: Decimal, max_abs_net_exposure: Decimal) -> PortfolioExposureLimitDecision:
    """Evaluate an increase against gross and absolute-net limits."""
    if not isinstance(snapshot, PortfolioExposureSnapshot) or not isinstance(side, ExposureSide):
        raise PortfolioExposureError("typed snapshot and side are required")
    quantity = _decimal(additional_quantity, "additional_quantity", non_negative=True)
    price = _decimal(mark_price, "mark_price", positive=True)
    gross_limit = _decimal(max_gross_exposure, "max_gross_exposure", non_negative=True)
    net_limit = _decimal(max_abs_net_exposure, "max_abs_net_exposure", non_negative=True)
    delta = quantity * price
    projected_gross = snapshot.gross_exposure + delta
    projected_net = snapshot.net_exposure + (delta if side is ExposureSide.LONG else -delta)
    if projected_gross > gross_limit:
        return PortfolioExposureLimitDecision(ExposureLimitDisposition.DENIED, projected_gross, projected_net, "gross_exposure_exceeded")
    if abs(projected_net) > net_limit:
        return PortfolioExposureLimitDecision(ExposureLimitDisposition.DENIED, projected_gross, projected_net, "net_exposure_exceeded")
    return PortfolioExposureLimitDecision(ExposureLimitDisposition.ALLOWED, projected_gross, projected_net, "within_limits")


__all__ = ["PORTFOLIO_EXPOSURE_CONTRACT_VERSION", "ExposureLimitDisposition", "ExposureSide", "PortfolioExposureError", "PortfolioExposureLimitDecision", "PortfolioExposureSnapshot", "PositionExposureFact", "evaluate_portfolio_exposure_limit"]
