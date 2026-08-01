"""Read-only Gate leverage and margin evidence (GATE-06/07)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Tuple

from .multi_asset_capability_contracts import AssetMarketType


GATE_MARGIN_CONTRACT_VERSION = "gate-margin-read-v1"


class GateMarginContractError(ValueError):
    """Malformed, incomplete, or unsafe margin evidence."""


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) > 127 or c.isspace() for c in value): raise GateMarginContractError(f"{field} must be canonical ASCII text")
    return value


def _decimal(value: Any, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (float, bool)): raise GateMarginContractError(f"{field} rejects float/bool input")
    try: result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc: raise GateMarginContractError(f"{field} is not a decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (non_negative and result < 0): raise GateMarginContractError(f"{field} has invalid bounds")
    return result


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value): raise GateMarginContractError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class GateLeverageTier:
    instrument_id: str
    tier: int
    notional_floor: Decimal
    notional_ceiling: Decimal
    max_leverage: Decimal
    maintenance_margin_rate: Decimal
    rule_version: str

    def __post_init__(self) -> None:
        _text(self.instrument_id, "instrument_id"); _text(self.rule_version, "rule_version")
        if not isinstance(self.tier, int) or isinstance(self.tier, bool) or self.tier <= 0: raise GateMarginContractError("tier must be positive")
        object.__setattr__(self, "notional_floor", _decimal(self.notional_floor, "notional_floor", non_negative=True))
        for field in ("notional_ceiling", "max_leverage", "maintenance_margin_rate"): object.__setattr__(self, field, _decimal(getattr(self, field), field, positive=True))
        if self.notional_ceiling <= self.notional_floor or self.maintenance_margin_rate > 1: raise GateMarginContractError("tier bounds are inconsistent")


@dataclass(frozen=True)
class GateMarginSnapshot:
    market_type: AssetMarketType
    account_scope: str
    instrument_id: str
    margin_currency: str
    equity: Decimal
    available_margin: Decimal
    used_margin: Decimal
    maintenance_margin: Decimal
    leverage_tiers: Tuple[GateLeverageTier, ...]
    observed_at: datetime
    source_event_id: str
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.market_type is not AssetMarketType.PERPETUAL: raise GateMarginContractError("margin snapshot is perpetual-only")
        _text(self.account_scope, "account_scope"); _text(self.instrument_id, "instrument_id"); _text(self.margin_currency, "margin_currency"); _text(self.source_event_id, "source_event_id"); _text(self.evidence_hash, "evidence_hash")
        for field in ("equity", "available_margin", "used_margin", "maintenance_margin"): object.__setattr__(self, field, _decimal(getattr(self, field), field, non_negative=True))
        if self.available_margin + self.used_margin > self.equity: raise GateMarginContractError("available plus used margin exceeds equity")
        if not self.leverage_tiers or any(not isinstance(t, GateLeverageTier) or t.instrument_id != self.instrument_id for t in self.leverage_tiers): raise GateMarginContractError("complete instrument tier evidence is required")
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))


def gate_margin_fingerprint(value: Any) -> str:
    def norm(item: Any) -> Any:
        if isinstance(item, Decimal): return format(item.normalize(), "f")
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "value") and not isinstance(item, (str, bytes)): return item.value
        if hasattr(item, "__dataclass_fields__"): return norm(asdict(item))
        if isinstance(item, dict): return {str(k): norm(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        if isinstance(item, (tuple, list)): return [norm(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


__all__ = ["GATE_MARGIN_CONTRACT_VERSION", "GateLeverageTier", "GateMarginContractError", "GateMarginSnapshot", "gate_margin_fingerprint"]
