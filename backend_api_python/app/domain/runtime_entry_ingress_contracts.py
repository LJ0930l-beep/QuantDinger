"""Versioned, fail-closed Runtime Entry ingress facts and stable identities.

This is a pure contract.  It performs neither authentication nor I/O and never
creates a connection, client order ID, exchange client, executor, or order.
The caller supplies an authenticated principal, resolved durable scope, and a
server acceptance time; retries reuse the persisted audit facts in a later
repository boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid5

from app.domain.canonical_entry_contracts import (
    EntryActorContext, EntryMode, EntrySource, ExecutionKind, OrderSide, PositionSide,
)
from app.domain.canonical_entry_v2_contracts import (
    CancelTargetKind, CanonicalEconomicIntentV2, CanonicalEntryRequestV2,
    CanonicalEntryV2Error, QuantitySemantics, TriggerDirection, TriggerPriceType,
)
from app.domain.decimal_values import Price, Quantity
from app.domain.entrypoint_v2_binding_contracts import DurableEntryIdentityV2
from app.domain.order_contracts import Actor, OrderAction, RiskEffect


RUNTIME_ENTRY_INGRESS_CONTRACT_VERSION = "runtime-entry-ingress-v1"
_INGRESS_NAMESPACE = UUID("8a6c0d7a-4a2b-5535-8e63-6fe7c4124ff2")


class RuntimeEntryIngressError(CanonicalEntryV2Error):
    """Runtime input cannot be transformed into authoritative V2 facts."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise RuntimeEntryIngressError(f"{field_name} must be canonical ASCII text")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeEntryIngressError(f"{field_name} must be a positive integer")
    return value


def _decimal_text(value: object, field_name: str, cls: type[Quantity] | type[Price]):
    if value is None:
        return None
    if isinstance(value, float) or not isinstance(value, str):
        raise RuntimeEntryIngressError(f"{field_name} must use a Decimal string")
    try:
        return cls(value)
    except Exception as exc:
        raise RuntimeEntryIngressError(f"{field_name} must be a valid Decimal string") from exc


@dataclass(frozen=True, slots=True)
class RuntimeIngressPrincipal:
    """Authenticated server-side identity; request bodies cannot choose it."""

    tenant_id: int
    actor_id: str
    source: EntrySource

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _positive_int(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "actor_id", _text(self.actor_id, "actor_id"))
        if not isinstance(self.source, EntrySource):
            raise RuntimeEntryIngressError("source must use EntrySource")

    @property
    def actor(self) -> EntryActorContext:
        actor_by_source = {
            EntrySource.REST: Actor.HUMAN, EntrySource.MANUAL: Actor.HUMAN,
            EntrySource.STRATEGY: Actor.STRATEGY, EntrySource.PROTECTION: Actor.PROTECTION,
            EntrySource.AGENT: Actor.AGENT, EntrySource.MCP: Actor.MCP, EntrySource.GRID: Actor.GRID,
        }
        return EntryActorContext(actor_by_source[self.source], self.actor_id, self.source)


@dataclass(frozen=True, slots=True)
class AuthoritativeIngressScope:
    """Server-resolved credential/account ownership, never client supplied."""

    tenant_id: int
    credential_id: int
    account_scope: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _positive_int(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "credential_id", _positive_int(self.credential_id, "credential_id"))
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))


