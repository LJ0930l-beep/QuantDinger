"""Pure, fail-closed contracts for durable order commands and reservations.

This PR deliberately stops at the durable command boundary.  It neither
calculates risk nor changes an order state, submits an order, or reads runtime
configuration.  IDs are caller supplied so retries can be made deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from app.domain.decimal_values import Price, Quantity, QuoteAmount
from app.domain.order_contracts import Actor, EconomicOrderState, OrderAction


COMMAND_FINGERPRINT_VERSION = "command-fingerprint-v1"
INTENT_PAYLOAD_HASH_VERSION = "intent-payload-v1"
CANONICAL_JSON_VERSION = "canonical-json-v1"


class CommandIntentContractError(ValueError):
    """Base error for invalid durable-command facts."""


class IdempotencyConflict(CommandIntentContractError):
    """The idempotency key already names a different command."""


class ReservationConflict(CommandIntentContractError):
    """A reservation identity is reused with different immutable facts."""


class ReservationStateConflict(CommandIntentContractError):
    """A reservation transition cannot be safely applied."""


class InstrumentRuleSnapshotMismatch(CommandIntentContractError):
    """The persisted rule snapshot does not describe the requested instrument."""


class CommandStatus(str, Enum):
    ACCEPTED = "ACCEPTED"


class CommandGraphDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


class ReservationState(str, Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ReservationTransitionDisposition(str, Enum):
    APPLIED = "APPLIED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,159}$")
_SENSITIVE_KEY_PARTS = ("secret", "api_key", "apikey", "access_key", "private_key", "passphrase", "token")


def _required_text(value: object, field_name: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str):
        raise CommandIntentContractError(f"{field_name} must be text")
    result = value.strip()
    if not result or len(result) > max_length or not _TOKEN.fullmatch(result):
        raise CommandIntentContractError(f"{field_name} is not canonical")
    return result


def canonical_uuid(value: UUID | str, field_name: str) -> str:
    try:
        result = str(value if isinstance(value, UUID) else UUID(value)).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise CommandIntentContractError(f"{field_name} must be a UUID") from exc
    if not _UUID.fullmatch(result):
        raise CommandIntentContractError(f"{field_name} must be a canonical UUID")
    return result


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CommandIntentContractError(f"{field_name} must be a positive integer")
    return value


def _canonical_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value.lower()):
        raise CommandIntentContractError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value.lower()


def _canonical_scope(value: object, field_name: str) -> str:
    result = _required_text(value, field_name)
    if value != result:
        raise CommandIntentContractError(f"{field_name} must not contain surrounding whitespace")
    return result


def _canonical_instrument(value: object) -> str:
    return _required_text(value, "instrument_id", max_length=100).upper()


def _canonical_market_type(value: object) -> str:
    return _required_text(value, "market_type", max_length=20).lower()


def _canonical_currency(value: object) -> str:
    return _required_text(value, "currency", max_length=20).upper()


def _canonical_json_value(value: Any, *, path: str = "payload") -> Any:
    if isinstance(value, float):
        raise CommandIntentContractError(f"{path} cannot contain binary float")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        raise CommandIntentContractError(f"{path} cannot contain Decimal; use a canonical string")
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item, path=f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        canonical: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or key.strip() != key:
                raise CommandIntentContractError(f"{path} has a non-canonical key")
            normalized_key = key.lower().replace("-", "_")
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                raise CommandIntentContractError(f"{path} contains a prohibited sensitive field")
            canonical[key] = _canonical_json_value(item, path=f"{path}.{key}")
        return {key: canonical[key] for key in sorted(canonical)}
    raise CommandIntentContractError(f"{path} contains unsupported value type")


def canonical_json(value: Mapping[str, Any]) -> str:
    """Return the versioned, secret-free JSON representation used for hashes."""

    canonical = _canonical_json_value(value)
    if not isinstance(canonical, dict):
        raise CommandIntentContractError("payload must be a mapping")
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_material(version: str, material: Mapping[str, Any]) -> str:
    encoded = canonical_json({"version": version, "material": material}).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CommandIntentContractError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise CommandIntentContractError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class OrderCommand:
    command_id: UUID | str
    tenant_id: int
    user_id: int
    credential_id: int
    actor_type: Actor
    actor_id: str
    source: str
    action: OrderAction
    account_scope: str
    request_payload: Mapping[str, Any]
    idempotency_key: str
    correlation_id: str = ""
    strategy_id: int | None = None
    request_fingerprint: str = field(init=False)
    canonical_request_json: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", canonical_uuid(self.command_id, "command_id"))
        object.__setattr__(self, "tenant_id", _positive_integer(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "user_id", _positive_integer(self.user_id, "user_id"))
        object.__setattr__(self, "credential_id", _positive_integer(self.credential_id, "credential_id"))
        if not isinstance(self.actor_type, Actor) or not isinstance(self.action, OrderAction):
            raise CommandIntentContractError("actor_type and action must use PR-00 enums")
        object.__setattr__(self, "actor_id", _required_text(self.actor_id, "actor_id"))
        object.__setattr__(self, "source", _required_text(self.source, "source", max_length=32).lower())
        object.__setattr__(self, "account_scope", _canonical_scope(self.account_scope, "account_scope"))
        object.__setattr__(self, "idempotency_key", _required_text(self.idempotency_key, "idempotency_key", max_length=180))
        correlation_id = "" if self.correlation_id == "" else _required_text(self.correlation_id, "correlation_id")
        object.__setattr__(self, "correlation_id", correlation_id)
        if self.strategy_id is not None:
            object.__setattr__(self, "strategy_id", _positive_integer(self.strategy_id, "strategy_id"))
        json_text = canonical_json(self.request_payload)
        object.__setattr__(self, "canonical_request_json", json_text)
        object.__setattr__(self, "request_payload", MappingProxyType(json.loads(json_text)))
        material = {
            "command_id": self.command_id, "tenant_id": self.tenant_id, "user_id": self.user_id,
            "credential_id": self.credential_id, "actor_type": self.actor_type.value, "actor_id": self.actor_id,
            "source": self.source, "action": self.action.value, "account_scope": self.account_scope,
            "strategy_id": self.strategy_id, "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id, "request_json": json.loads(json_text),
        }
        object.__setattr__(self, "request_fingerprint", _hash_material(COMMAND_FINGERPRINT_VERSION, material))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: UUID | str
    economic_order_id: UUID | str
    command_id: UUID | str
    tenant_id: int
    credential_id: int
    account_scope: str
    exchange_id: str
    instrument_id: str
    market_type: str
    side: str
    target_quantity: Quantity
    instrument_rule_snapshot_id: UUID | str
    instrument_rule_version: str
    order_type: str
    execution_algo: str
    rounding_mode: str
    position_side: str = ""
    reduce_only: bool = False
    time_in_force: str = ""
    limit_price: Price | None = None
    quote_notional: QuoteAmount | None = None
    intent_version: int = 1
    payload_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("intent_id", "economic_order_id", "command_id", "instrument_rule_snapshot_id"):
            object.__setattr__(self, name, canonical_uuid(getattr(self, name), name))
        object.__setattr__(self, "tenant_id", _positive_integer(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "credential_id", _positive_integer(self.credential_id, "credential_id"))
        object.__setattr__(self, "account_scope", _canonical_scope(self.account_scope, "account_scope"))
        object.__setattr__(self, "exchange_id", _required_text(self.exchange_id, "exchange_id", max_length=50).lower())
        object.__setattr__(self, "instrument_id", _canonical_instrument(self.instrument_id))
        object.__setattr__(self, "market_type", _canonical_market_type(self.market_type))
        side = _required_text(self.side, "side", max_length=8).upper()
        if side not in {"BUY", "SELL"}:
            raise CommandIntentContractError("side must be BUY or SELL")
        object.__setattr__(self, "side", side)
        if not isinstance(self.target_quantity, Quantity) or self.target_quantity.value <= 0:
            raise CommandIntentContractError("target_quantity must be a positive Quantity")
        for name in ("order_type", "execution_algo", "rounding_mode"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name, max_length=32).upper())
        position_side = "" if self.position_side == "" else _required_text(self.position_side, "position_side", max_length=12).upper()
        if position_side not in {"", "LONG", "SHORT"}:
            raise CommandIntentContractError("position_side must be LONG, SHORT, or empty")
        object.__setattr__(self, "position_side", position_side)
        if not isinstance(self.reduce_only, bool):
            raise CommandIntentContractError("reduce_only must be bool")
        tif = "" if self.time_in_force == "" else _required_text(self.time_in_force, "time_in_force", max_length=16).upper()
        object.__setattr__(self, "time_in_force", tif)
        if self.limit_price is not None and not isinstance(self.limit_price, Price):
            raise CommandIntentContractError("limit_price must be Price or None")
        if self.quote_notional is not None and not isinstance(self.quote_notional, QuoteAmount):
            raise CommandIntentContractError("quote_notional must be QuoteAmount or None")
        if not isinstance(self.intent_version, int) or isinstance(self.intent_version, bool) or self.intent_version != 1:
            raise CommandIntentContractError("PR-03 only accepts intent_version 1")
        object.__setattr__(self, "instrument_rule_version", _required_text(self.instrument_rule_version, "instrument_rule_version", max_length=100))
        material = self.canonical_material()
        object.__setattr__(self, "payload_hash", _hash_material(INTENT_PAYLOAD_HASH_VERSION, material))

    def canonical_material(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "intent_id": self.intent_id, "economic_order_id": self.economic_order_id, "command_id": self.command_id,
            "tenant_id": self.tenant_id, "credential_id": self.credential_id, "account_scope": self.account_scope,
            "exchange_id": self.exchange_id, "instrument_id": self.instrument_id, "market_type": self.market_type,
            "side": self.side, "position_side": self.position_side, "reduce_only": self.reduce_only,
            "order_type": self.order_type, "execution_algo": self.execution_algo, "time_in_force": self.time_in_force,
            "target_quantity": self.target_quantity.to_string(),
            "limit_price": None if self.limit_price is None else self.limit_price.to_string(),
            "quote_notional": None if self.quote_notional is None else self.quote_notional.to_string(),
            "instrument_rule_snapshot_id": self.instrument_rule_snapshot_id,
            "instrument_rule_version": self.instrument_rule_version, "rounding_mode": self.rounding_mode,
            "intent_version": self.intent_version,
        })


@dataclass(frozen=True, slots=True)
class RiskReservation:
    reservation_id: UUID | str
    command_id: UUID | str
    economic_order_id: UUID | str
    tenant_id: int
    credential_id: int
    account_scope: str
    reservation_kind: str
    currency: str
    reserved_notional: QuoteAmount
    reserved_margin: QuoteAmount
    reserved_position_qty: Quantity
    limits_snapshot: Mapping[str, Any]
    risk_input_hash: str
    expires_at: datetime | None

    def __post_init__(self) -> None:
        for name in ("reservation_id", "command_id", "economic_order_id"):
            object.__setattr__(self, name, canonical_uuid(getattr(self, name), name))
        object.__setattr__(self, "tenant_id", _positive_integer(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "credential_id", _positive_integer(self.credential_id, "credential_id"))
        object.__setattr__(self, "account_scope", _canonical_scope(self.account_scope, "account_scope"))
        object.__setattr__(self, "reservation_kind", _required_text(self.reservation_kind, "reservation_kind", max_length=32).upper())
        object.__setattr__(self, "currency", _canonical_currency(self.currency))
        for name, type_ in (("reserved_notional", QuoteAmount), ("reserved_margin", QuoteAmount), ("reserved_position_qty", Quantity)):
            if not isinstance(getattr(self, name), type_):
                raise CommandIntentContractError(f"{name} must use the PR-01 decimal contract")
        object.__setattr__(self, "risk_input_hash", _canonical_hash(self.risk_input_hash, "risk_input_hash"))
        limits_json = canonical_json(self.limits_snapshot)
        object.__setattr__(self, "limits_snapshot", MappingProxyType(json.loads(limits_json)))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _aware_utc(self.expires_at, "expires_at"))

    @property
    def canonical_limits_json(self) -> str:
        return canonical_json(self.limits_snapshot)

    def immutable_fingerprint(self) -> str:
        return _hash_material("risk-reservation-v1", {
            "reservation_id": self.reservation_id, "command_id": self.command_id,
            "economic_order_id": self.economic_order_id, "tenant_id": self.tenant_id,
            "credential_id": self.credential_id, "account_scope": self.account_scope,
            "reservation_kind": self.reservation_kind, "currency": self.currency,
            "reserved_notional": self.reserved_notional.to_string(), "reserved_margin": self.reserved_margin.to_string(),
            "reserved_position_qty": self.reserved_position_qty.to_string(), "limits": json.loads(self.canonical_limits_json),
            "risk_input_hash": self.risk_input_hash,
            "expires_at": None if self.expires_at is None else self.expires_at.isoformat(),
        })


@dataclass(frozen=True, slots=True)
class CommandGraph:
    command: OrderCommand
    intent: OrderIntent

    def __post_init__(self) -> None:
        if self.intent.command_id != self.command.command_id:
            raise CommandIntentContractError("intent.command_id must match command.command_id")
        if self.intent.tenant_id != self.command.tenant_id or self.intent.credential_id != self.command.credential_id:
            raise CommandIntentContractError("intent tenant and credential must match command")
        if self.intent.account_scope != self.command.account_scope:
            raise CommandIntentContractError("intent account_scope must match command")


@dataclass(frozen=True, slots=True)
class CommandGraphResult:
    command_id: str
    intent_id: str
    economic_order_id: str
    state: EconomicOrderState
    disposition: CommandGraphDisposition


@dataclass(frozen=True, slots=True)
class ReservationTransitionResult:
    reservation_id: str
    state: ReservationState
    version: int
    disposition: ReservationTransitionDisposition
