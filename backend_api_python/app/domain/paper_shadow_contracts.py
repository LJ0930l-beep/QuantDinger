"""Pure PAPER/SHADOW simulation contracts (PS-01).

Simulation decisions are explicit, deterministic facts.  There is no LIVE
mode, exchange client, order submission, or persistence in this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


PAPER_SHADOW_CONTRACT_VERSION = "paper-shadow-v1"


class PaperShadowContractError(ValueError):
    """Base error for unsafe or incomplete simulation facts."""


class SimulationMode(str, Enum):
    DISABLED = "DISABLED"
    PAPER = "PAPER"
    SHADOW = "SHADOW"


class SimulationDisposition(str, Enum):
    DISABLED = "DISABLED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) > 127 or c.isspace() for c in value):
        raise PaperShadowContractError(f"{field} must be canonical ASCII text")
    return value


def _reason(value: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) < 32 for c in value):
        raise PaperShadowContractError("reason must be canonical text")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise PaperShadowContractError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise PaperShadowContractError(f"{field} rejects float/bool input")
    try: result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc: raise PaperShadowContractError(f"{field} is not a decimal") from exc
    if not result.is_finite(): raise PaperShadowContractError(f"{field} must be finite")
    return result


@dataclass(frozen=True)
class PaperShadowRunFacts:
    run_id: str
    mode: SimulationMode
    dataset_snapshot_id: str
    strategy_fingerprint: str
    risk_policy_fingerprint: str
    tolerance_policy_fingerprint: str
    started_at: datetime
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id"); _text(self.dataset_snapshot_id, "dataset_snapshot_id")
        _text(self.strategy_fingerprint, "strategy_fingerprint"); _text(self.risk_policy_fingerprint, "risk_policy_fingerprint"); _text(self.tolerance_policy_fingerprint, "tolerance_policy_fingerprint")
        if self.mode is SimulationMode.DISABLED: raise PaperShadowContractError("disabled mode cannot create a run")
        if not isinstance(self.mode, SimulationMode): raise PaperShadowContractError("mode must be typed")
        started = _utc(self.started_at, "started_at"); object.__setattr__(self, "started_at", started)
        if self.ended_at is not None:
            ended = _utc(self.ended_at, "ended_at")
            if ended < started: raise PaperShadowContractError("ended_at cannot precede started_at")
            object.__setattr__(self, "ended_at", ended)


@dataclass(frozen=True)
class PaperShadowDecision:
    run_id: str
    request_fingerprint: str
    economic_fingerprint: str
    mode: SimulationMode
    disposition: SimulationDisposition
    quantity: Decimal
    notional: Decimal
    reason: str
    decided_at: datetime

    def __post_init__(self) -> None:
        for field in ("run_id", "request_fingerprint", "economic_fingerprint"): _text(getattr(self, field), field)
        _reason(self.reason)
        if not isinstance(self.mode, SimulationMode) or self.mode is SimulationMode.DISABLED: raise PaperShadowContractError("decision requires PAPER or SHADOW")
        if not isinstance(self.disposition, SimulationDisposition) or self.disposition is SimulationDisposition.DISABLED: raise PaperShadowContractError("decision disposition must be typed")
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity")); object.__setattr__(self, "notional", _decimal(self.notional, "notional")); object.__setattr__(self, "decided_at", _utc(self.decided_at, "decided_at"))


def simulation_fingerprint(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Enum): return item.value
        if isinstance(item, Decimal): return format(item.normalize(), "f")
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "__dataclass_fields__"): return normalize(asdict(item))
        if isinstance(item, dict): return {str(k): normalize(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        if isinstance(item, (tuple, list)): return [normalize(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


__all__ = ["PAPER_SHADOW_CONTRACT_VERSION", "PaperShadowContractError", "PaperShadowDecision", "PaperShadowRunFacts", "SimulationDisposition", "SimulationMode", "simulation_fingerprint"]
