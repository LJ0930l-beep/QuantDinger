"""Pure authority checks for Runtime Entry ingress.

These contracts consume only server-resolved, persisted facts.  They never
infer market type, account scope, instrument rules, or position identity from
a request body or an exchange response.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.canonical_entry_contracts import PositionSide
from app.domain.order_contracts import OrderAction
from app.domain.runtime_entry_ingress_contracts import (
    AuthoritativeIngressScope, RuntimeEntryIngressV1, RuntimeIngressPrincipal,
)


class RuntimeEntryResolutionError(ValueError):
    """A runtime ingress does not match persisted authority facts."""


def _uuid(value: UUID | str, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeEntryResolutionError(f"{field} must be a UUID") from exc


@dataclass(frozen=True, slots=True)
class CredentialOwnership:
    tenant_id: int
    credential_id: int
    account_scope: str
    exchange_id: str


@dataclass(frozen=True, slots=True)
class InstrumentAuthority:
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    instrument_rule_snapshot_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_rule_snapshot_id", _uuid(self.instrument_rule_snapshot_id, "instrument_rule_snapshot_id"))


@dataclass(frozen=True, slots=True)
class PositionSubjectAuthority:
    position_id: str
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    position_side: PositionSide

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_id", _uuid(self.position_id, "position_id"))
        if not isinstance(self.position_side, PositionSide) or self.position_side is PositionSide.NET:
            raise RuntimeEntryResolutionError("position authority requires LONG or SHORT side")


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeEntryFacts:
    scope: AuthoritativeIngressScope
    credential: CredentialOwnership
    instrument: InstrumentAuthority
    position: PositionSubjectAuthority | None


def resolve_runtime_entry_facts(
    ingress: RuntimeEntryIngressV1,
    principal: RuntimeIngressPrincipal,
    credential: CredentialOwnership,
    instrument: InstrumentAuthority,
    position: PositionSubjectAuthority | None = None,
) -> ResolvedRuntimeEntryFacts:
    if not isinstance(ingress, RuntimeEntryIngressV1) or not isinstance(principal, RuntimeIngressPrincipal):
        raise RuntimeEntryResolutionError("runtime ingress and principal must be typed")
    if not isinstance(credential, CredentialOwnership) or not isinstance(instrument, InstrumentAuthority):
        raise RuntimeEntryResolutionError("credential and instrument authority must be typed")
    if credential.tenant_id != principal.tenant_id or credential.credential_id != ingress.credential_id:
        raise RuntimeEntryResolutionError("credential does not belong to authenticated principal")
    scope = AuthoritativeIngressScope(credential.tenant_id, credential.credential_id, credential.account_scope)
    expected = (scope.tenant_id, scope.credential_id, scope.account_scope, ingress.instrument_id, ingress.market_type)
    actual = (instrument.tenant_id, instrument.credential_id, instrument.account_scope, instrument.instrument_id, instrument.market_type)
    if actual != expected:
        raise RuntimeEntryResolutionError("instrument authority scope does not match ingress")
    needs_position = ingress.action in {OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION}
    if needs_position:
        if position is None or ingress.target_position_id != position.position_id:
            raise RuntimeEntryResolutionError("reducing ingress requires its persisted position authority")
        if (position.tenant_id, position.credential_id, position.account_scope, position.instrument_id, position.market_type, position.position_side) != (*expected, ingress.position_side):
            raise RuntimeEntryResolutionError("position authority scope does not match ingress")
    elif position is not None or ingress.target_position_id is not None:
        raise RuntimeEntryResolutionError("non-reducing ingress cannot carry a position authority")
    return ResolvedRuntimeEntryFacts(scope, credential, instrument, position)
