"""Immutable facts for durable hard-risk enforcement.

This is a pure domain boundary.  It turns the existing PR-10 policy evaluation
into versioned policy/input/decision/reservation facts that match the Wave 2
expand-only schema.  It does not call a gateway, worker, exchange, or runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import UUID

from app.domain.decimal_values import canonical_decimal_string
from app.domain.hard_risk_contracts import (
    HardRiskDecision,
    HardRiskRequest,
    RiskExposureSnapshot,
    RiskLimitPolicy,
    RiskReservationDemand,
)
from app.domain.order_contracts import Actor, OrderAction


RISK_ENFORCEMENT_CONTRACT_VERSION = "hard-risk-enforcement-v1"


class RiskEnforcementContractError(ValueError):
    """Raised when immutable enforcement facts are incomplete or mixed-scope."""


def _uuid(value: UUID | str, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(value)).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise RiskEnforcementContractError(f"{field_name} must be a UUID") from exc


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RiskEnforcementContractError(f"{field_name} must be a positive integer")
    return value


def _text(value: object, field_name: str, *, uppercase: bool = False, lowercase: bool = False, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii() or len(value) > max_length:
        raise RiskEnforcementContractError(f"{field_name} must be canonical ASCII text")
    if uppercase and value != value.upper():
        raise RiskEnforcementContractError(f"{field_name} must be uppercase")
    if lowercase and value != value.lower():
        raise RiskEnforcementContractError(f"{field_name} must be lowercase")
    return value


def _decimal(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise RiskEnforcementContractError("risk facts must use Decimal")
    return canonical_decimal_string(value)


def _hash(material: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise RiskEnforcementContractError("risk fact cannot be canonically encoded") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RiskEnforcementScope:
    """All facts which must agree before a decision can reserve capacity."""

    command_id: UUID | str
    economic_order_id: UUID | str
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    action: OrderAction
    actor: Actor

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, "command_id"))
        object.__setattr__(self, "economic_order_id", _uuid(self.economic_order_id, "economic_order_id"))
        object.__setattr__(self, "tenant_id", _positive_int(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "credential_id", _positive_int(self.credential_id, "credential_id"))
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", uppercase=True, max_length=100))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lowercase=True, max_length=20))
        if not isinstance(self.action, OrderAction) or not isinstance(self.actor, Actor):
            raise RiskEnforcementContractError("action and actor must use PR-00 enums")

    def canonical(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id, "economic_order_id": self.economic_order_id,
            "tenant_id": self.tenant_id, "credential_id": self.credential_id,
            "account_scope": self.account_scope, "instrument_id": self.instrument_id,
            "market_type": self.market_type, "action": self.action.value,
            "actor": self.actor.value,
        }


@dataclass(frozen=True, slots=True)
class RiskPolicySnapshotFact:
    snapshot_id: UUID | str
    scope: RiskEnforcementScope
    policy: RiskLimitPolicy
    policy_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _uuid(self.snapshot_id, "snapshot_id"))
        if not isinstance(self.scope, RiskEnforcementScope) or not isinstance(self.policy, RiskLimitPolicy):
            raise RiskEnforcementContractError("policy snapshot requires canonical scope and policy")
        object.__setattr__(self, "policy_hash", _hash({
            "version": RISK_ENFORCEMENT_CONTRACT_VERSION,
            "scope": self.scope.canonical(),
            "policy_version": self.policy.policy_version,
            "valuation_currency": self.policy.valuation_currency,
            "limits": {
                "max_gross_notional": _decimal(self.policy.max_gross_notional.value),
                "max_net_notional": _decimal(self.policy.max_net_notional.value),
                "max_instrument_notional": _decimal(self.policy.max_instrument_notional.value),
                "max_leverage": _decimal(self.policy.max_leverage),
                "minimum_available_margin": _decimal(self.policy.minimum_available_margin.value),
                "max_daily_loss": _decimal(self.policy.max_daily_loss.value),
                "max_drawdown_ratio": _decimal(self.policy.max_drawdown_ratio),
            },
        }))


@dataclass(frozen=True, slots=True)
class RiskInputSnapshotFact:
    snapshot_id: UUID | str
    scope: RiskEnforcementScope
    input_version: str
    exposure: RiskExposureSnapshot
    input_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _uuid(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "input_version", _text(self.input_version, "input_version", max_length=96))
        if not isinstance(self.scope, RiskEnforcementScope) or not isinstance(self.exposure, RiskExposureSnapshot):
            raise RiskEnforcementContractError("input snapshot requires canonical scope and exposure")
        if (self.scope.account_scope, self.scope.instrument_id) != (self.exposure.account_scope, self.exposure.instrument_id):
            raise RiskEnforcementContractError("input exposure scope must match enforcement scope")
        object.__setattr__(self, "input_hash", _hash({
            "version": RISK_ENFORCEMENT_CONTRACT_VERSION,
            "scope": self.scope.canonical(), "input_version": self.input_version,
            "valuation_currency": self.exposure.valuation_currency,
            "reconciliation_health": self.exposure.reconciliation_health.value,
            "market_data_health": self.exposure.market_data_health.value,
            "account_facts_verified": self.exposure.account_facts_verified,
            "facts": {name: _decimal(getattr(self.exposure, name)) for name in (
                "gross_notional", "net_notional", "instrument_notional", "available_margin",
                "equity", "peak_equity", "daily_realized_pnl",
            )},
        }))


@dataclass(frozen=True, slots=True)
class RiskDecisionFact:
    decision_id: UUID | str
    scope: RiskEnforcementScope
    policy_snapshot: RiskPolicySnapshotFact
    input_snapshot: RiskInputSnapshotFact
    decision: HardRiskDecision
    decision_status: str = field(init=False)
    decision_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _uuid(self.decision_id, "decision_id"))
        if not isinstance(self.scope, RiskEnforcementScope):
            raise RiskEnforcementContractError("decision requires canonical scope")
        if not isinstance(self.policy_snapshot, RiskPolicySnapshotFact) or not isinstance(self.input_snapshot, RiskInputSnapshotFact):
            raise RiskEnforcementContractError("decision requires policy and input snapshots")
        if self.policy_snapshot.scope != self.scope or self.input_snapshot.scope != self.scope:
            raise RiskEnforcementContractError("decision snapshots must exactly match decision scope")
        if not isinstance(self.decision, HardRiskDecision):
            raise RiskEnforcementContractError("decision must use HardRiskDecision")
        if (self.decision.account_scope, self.decision.instrument_id, self.decision.action) != (
            self.scope.account_scope, self.scope.instrument_id, self.scope.action,
        ):
            raise RiskEnforcementContractError("hard-risk decision scope must match durable scope")
        if self.decision.policy_version != self.policy_snapshot.policy.policy_version:
            raise RiskEnforcementContractError("decision policy version must match policy snapshot")
        status = "ALLOW" if self.decision.allowed else "DENY"
        object.__setattr__(self, "decision_status", status)
        object.__setattr__(self, "decision_fingerprint", _hash({
            "version": RISK_ENFORCEMENT_CONTRACT_VERSION,
            "scope": self.scope.canonical(), "policy_snapshot_id": self.policy_snapshot.snapshot_id,
            "policy_hash": self.policy_snapshot.policy_hash, "input_snapshot_id": self.input_snapshot.snapshot_id,
            "input_hash": self.input_snapshot.input_hash, "hard_risk_fingerprint": self.decision.canonical_fingerprint,
            "decision": status,
        }))


@dataclass(frozen=True, slots=True)
class RiskReservationFact:
    reservation_id: UUID | str
    decision: RiskDecisionFact
    demand: RiskReservationDemand
    reservation_kind: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reservation_id", _uuid(self.reservation_id, "reservation_id"))
        object.__setattr__(self, "reservation_kind", _text(self.reservation_kind, "reservation_kind", max_length=32))
        if not isinstance(self.decision, RiskDecisionFact) or not isinstance(self.demand, RiskReservationDemand):
            raise RiskEnforcementContractError("reservation requires decision and demand facts")
        if self.reservation_id != self.demand.reservation_id:
            raise RiskEnforcementContractError("reservation id must match immutable demand identity")
        if not self.decision.decision.allowed:
            raise RiskEnforcementContractError("denied decision cannot reserve risk capacity")
        scope = self.decision.scope
        if (self.demand.account_scope, self.demand.instrument_id, self.demand.valuation_currency) != (
            scope.account_scope, scope.instrument_id, self.decision.policy_snapshot.policy.valuation_currency,
        ):
            raise RiskEnforcementContractError("reservation demand must exactly match decision scope and currency")
        if self.expires_at is not None:
            if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None or self.expires_at.utcoffset() != timezone.utc.utcoffset(self.expires_at):
                raise RiskEnforcementContractError("expires_at must use a zero UTC offset")
            object.__setattr__(self, "expires_at", self.expires_at.astimezone(timezone.utc))


def build_risk_reservation_fact(
    *,
    reservation_id: UUID | str,
    decision: RiskDecisionFact,
    request: HardRiskRequest,
    reservation_kind: str,
    expires_at: datetime | None = None,
) -> RiskReservationFact | None:
    """Build a capacity fact only for an allowed risk-increasing decision."""

    if not isinstance(request, HardRiskRequest):
        raise RiskEnforcementContractError("request must use HardRiskRequest")
    if request.action is not decision.scope.action or request.action is not decision.decision.action:
        raise RiskEnforcementContractError("reservation request action must match decision")
    if not decision.decision.allowed or request.risk_effect.value != "INCREASE_RISK":
        return None
    scope = decision.scope
    demand = RiskReservationDemand(
        reservation_id=str(reservation_id), account_scope=scope.account_scope,
        instrument_id=scope.instrument_id,
        valuation_currency=decision.policy_snapshot.policy.valuation_currency,
        gross_notional=request.gross_notional, net_notional=request.net_notional,
        instrument_notional=request.instrument_notional, margin=request.margin,
    )
    return RiskReservationFact(reservation_id, decision, demand, reservation_kind, expires_at)
