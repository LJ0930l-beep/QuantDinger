"""Pure contracts for transactional outbox events and replay-safe projections.

This module has no worker, scheduler, database, or application read-model
dependency.  Storage and delivery are deliberately separated from the
deterministic identities defined here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from uuid import UUID, NAMESPACE_URL, uuid5


OUTBOX_CONTRACT_VERSION = "outbox-contract-v1"


class OutboxProjectionContractError(ValueError):
    """Base error for non-canonical outbox and projection facts."""


class OutboxConflict(OutboxProjectionContractError):
    """A business event identity was reused with a different payload."""


class ProjectionGap(OutboxProjectionContractError):
    """A projection cannot advance across a missing aggregate version."""


class ProjectionVersionConflict(OutboxProjectionContractError):
    """A checkpoint version was reused with different immutable facts."""


class UnsupportedEventSchema(OutboxProjectionContractError):
    """A projection does not understand the event type or schema version."""


def _text(value: object, field_name: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > max_length or not value.isascii():
        raise OutboxProjectionContractError(f"{field_name} must be canonical ASCII text")
    return value


def _uuid(value: UUID | str, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(value)).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise OutboxProjectionContractError(f"{field_name} must be a UUID") from exc


def _non_negative_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OutboxProjectionContractError(f"{field_name} must be a non-negative integer")
    return value


def _canonical_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise OutboxProjectionContractError("payload cannot contain binary float")
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, Mapping):
        values: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key != key.strip() or not key:
                raise OutboxProjectionContractError("payload has a non-canonical key")
            values[key] = _canonical_json_value(item)
        return {key: values[key] for key in sorted(values)}
    raise OutboxProjectionContractError("payload contains an unsupported value")


def canonical_payload_json(payload: Mapping[str, Any]) -> str:
    canonical = _canonical_json_value(payload)
    if not isinstance(canonical, dict):
        raise OutboxProjectionContractError("payload must be a mapping")
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """A deterministic event identity derived only from stable source facts."""

    aggregate_type: str
    aggregate_id: UUID | str
    aggregate_version: int
    event_type: str
    schema_version: str
    payload: Mapping[str, Any]
    payload_hash: str = field(init=False)
    event_id: str = field(init=False)
    canonical_payload: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_type", _text(self.aggregate_type, "aggregate_type", max_length=64))
        object.__setattr__(self, "aggregate_id", _uuid(self.aggregate_id, "aggregate_id"))
        object.__setattr__(self, "aggregate_version", _non_negative_integer(self.aggregate_version, "aggregate_version"))
        object.__setattr__(self, "event_type", _text(self.event_type, "event_type", max_length=96))
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version", max_length=64))
        payload = canonical_payload_json(self.payload)
        object.__setattr__(self, "canonical_payload", payload)
        object.__setattr__(self, "payload", MappingProxyType(json.loads(payload)))
        object.__setattr__(self, "payload_hash", hashlib.sha256(payload.encode("ascii")).hexdigest())
        identity = "|".join((OUTBOX_CONTRACT_VERSION, self.aggregate_type, self.aggregate_id, str(self.aggregate_version), self.event_type, self.schema_version))
        object.__setattr__(self, "event_id", str(uuid5(NAMESPACE_URL, identity)))


@dataclass(frozen=True, slots=True)
class ProjectionCheckpoint:
    """Per-consumer, per-aggregate monotonic checkpoint fact."""

    consumer_name: str
    aggregate_type: str
    aggregate_id: UUID | str
    last_applied_version: int = -1
    last_event_id: str = ""
    last_payload_hash: str = ""
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "consumer_name", _text(self.consumer_name, "consumer_name"))
        object.__setattr__(self, "aggregate_type", _text(self.aggregate_type, "aggregate_type", max_length=64))
        object.__setattr__(self, "aggregate_id", _uuid(self.aggregate_id, "aggregate_id"))
        if isinstance(self.last_applied_version, bool) or not isinstance(self.last_applied_version, int) or self.last_applied_version < -1:
            raise OutboxProjectionContractError("last_applied_version must be at least -1")
        if self.last_applied_version == -1:
            if self.last_event_id or self.last_payload_hash:
                raise OutboxProjectionContractError("empty checkpoint cannot name an event")
        else:
            object.__setattr__(self, "last_event_id", _uuid(self.last_event_id, "last_event_id"))
            if not isinstance(self.last_payload_hash, str) or len(self.last_payload_hash) != 64:
                raise OutboxProjectionContractError("last_payload_hash must be SHA-256")
        if self.updated_at is not None:
            if self.updated_at.tzinfo is None or self.updated_at.utcoffset() != timezone.utc.utcoffset(self.updated_at):
                raise OutboxProjectionContractError("updated_at must use a zero UTC offset")
            object.__setattr__(self, "updated_at", self.updated_at.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class ProjectionApplyResult:
    checkpoint: ProjectionCheckpoint
    idempotent_replay: bool


def apply_projection_event(
    checkpoint: ProjectionCheckpoint,
    event: OutboxEvent,
    *,
    supported_schemas: Iterable[tuple[str, str]],
    now_utc: datetime,
) -> ProjectionApplyResult:
    """Advance exactly one checkpoint, rejecting gaps and unknown schemas."""

    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None or now_utc.utcoffset() != timezone.utc.utcoffset(now_utc):
        raise OutboxProjectionContractError("now_utc must use a zero UTC offset")
    if (event.event_type, event.schema_version) not in set(supported_schemas):
        raise UnsupportedEventSchema("event type or schema version is unsupported")
    if (checkpoint.aggregate_type, checkpoint.aggregate_id) != (event.aggregate_type, event.aggregate_id):
        raise OutboxProjectionContractError("event scope does not match checkpoint")
    if event.aggregate_version == checkpoint.last_applied_version:
        if (event.event_id, event.payload_hash) == (checkpoint.last_event_id, checkpoint.last_payload_hash):
            return ProjectionApplyResult(checkpoint, True)
        raise ProjectionVersionConflict("same aggregate version has different event facts")
    if event.aggregate_version < checkpoint.last_applied_version:
        raise ProjectionVersionConflict("aggregate version cannot move backwards")
    if event.aggregate_version != checkpoint.last_applied_version + 1:
        raise ProjectionGap("aggregate version has a gap")
    return ProjectionApplyResult(
        ProjectionCheckpoint(checkpoint.consumer_name, checkpoint.aggregate_type, checkpoint.aggregate_id,
                             event.aggregate_version, event.event_id, event.payload_hash, now_utc.astimezone(timezone.utc)),
        False,
    )
