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

from app.domain.decimal_values import Price, Quantity
from app.domain.order_contracts import Actor, OrderAction, RiskEffect


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


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExecutionKind(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class PositionSide(str, Enum):
    NET = "NET"
    LONG = "LONG"
    SHORT = "SHORT"


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
class CanonicalEconomicIntent:
    """Typed facts that determine an order's economic effect.

    This deliberately contains no caller-owned JSON or dictionary payload.  A
    future durable command must snapshot this value, rather than infer it from
    entry-specific metadata.
    """

    side: OrderSide | None = None
    quantity: Quantity | None = None
    execution_kind: ExecutionKind | None = None
    limit_price: Price | None = None
    trigger_price: Price | None = None
    reduce_only: bool = False
    position_side: PositionSide = PositionSide.NET
    cancel_target_id: str | None = None
    target_position_id: str | None = None
    close_quantity: Quantity | None = None
    close_all: bool = False

    def __post_init__(self) -> None:
        if self.side is not None and not isinstance(self.side, OrderSide):
            raise EntryContractError("side must use OrderSide")
        if self.quantity is not None and not isinstance(self.quantity, Quantity):
            raise EntryContractError("quantity must use Quantity")
        if self.execution_kind is not None and not isinstance(self.execution_kind, ExecutionKind):
            raise EntryContractError("execution_kind must use ExecutionKind")
        if self.limit_price is not None and not isinstance(self.limit_price, Price):
            raise EntryContractError("limit_price must use Price")
        if self.trigger_price is not None and not isinstance(self.trigger_price, Price):
            raise EntryContractError("trigger_price must use Price")
        if not isinstance(self.reduce_only, bool) or not isinstance(self.close_all, bool):
            raise EntryContractError("reduce_only and close_all must be bool")
        if not isinstance(self.position_side, PositionSide):
            raise EntryContractError("position_side must use PositionSide")
        for name in ("cancel_target_id", "target_position_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))
        if self.close_quantity is not None and not isinstance(self.close_quantity, Quantity):
            raise EntryContractError("close_quantity must use Quantity")

    def validate_for_action(self, action: OrderAction) -> None:
        if not isinstance(action, OrderAction):
            raise EntryContractError("action must use OrderAction")
        if action is OrderAction.CANCEL:
            if self.cancel_target_id is None:
                raise EntryContractError("cancel requires cancel_target_id")
            if any(value is not None for value in (self.side, self.quantity, self.execution_kind, self.limit_price, self.trigger_price, self.target_position_id, self.close_quantity)) or self.reduce_only or self.close_all or self.position_side is not PositionSide.NET:
                raise EntryContractError("cancel intent cannot carry order or position facts")
            return

        if self.side is None or self.execution_kind is None:
            raise EntryContractError("order action requires side and execution_kind")
        if self.execution_kind in (ExecutionKind.LIMIT, ExecutionKind.STOP_LIMIT) and self.limit_price is None:
            raise EntryContractError("limit execution requires limit_price")
        if self.execution_kind in (ExecutionKind.STOP_MARKET, ExecutionKind.STOP_LIMIT) and self.trigger_price is None:
            raise EntryContractError("stop execution requires trigger_price")
        if self.execution_kind in (ExecutionKind.MARKET, ExecutionKind.STOP_MARKET) and self.limit_price is not None:
            raise EntryContractError("market execution cannot carry limit_price")
        if self.execution_kind in (ExecutionKind.MARKET, ExecutionKind.LIMIT) and self.trigger_price is not None:
            raise EntryContractError("non-stop execution cannot carry trigger_price")
        if action in (OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION):
            if not self.reduce_only or self.target_position_id is None:
                raise EntryContractError("risk-reducing action requires reduce_only target_position_id")
            if self.close_all == (self.close_quantity is not None):
                raise EntryContractError("risk-reducing action requires exactly one close scope")
            if self.quantity is not None or self.cancel_target_id is not None:
                raise EntryContractError("risk-reducing action cannot carry open or cancel facts")
            if self.close_quantity is not None and self.close_quantity.to_decimal() <= 0:
                raise EntryContractError("close_quantity must be greater than zero")
        elif self.quantity is None or self.quantity.to_decimal() <= 0:
            raise EntryContractError("risk-increasing action requires positive quantity")
        elif self.reduce_only or self.cancel_target_id is not None or self.target_position_id is not None or self.close_quantity is not None or self.close_all:
            raise EntryContractError("risk-increasing action cannot carry close-only facts")

    def canonical_facts(self) -> dict[str, str | bool | None]:
        return {
            "side": None if self.side is None else self.side.value,
            "quantity": None if self.quantity is None else self.quantity.to_string(),
            "execution_kind": None if self.execution_kind is None else self.execution_kind.value,
            "limit_price": None if self.limit_price is None else self.limit_price.to_string(),
            "trigger_price": None if self.trigger_price is None else self.trigger_price.to_string(),
            "reduce_only": self.reduce_only,
            "position_side": self.position_side.value,
            "cancel_target_id": self.cancel_target_id,
            "target_position_id": self.target_position_id,
            "close_quantity": None if self.close_quantity is None else self.close_quantity.to_string(),
            "close_all": self.close_all,
        }


@dataclass(frozen=True, slots=True)
class CanonicalEntryRequest:
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    action: OrderAction
    economic_intent: CanonicalEconomicIntent
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
        if not isinstance(self.action, OrderAction) or not isinstance(self.actor, EntryActorContext) or not isinstance(self.economic_intent, CanonicalEconomicIntent):
            raise EntryContractError("action, actor, and economic_intent must use canonical contracts")
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "correlation_id", _text(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "occurred_at", _zero_utc(self.occurred_at, "occurred_at"))
        self.economic_intent.validate_for_action(self.action)
        expected_effect = (
            RiskEffect.NEUTRAL if self.action is OrderAction.CANCEL
            else RiskEffect.INCREASE_RISK if self.action in (OrderAction.OPEN, OrderAction.INCREASE)
            else RiskEffect.REDUCE_RISK
        )
        effect = expected_effect if self.risk_effect is None else self.risk_effect
        if not isinstance(effect, RiskEffect) or effect is not expected_effect:
            raise EntryContractError(EntryRejection.AMBIGUOUS_RISK_EFFECT.value)
        object.__setattr__(self, "risk_effect", effect)
        mode = default_entry_mode(self.actor.entry_source) if self.mode is None else self.mode
        if not isinstance(mode, EntryMode):
            raise EntryContractError("mode must use EntryMode")
        if self.actor.entry_source in _RESTRICTED_SOURCES and mode not in (EntryMode.DISABLED, EntryMode.PAPER, EntryMode.SHADOW):
            raise EntryContractError(EntryRejection.UNSAFE_MODE.value)
        if self.action is OrderAction.PROTECTION:
            if self.actor.entry_source is not EntrySource.PROTECTION or self.actor.actor_type is not Actor.PROTECTION:
                raise EntryContractError(EntryRejection.PROTECTION_SEMANTICS.value)
        if self.actor.entry_source is EntrySource.PROTECTION:
            if self.action not in (OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION):
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
            "economic_intent": self.economic_intent.canonical_facts(),
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
            "economic_fingerprint": self.economic_fingerprint,
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
