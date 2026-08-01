"""Pure Strategy V2 candidate-to-admission contracts.

This module is the replacement boundary for the retired Strategy V2 legacy
queue.  It accepts only typed, decimal-safe strategy facts and produces a
``CanonicalEntryRequestV2``/``DurableEntryGraphV2``.  It does not persist,
open a connection, call a worker, invoke an executor, or contact an exchange.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import TypeAlias

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
    DurableEntryGraphV2,
    QuantitySemantics,
    TriggerDirection,
    TriggerPriceType,
)
from app.domain.decimal_values import Price, Quantity
from app.domain.entrypoint_v2_binding_contracts import (
    DurableEntryIdentityV2,
    bind_strategy_v2,
)
from app.domain.order_contracts import Actor, OrderAction, RiskEffect


STRATEGY_V2_CANDIDATE_CONTRACT_VERSION = "strategy-v2-candidate-v1"
DecimalInputValue: TypeAlias = Decimal | str | int


class StrategyV2CandidateError(ValueError):
    """A strategy candidate cannot be converted losslessly to Admission V2."""


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StrategyV2CandidateError(f"{name} must be a positive integer")
    return value


def _text(value: object, name: str, *, max_length: int = 160) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isascii()
        or len(value) > max_length
    ):
        raise StrategyV2CandidateError(f"{name} must be canonical ASCII text")
    return value


def _decimal(value: object, name: str, value_type: type[Quantity] | type[Price]):
    if value is None:
        return None
    try:
        return value if isinstance(value, value_type) else value_type(value)  # type: ignore[arg-type]
    except Exception as exc:
        raise StrategyV2CandidateError(f"{name} must use a valid Decimal value") from exc


def _risk_effect(action: OrderAction) -> RiskEffect:
    if action in (OrderAction.OPEN, OrderAction.INCREASE):
        return RiskEffect.INCREASE_RISK
    return RiskEffect.REDUCE_RISK


@dataclass(frozen=True, slots=True)
class StrategyV2CandidateTradePlan:
    """A typed candidate emitted by Strategy V2 before Admission.

    ``strategy_id``, ``strategy_run_id`` and ``signal_id`` are the immutable
    source identity used to derive the retry-stable idempotency key.  Scope,
    correlation and event time are supplied by the authoritative ingress
    boundary when the candidate is converted into a request.
    """

    strategy_id: int
    strategy_run_id: int
    signal_id: str
    instrument_id: str
    market_type: str
    action: OrderAction
    side: OrderSide | None
    quantity: DecimalInputValue | None
    execution_kind: ExecutionKind
    limit_price: DecimalInputValue | None = None
    trigger_price: DecimalInputValue | None = None
    trigger_direction: TriggerDirection | None = None
    trigger_price_type: TriggerPriceType | None = None
    reduce_only: bool = False
    position_side: PositionSide = PositionSide.NET
    target_position_id: str | None = None
    close_quantity: DecimalInputValue | None = None
    close_all: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _positive_int(self.strategy_id, "strategy_id"))
        object.__setattr__(self, "strategy_run_id", _positive_int(self.strategy_run_id, "strategy_run_id"))
        object.__setattr__(self, "signal_id", _text(self.signal_id, "signal_id"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id").upper())
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type").lower())
        if not isinstance(self.action, OrderAction) or self.action is OrderAction.CANCEL:
            raise StrategyV2CandidateError("Strategy V2 candidates require a non-CANCEL action")
        if not isinstance(self.execution_kind, ExecutionKind):
            raise StrategyV2CandidateError("execution_kind must use ExecutionKind")
        if self.side is not None and not isinstance(self.side, OrderSide):
            raise StrategyV2CandidateError("side must use OrderSide")
        if not isinstance(self.reduce_only, bool) or not isinstance(self.close_all, bool):
            raise StrategyV2CandidateError("reduce_only and close_all must be bool")
        if not isinstance(self.position_side, PositionSide):
            raise StrategyV2CandidateError("position_side must use PositionSide")
        for name, enum in (
            ("trigger_direction", TriggerDirection),
            ("trigger_price_type", TriggerPriceType),
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, enum):
                raise StrategyV2CandidateError(f"{name} must use a typed enum")
        if self.target_position_id is not None:
            object.__setattr__(self, "target_position_id", _text(self.target_position_id, "target_position_id"))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", Quantity))
        object.__setattr__(self, "close_quantity", _decimal(self.close_quantity, "close_quantity", Quantity))
        object.__setattr__(self, "limit_price", _decimal(self.limit_price, "limit_price", Price))
        object.__setattr__(self, "trigger_price", _decimal(self.trigger_price, "trigger_price", Price))
        try:
            CanonicalEconomicIntentV2(
                side=self.side,
                quantity=self.quantity,
                quantity_semantics=QuantitySemantics.ABSOLUTE if self.quantity is not None else None,
                execution_kind=self.execution_kind,
                limit_price=self.limit_price,
                trigger_price=self.trigger_price,
                trigger_direction=self.trigger_direction,
                trigger_price_type=self.trigger_price_type,
                reduce_only=self.reduce_only,
                position_side=self.position_side,
                target_position_id=self.target_position_id,
                close_quantity=self.close_quantity,
                close_all=self.close_all,
            ).validate(self.action)
        except CanonicalEntryV2Error as exc:
            raise StrategyV2CandidateError("candidate facts are invalid for action") from exc

    def idempotency_key(self) -> str:
        """Return a deterministic retry key without time, randomness or floats."""

        material = {
            "version": STRATEGY_V2_CANDIDATE_CONTRACT_VERSION,
            "strategy_id": self.strategy_id,
            "strategy_run_id": self.strategy_run_id,
            "signal_id": self.signal_id,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "strategy-v2-" + hashlib.sha256(encoded.encode("ascii")).hexdigest()[:48]

    def to_request(
        self,
        *,
        tenant_id: int,
        credential_id: int,
        account_scope: str,
        correlation_id: str,
        occurred_at: datetime,
        mode: EntryMode | None = None,
    ) -> CanonicalEntryRequestV2:
        """Build a canonical request; this method performs no persistence."""

        try:
            return CanonicalEntryRequestV2(
                tenant_id=tenant_id,
                credential_id=credential_id,
                account_scope=account_scope,
                instrument_id=self.instrument_id,
                market_type=self.market_type,
                action=self.action,
                economic_intent=CanonicalEconomicIntentV2(
                    side=self.side,
                    quantity=self.quantity,
                    quantity_semantics=QuantitySemantics.ABSOLUTE if self.quantity is not None else None,
                    execution_kind=self.execution_kind,
                    limit_price=self.limit_price,
                    trigger_price=self.trigger_price,
                    trigger_direction=self.trigger_direction,
                    trigger_price_type=self.trigger_price_type,
                    reduce_only=self.reduce_only,
                    position_side=self.position_side,
                    target_position_id=self.target_position_id,
                    close_quantity=self.close_quantity,
                    close_all=self.close_all,
                ),
                actor=EntryActorContext(Actor.STRATEGY, f"strategy-{self.strategy_id}", EntrySource.STRATEGY),
                risk_effect=_risk_effect(self.action),
                idempotency_key=self.idempotency_key(),
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                mode=mode,
            )
        except (CanonicalEntryV2Error, TypeError, ValueError) as exc:
            raise StrategyV2CandidateError("candidate cannot become a canonical entry request") from exc

    def to_graph(
        self,
        *,
        command_id: str,
        economic_order_id: str,
        tenant_id: int,
        credential_id: int,
        account_scope: str,
        correlation_id: str,
        occurred_at: datetime,
        mode: EntryMode | None = None,
    ) -> DurableEntryGraphV2:
        """Bind the candidate to a durable graph for a future Admission call."""

        try:
            request = self.to_request(
                tenant_id=tenant_id,
                credential_id=credential_id,
                account_scope=account_scope,
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                mode=mode,
            )
            return bind_strategy_v2(request, DurableEntryIdentityV2(command_id, economic_order_id))
        except (CanonicalEntryV2Error, TypeError, ValueError) as exc:
            raise StrategyV2CandidateError("candidate cannot become a durable entry graph") from exc


__all__ = [
    "STRATEGY_V2_CANDIDATE_CONTRACT_VERSION",
    "StrategyV2CandidateError",
    "StrategyV2CandidateTradePlan",
]