@dataclass(frozen=True, slots=True)
class RuntimeEntryIngressV1:
    """Explicit request facts.  Amount/notional/margin conversions are absent."""

    credential_id: int
    instrument_id: str
    market_type: str
    action: OrderAction
    side: OrderSide | None = None
    quantity: str | None = None
    quantity_semantics: QuantitySemantics | None = None
    execution_kind: ExecutionKind | None = None
    limit_price: str | None = None
    trigger_price: str | None = None
    trigger_direction: TriggerDirection | None = None
    trigger_price_type: TriggerPriceType | None = None
    reduce_only: bool = False
    position_side: PositionSide = PositionSide.NET
    cancel_target_kind: CancelTargetKind | None = None
    cancel_target_id: str | None = None
    target_position_id: str | None = None
    close_quantity: str | None = None
    close_all: bool = False
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "credential_id", _positive_int(self.credential_id, "credential_id"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id").upper())
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type").lower())
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, "idempotency_key"))
        if not isinstance(self.action, OrderAction) or not isinstance(self.reduce_only, bool) or not isinstance(self.close_all, bool):
            raise RuntimeEntryIngressError("action and booleans must use typed ingress facts")
        for name, enum in (("side", OrderSide), ("quantity_semantics", QuantitySemantics),
                           ("execution_kind", ExecutionKind), ("trigger_direction", TriggerDirection),
                           ("trigger_price_type", TriggerPriceType), ("position_side", PositionSide),
                           ("cancel_target_kind", CancelTargetKind)):
            value = getattr(self, name)
            if value is not None and not isinstance(value, enum):
                raise RuntimeEntryIngressError(f"{name} must use a typed enum")
        for name in ("cancel_target_id", "target_position_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))
        # These conversions reject floats and preserve the project Decimal contract.
        quantity = _decimal_text(self.quantity, "quantity", Quantity)
        close_quantity = _decimal_text(self.close_quantity, "close_quantity", Quantity)
        limit_price = _decimal_text(self.limit_price, "limit_price", Price)
        trigger_price = _decimal_text(self.trigger_price, "trigger_price", Price)
        intent = CanonicalEconomicIntentV2(
            side=self.side, quantity=quantity, quantity_semantics=self.quantity_semantics,
            execution_kind=self.execution_kind, limit_price=limit_price, trigger_price=trigger_price,
            trigger_direction=self.trigger_direction, trigger_price_type=self.trigger_price_type,
            reduce_only=self.reduce_only, position_side=self.position_side,
            cancel_target_kind=self.cancel_target_kind, cancel_target_id=self.cancel_target_id,
            target_position_id=self.target_position_id, close_quantity=close_quantity,
            close_all=self.close_all,
        )
        try:
            intent.validate(self.action)
        except CanonicalEntryV2Error as exc:
            raise RuntimeEntryIngressError("explicit ingress facts conflict with action") from exc

    def economic_intent(self) -> CanonicalEconomicIntentV2:
        return CanonicalEconomicIntentV2(
            side=self.side, quantity=_decimal_text(self.quantity, "quantity", Quantity),
            quantity_semantics=self.quantity_semantics, execution_kind=self.execution_kind,
            limit_price=_decimal_text(self.limit_price, "limit_price", Price),
            trigger_price=_decimal_text(self.trigger_price, "trigger_price", Price),
            trigger_direction=self.trigger_direction, trigger_price_type=self.trigger_price_type,
            reduce_only=self.reduce_only, position_side=self.position_side,
            cancel_target_kind=self.cancel_target_kind, cancel_target_id=self.cancel_target_id,
            target_position_id=self.target_position_id,
            close_quantity=_decimal_text(self.close_quantity, "close_quantity", Quantity), close_all=self.close_all,
        )


def _risk_effect(action: OrderAction) -> RiskEffect:
    return RiskEffect.NEUTRAL if action is OrderAction.CANCEL else (
        RiskEffect.INCREASE_RISK if action in (OrderAction.OPEN, OrderAction.INCREASE) else RiskEffect.REDUCE_RISK
    )


def _stable_uuid(label: str) -> str:
    return str(uuid5(_INGRESS_NAMESPACE, label)).lower()


def build_runtime_entry_request(
    ingress: RuntimeEntryIngressV1,
    *, principal: RuntimeIngressPrincipal, scope: AuthoritativeIngressScope,
    correlation_id: str, occurred_at: datetime, mode: EntryMode | None = None,
) -> CanonicalEntryRequestV2:
    if not isinstance(ingress, RuntimeEntryIngressV1) or not isinstance(principal, RuntimeIngressPrincipal) or not isinstance(scope, AuthoritativeIngressScope):
        raise RuntimeEntryIngressError("ingress, principal, and scope must use typed contracts")
    if principal.tenant_id != scope.tenant_id or ingress.credential_id != scope.credential_id:
        raise RuntimeEntryIngressError("authoritative scope does not match ingress identity")
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None or occurred_at.utcoffset() != timezone.utc.utcoffset(occurred_at):
        raise RuntimeEntryIngressError("occurred_at must be server UTC")
    return CanonicalEntryRequestV2(
        tenant_id=scope.tenant_id, credential_id=scope.credential_id, account_scope=scope.account_scope,
        instrument_id=ingress.instrument_id, market_type=ingress.market_type, action=ingress.action,
        economic_intent=ingress.economic_intent(), actor=principal.actor, risk_effect=_risk_effect(ingress.action),
        idempotency_key=ingress.idempotency_key, correlation_id=_text(correlation_id, "correlation_id"),
        occurred_at=occurred_at.astimezone(timezone.utc), mode=mode,
    )


def derive_durable_entry_identity(
    ingress: RuntimeEntryIngressV1, *, principal: RuntimeIngressPrincipal, scope: AuthoritativeIngressScope,
) -> DurableEntryIdentityV2:
    if not isinstance(ingress, RuntimeEntryIngressV1) or not isinstance(principal, RuntimeIngressPrincipal) or not isinstance(scope, AuthoritativeIngressScope):
        raise RuntimeEntryIngressError("identity inputs must use typed contracts")
    if principal.tenant_id != scope.tenant_id or ingress.credential_id != scope.credential_id:
        raise RuntimeEntryIngressError("identity scope does not match ingress")
    material = "|".join((RUNTIME_ENTRY_INGRESS_CONTRACT_VERSION, str(scope.tenant_id), str(scope.credential_id), scope.account_scope, principal.source.value, ingress.idempotency_key))
    command_id = _stable_uuid("command|" + material)
    economic_order_id = None if ingress.action is OrderAction.CANCEL else _stable_uuid("economic-order-v1|" + command_id)
    return DurableEntryIdentityV2(command_id=command_id, economic_order_id=economic_order_id)


def derive_default_correlation_id(ingress: RuntimeEntryIngressV1, *, principal: RuntimeIngressPrincipal, scope: AuthoritativeIngressScope) -> str:
    identity = derive_durable_entry_identity(ingress, principal=principal, scope=scope)
    return "ingress-" + _stable_uuid("correlation-v1|" + identity.command_id)
