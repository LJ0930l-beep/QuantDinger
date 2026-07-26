"""Pure, fail-closed normalization for every future trading entry surface.

This module defines intent only.  It deliberately has no dependency on routes,
repositories, executors, workers, exchanges, or a live gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from app.domain.order_contracts import Actor, OrderAction, RiskEffect, classify_risk_effect


ENTRY_CONTRACT_VERSION = "canonical-entry-v1"


class EntryContractError(ValueError):
    """Raised when an entry cannot become a durable, safe command draft."""


class EntrySource(str, Enum):
    REST = "REST"
    MANUAL = "MANUAL"
    STRATEGY = "STRATEGY"
    AGENT = "AGENT"
    MCP = "MCP"
    GRID = "GRID"
    PROTECTION = "PROTECTION"


class EntryMode(str, Enum):
    DISABLED = "DISABLED"
    PAPER = "PAPER"
    SHADOW = "SHADOW"


class EntryDisposition(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class EntryRejection(str, Enum):
    MISSING_FACT = "MISSING_FACT"
    SOURCE_ACTOR_MISMATCH = "SOURCE_ACTOR_MISMATCH"
    UNSAFE_MODE = "UNSAFE_MODE"
    AMBIGUOUS_RISK_EFFECT = "AMBIGUOUS_RISK_EFFECT"
    PROTECTION_SEMANTICS = "PROTECTION_SEMANTICS"


_SOURCE_ACTORS = {
    EntrySource.REST: Actor.HUMAN,
    EntrySource.MANUAL: Actor.HUMAN,
    EntrySource.STRATEGY: Actor.STRATEGY,
    EntrySource.AGENT: Actor.AGENT,
    EntrySource.MCP: Actor.MCP,
    EntrySource.GRID: Actor.GRID,
    EntrySource.PROTECTION: Actor.PROTECTION,
}
_RESTRICTED_SOURCES = frozenset({EntrySource.AGENT, EntrySource.MCP, EntrySource.GRID})


def _text(value: object, field_name: str, *, uppercase: bool = False, lowercase: bool = False, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii() or len(value) > max_length:
        raise EntryContractError(f"{field_name} must be canonical ASCII text")
    if uppercase and value != value.upper():
        raise EntryContractError(f"{field_name} must be uppercase")
    if lowercase and value != value.lower():
        raise EntryContractError(f"{field_name} must be lowercase")
    return value


def _zero_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise EntryContractError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


def _fingerprint(material: dict[str, Any]) -> str:
    try:
        payload = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise EntryContractError("entry facts cannot be canonically encoded") from exc
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def default_entry_mode(source: EntrySource) -> EntryMode:
    if not isinstance(source, EntrySource):
        raise EntryContractError("entry source must use EntrySource")
    return EntryMode.DISABLED if source in _RESTRICTED_SOURCES else EntryMode.PAPER


@dataclass(frozen=True, slots=True)
class EntryActorContext:
    actor_type: Actor
    actor_id: str
    entry_source: EntrySource

    def __post_init__(self) -> None:
        if not isinstance(self.actor_type, Actor) or not isinstance(self.entry_source, EntrySource):
            raise EntryContractError("actor_type and entry_source must use canonical enums")
        object.__setattr__(self, "actor_id", _text(self.actor_id, "actor_id"))
        if _SOURCE_ACTORS[self.entry_source] is not self.actor_type:
            raise EntryContractError(EntryRejection.SOURCE_ACTOR_MISMATCH.value)


@dataclass(frozen=True, slots=True)
class CanonicalEntryRequest:
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    action: OrderAction
    actor: EntryActorContext
    idempotency_key: str
    correlation_id: str
    occurred_at: datetime
    risk_effect: RiskEffect | None = None
    mode: EntryMode | None = None
    economic_fingerprint: str = field(init=False)
    request_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.tenant_id, bool) or not isinstance(self.tenant_id, int) or self.tenant_id <= 0:
            raise EntryContractError("tenant_id must be positive")
        if isinstance(self.credential_id, bool) or not isinstance(self.credential_id, int) or self.credential_id <= 0:
            raise EntryContractError("credential_id must be positive")
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", uppercase=True, max_length=100))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lowercase=True, max_length=20))
        if not isinstance(self.action, OrderAction) or not isinstance(self.actor, EntryActorContext):
            raise EntryContractError("action and actor must use canonical contracts")
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "correlation_id", _text(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "occurred_at", _zero_utc(self.occurred_at, "occurred_at"))
        effect = self.risk_effect
        if effect is None:
            try:
                effect = classify_risk_effect(self.action)
            except ValueError as exc:
                raise EntryContractError(EntryRejection.AMBIGUOUS_RISK_EFFECT.value) from exc
        if not isinstance(effect, RiskEffect):
            raise EntryContractError("risk_effect must use RiskEffect")
        object.__setattr__(self, "risk_effect", effect)
        mode = default_entry_mode(self.actor.entry_source) if self.mode is None else self.mode
        if not isinstance(mode, EntryMode):
            raise EntryContractError("mode must use EntryMode")
        if self.actor.entry_source in _RESTRICTED_SOURCES and mode not in (EntryMode.DISABLED, EntryMode.PAPER, EntryMode.SHADOW):
            raise EntryContractError(EntryRejection.UNSAFE_MODE.value)
        if self.actor.entry_source is EntrySource.PROTECTION:
            if self.action not in (OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.CANCEL, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION):
                raise EntryContractError(EntryRejection.PROTECTION_SEMANTICS.value)
            if effect is not RiskEffect.REDUCE_RISK:
                raise EntryContractError(EntryRejection.PROTECTION_SEMANTICS.value)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "economic_fingerprint", _fingerprint(self.canonical_economic_facts()))
        object.__setattr__(self, "request_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_economic_facts(self) -> dict[str, Any]:
        """Facts that identify the requested economic effect, not its entry path."""

        return {
            "version": ENTRY_CONTRACT_VERSION,
            "tenant_id": self.tenant_id,
            "credential_id": self.credential_id,
            "account_scope": self.account_scope,
            "instrument_id": self.instrument_id,
            "market_type": self.market_type,
            "action": self.action.value,
            "risk_effect": self.risk_effect.value,
        }

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "version": ENTRY_CONTRACT_VERSION,
            "tenant_id": self.tenant_id, "credential_id": self.credential_id,
            "account_scope": self.account_scope, "instrument_id": self.instrument_id,
            "market_type": self.market_type, "action": self.action.value,
            "actor_type": self.actor.actor_type.value, "actor_id": self.actor.actor_id,
            "entry_source": self.actor.entry_source.value, "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id, "occurred_at": self.occurred_at.isoformat(),
            "risk_effect": self.risk_effect.value, "mode": self.mode.value,
        }


@dataclass(frozen=True, slots=True)
class CanonicalCommandDraft:
    """A pure draft that a future Command Gateway may validate and persist."""

    request: CanonicalEntryRequest
    disposition: EntryDisposition = EntryDisposition.ACCEPTED
    rejection: EntryRejection | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, CanonicalEntryRequest) or not isinstance(self.disposition, EntryDisposition):
            raise EntryContractError("command draft requires a canonical request and disposition")
        if self.disposition is EntryDisposition.ACCEPTED and self.rejection is not None:
            raise EntryContractError("accepted draft cannot carry a rejection")
        if self.disposition is EntryDisposition.REJECTED and not isinstance(self.rejection, EntryRejection):
            raise EntryContractError("rejected draft requires a typed rejection")


def normalize_entry(**facts: Any) -> CanonicalCommandDraft:
    """Single pure normalization boundary; it performs no persistence or I/O."""

    return CanonicalCommandDraft(CanonicalEntryRequest(**facts))
