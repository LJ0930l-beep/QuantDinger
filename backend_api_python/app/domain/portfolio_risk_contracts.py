"""Pure deterministic position-sizing and cooldown risk contracts (PORT-01)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


PORTFOLIO_RISK_CONTRACT_VERSION = "portfolio-risk-v1"


class PortfolioRiskError(ValueError):
    """Invalid or unsafe sizing/risk fact."""


class SizingDisposition(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"


class CooldownState(str, Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) > 127 or c.isspace() for c in value): raise PortfolioRiskError(f"{field} must be canonical ASCII text")
    return value


def _decimal(value: Any, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (float, bool)): raise PortfolioRiskError(f"{field} rejects float/bool input")
    try: result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc: raise PortfolioRiskError(f"{field} is not a decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (non_negative and result < 0): raise PortfolioRiskError(f"{field} has invalid bounds")
    return result


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value): raise PortfolioRiskError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PositionSizingRequest:
    request_fingerprint: str
    instrument_id: str
    mark_price: Decimal
    requested_quantity: Decimal
    available_margin: Decimal
    max_notional: Decimal
    max_leverage: Decimal
    margin_rate: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        for field in ("request_fingerprint", "instrument_id"): _text(getattr(self, field), field)
        for field in ("mark_price", "requested_quantity", "available_margin", "max_notional", "max_leverage"):
            object.__setattr__(self, field, _decimal(getattr(self, field), field, positive=True))
        # A zero margin rate is a valid input fact but never an allowed sizing
        # result: the evaluator must return a typed deny instead of making an
        # invalid infinite-leverage assumption.  Keeping the value representable
        # also makes the guard below reachable and replayable.
        object.__setattr__(self, "margin_rate", _decimal(self.margin_rate, "margin_rate", non_negative=True))
        if self.margin_rate > 1: raise PortfolioRiskError("margin_rate cannot exceed 1")
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))


@dataclass(frozen=True)
class PositionSizingDecision:
    request_fingerprint: str
    disposition: SizingDisposition
    approved_quantity: Decimal
    notional: Decimal
    required_margin: Decimal
    reason: str

    def __post_init__(self) -> None:
        _text(self.request_fingerprint, "request_fingerprint"); _text(self.reason, "reason")
        if not isinstance(self.disposition, SizingDisposition): raise PortfolioRiskError("disposition must be typed")
        object.__setattr__(self, "approved_quantity", _decimal(self.approved_quantity, "approved_quantity", non_negative=True)); object.__setattr__(self, "notional", _decimal(self.notional, "notional", non_negative=True)); object.__setattr__(self, "required_margin", _decimal(self.required_margin, "required_margin", non_negative=True))
        if self.disposition is SizingDisposition.DENIED and (self.approved_quantity or self.notional or self.required_margin): raise PortfolioRiskError("denied decision must carry neutral sizing")


def evaluate_position_sizing(request: PositionSizingRequest) -> PositionSizingDecision:
    quantity = request.requested_quantity
    notional = quantity * request.mark_price
    required_margin = notional * request.margin_rate
    if notional > request.max_notional: return PositionSizingDecision(request.request_fingerprint, SizingDisposition.DENIED, Decimal("0"), Decimal("0"), Decimal("0"), "max_notional_exceeded")
    if request.margin_rate == 0 or (Decimal("1") / request.margin_rate) > request.max_leverage: return PositionSizingDecision(request.request_fingerprint, SizingDisposition.DENIED, Decimal("0"), Decimal("0"), Decimal("0"), "max_leverage_exceeded")
    if required_margin > request.available_margin: return PositionSizingDecision(request.request_fingerprint, SizingDisposition.DENIED, Decimal("0"), Decimal("0"), Decimal("0"), "available_margin_exceeded")
    return PositionSizingDecision(request.request_fingerprint, SizingDisposition.ALLOWED, quantity, notional, required_margin, "within_limits")


@dataclass(frozen=True)
class CooldownFact:
    account_scope: str
    instrument_id: str
    state: CooldownState
    until: datetime | None
    reason: str

    def __post_init__(self) -> None:
        _text(self.account_scope, "account_scope"); _text(self.instrument_id, "instrument_id"); _text(self.reason, "reason")
        if not isinstance(self.state, CooldownState): raise PortfolioRiskError("state must be typed")
        if self.state is CooldownState.ACTIVE and self.until is None: raise PortfolioRiskError("active cooldown requires until")
        if self.until is not None: object.__setattr__(self, "until", _utc(self.until, "until"))


def portfolio_risk_fingerprint(value: Any) -> str:
    def norm(item: Any) -> Any:
        if isinstance(item, Enum): return item.value
        if isinstance(item, Decimal): return format(item.normalize(), "f")
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "__dataclass_fields__"): return norm(asdict(item))
        if isinstance(item, dict): return {str(k): norm(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        if isinstance(item, (tuple, list)): return [norm(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


__all__ = ["PORTFOLIO_RISK_CONTRACT_VERSION", "CooldownFact", "CooldownState", "PortfolioRiskError", "PositionSizingDecision", "PositionSizingRequest", "SizingDisposition", "evaluate_position_sizing", "portfolio_risk_fingerprint"]
