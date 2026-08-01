"""Pure Protection-to-Canonical Entry V2 mapping contracts.

Protection evaluation is intentionally kept separate from this adapter.  The
caller must provide every fact needed to create a reducing canonical request;
this module never infers an order side, position, quantity, or trigger rule.
It performs no persistence, runtime dispatch, exchange access, or live-mode
selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.canonical_entry_contracts import (
    EntryActorContext,
    EntryMode,
    EntrySource,
    ExecutionKind,
    OrderSide,
    PositionSide,
)
from app.domain.canonical_entry_v2_contracts import (
    CanonicalEconomicIntentV2,
    CanonicalEntryRequestV2,
    CanonicalEntryV2Error,
    QuantitySemantics,
    TriggerDirection,
    TriggerPriceType,
)
from app.domain.decimal_values import Price, Quantity
from app.domain.order_contracts import Actor, OrderAction, RiskEffect


class ProtectionEntryContractError(CanonicalEntryV2Error):
    """Protection facts cannot be transformed into an authoritative request."""


@dataclass(frozen=True, slots=True)
class ProtectionEntryFacts:
    """Complete immutable facts required for one protection admission.

    ``side`` is the side of the reducing order, not an inferred value from a
    position.  ``close_quantity`` and ``close_all`` are mutually exclusive;
    quantity is deliberately absent so there is only one quantity truth.
    Stop trigger facts are mandatory for protection, including for a market
    execution representation, because a protection request is always the
    result of an explicit trigger decision.
    """

    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    actor_id: str
    side: OrderSide
    execution_kind: ExecutionKind
    position_side: PositionSide
    target_position_id: str
    close_quantity: Quantity | None
    close_all: bool
    trigger_price: Price
    trigger_direction: TriggerDirection
    trigger_price_type: TriggerPriceType
    idempotency_key: str
    correlation_id: str
    occurred_at: datetime
    limit_price: Price | None = None
    mode: EntryMode = EntryMode.PAPER

    def __post_init__(self) -> None:
        if isinstance(self.tenant_id, bool) or not isinstance(self.tenant_id, int) or self.tenant_id <= 0:
            raise ProtectionEntryContractError("tenant_id must be a positive integer")
        if isinstance(self.credential_id, bool) or not isinstance(self.credential_id, int) or self.credential_id <= 0:
            raise ProtectionEntryContractError("credential_id must be a positive integer")
        if not isinstance(self.account_scope, str) or not self.account_scope or not self.account_scope.isascii() or self.account_scope != self.account_scope.strip():
            raise ProtectionEntryContractError("account_scope must be canonical ASCII text")
        if not isinstance(self.instrument_id, str) or not self.instrument_id or not self.instrument_id.isascii() or self.instrument_id != self.instrument_id.strip():
            raise ProtectionEntryContractError("instrument_id must be canonical ASCII text")
        if not isinstance(self.market_type, str) or not self.market_type or not self.market_type.isascii() or self.market_type != self.market_type.strip():
            raise ProtectionEntryContractError("market_type must be canonical ASCII text")
        if not isinstance(self.actor_id, str) or not self.actor_id or not self.actor_id.isascii() or self.actor_id != self.actor_id.strip():
            raise ProtectionEntryContractError("actor_id must be canonical ASCII text")
        if not isinstance(self.side, OrderSide) or not isinstance(self.position_side, PositionSide):
            raise ProtectionEntryContractError("side and position_side must use typed enums")
        if self.execution_kind not in (ExecutionKind.STOP_MARKET, ExecutionKind.STOP_LIMIT):
            raise ProtectionEntryContractError("protection requires explicit stop execution")
        if not isinstance(self.target_position_id, str) or not self.target_position_id or not self.target_position_id.isascii() or self.target_position_id != self.target_position_id.strip():
            raise ProtectionEntryContractError("target_position_id must be canonical ASCII text")
        if not isinstance(self.close_all, bool):
            raise ProtectionEntryContractError("close_all must be bool")
        if self.close_quantity is not None and not isinstance(self.close_quantity, Quantity):
            raise ProtectionEntryContractError("close_quantity must use Quantity")
        if self.close_all == (self.close_quantity is not None):
            raise ProtectionEntryContractError("protection requires close_quantity XOR close_all")
        if not isinstance(self.trigger_price, Price):
            raise ProtectionEntryContractError("trigger_price must use Price")
        if not isinstance(self.trigger_direction, TriggerDirection) or not isinstance(self.trigger_price_type, TriggerPriceType):
            raise ProtectionEntryContractError("trigger facts must use typed enums")
        if self.execution_kind is ExecutionKind.STOP_MARKET and self.limit_price is not None:
            raise ProtectionEntryContractError("STOP_MARKET cannot carry limit_price")
        if self.execution_kind is ExecutionKind.STOP_LIMIT and not isinstance(self.limit_price, Price):
            raise ProtectionEntryContractError("STOP_LIMIT requires limit_price")
        if not isinstance(self.mode, EntryMode):
            raise ProtectionEntryContractError("mode must use EntryMode")
        for name in ("idempotency_key", "correlation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
                raise ProtectionEntryContractError(f"{name} must be canonical ASCII text")
        if not isinstance(self.occurred_at, datetime) or self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != timezone.utc.utcoffset(self.occurred_at):
            raise ProtectionEntryContractError("occurred_at must use a zero UTC offset")

    def to_request(self) -> CanonicalEntryRequestV2:
        """Build the typed reducing request without any side effects."""

        try:
            intent = CanonicalEconomicIntentV2(
                side=self.side,
                quantity=None,
                quantity_semantics=None,
                execution_kind=self.execution_kind,
                limit_price=self.limit_price,
                trigger_price=self.trigger_price,
                trigger_direction=self.trigger_direction,
                trigger_price_type=self.trigger_price_type,
                reduce_only=True,
                position_side=self.position_side,
                target_position_id=self.target_position_id,
                close_quantity=self.close_quantity,
                close_all=self.close_all,
            )
            return CanonicalEntryRequestV2(
                tenant_id=self.tenant_id,
                credential_id=self.credential_id,
                account_scope=self.account_scope,
                instrument_id=self.instrument_id,
                market_type=self.market_type,
                action=OrderAction.PROTECTION,
                economic_intent=intent,
                actor=EntryActorContext(Actor.PROTECTION, self.actor_id, EntrySource.PROTECTION),
                risk_effect=RiskEffect.REDUCE_RISK,
                idempotency_key=self.idempotency_key,
                correlation_id=self.correlation_id,
                occurred_at=self.occurred_at,
                mode=self.mode,
            )
        except (CanonicalEntryV2Error, TypeError, ValueError) as exc:
            raise ProtectionEntryContractError("protection facts are not a valid canonical request") from exc


def map_protection_to_canonical_entry(facts: ProtectionEntryFacts) -> CanonicalEntryRequestV2:
    """Map typed Protection facts to Canonical Entry V2; reject everything else."""

    if not isinstance(facts, ProtectionEntryFacts):
        raise ProtectionEntryContractError("protection facts must use ProtectionEntryFacts")
    return facts.to_request()


__all__ = ["ProtectionEntryContractError", "ProtectionEntryFacts", "map_protection_to_canonical_entry"]
