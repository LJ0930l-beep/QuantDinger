"""Pure registration and dispatch contracts for read-only projection consumers.

This module deliberately has no worker, scheduler, database, read-model, or
trading dependency. It validates the immutable facts a caller must supply to
the existing projection repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable

from app.domain.outbox_projection_contracts import OutboxEvent


PROJECTION_CONSUMER_CONTRACT_VERSION = "projection-consumer-v1"


class ProjectionConsumerContractError(ValueError):
    """Base typed failure for an invalid projection consumer contract."""


class UnsupportedProjectionEvent(ProjectionConsumerContractError):
    """The registered consumer cannot safely interpret an event."""


def _text(value: object, field_name: str, *, max_length: int = 160) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or not value.isascii()
        or len(value) > max_length
    ):
        raise ProjectionConsumerContractError(f"{field_name} must be canonical ASCII text")
    return value


def _schema_pair(value: object) -> tuple[str, str]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ProjectionConsumerContractError("supported schema must be an event/schema pair")
    return _text(value[0], "event_type", max_length=96), _text(value[1], "schema_version")


def _unique_pairs(values: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    pairs = tuple(_schema_pair(item) for item in values)
    if not pairs or len(set(pairs)) != len(pairs):
        raise ProjectionConsumerContractError("supported schemas must be non-empty and unique")
    return tuple(sorted(pairs))


def _unique_text(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    result = tuple(_text(item, field_name, max_length=64) for item in values)
    if not result or len(set(result)) != len(result):
        raise ProjectionConsumerContractError(f"{field_name} must be non-empty and unique")
    return tuple(sorted(result))


def _zero_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ProjectionConsumerContractError(f"{field_name} must be timezone.utc")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class RegisteredProjectionConsumer:
    """Immutable allow-list for one named read-only projection consumer."""

    consumer_name: str
    contract_version: str
    supported_schemas: tuple[tuple[str, str], ...]
    aggregate_types: tuple[str, ...]
    build_fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "consumer_name", _text(self.consumer_name, "consumer_name"))
        object.__setattr__(self, "contract_version", _text(self.contract_version, "contract_version", max_length=64))
        object.__setattr__(self, "supported_schemas", _unique_pairs(self.supported_schemas))
        object.__setattr__(self, "aggregate_types", _unique_text(self.aggregate_types, "aggregate_type"))
        if self.build_fingerprint and (
            not isinstance(self.build_fingerprint, str)
            or len(self.build_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.build_fingerprint)
        ):
            raise ProjectionConsumerContractError("build_fingerprint must be lowercase SHA-256")

    @property
    def fingerprint(self) -> str:
        material = {
            "contract_version": self.contract_version,
            "consumer_name": self.consumer_name,
            "supported_schemas": self.supported_schemas,
            "aggregate_types": self.aggregate_types,
            "build_fingerprint": self.build_fingerprint,
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    def accepts(self, event: OutboxEvent) -> bool:
        if not isinstance(event, OutboxEvent):
            raise ProjectionConsumerContractError("consumer requires an OutboxEvent")
        if event.aggregate_type not in self.aggregate_types:
            raise UnsupportedProjectionEvent("aggregate type is not registered for this consumer")
        if (event.event_type, event.schema_version) not in self.supported_schemas:
            raise UnsupportedProjectionEvent("event schema is not registered for this consumer")
        return True


@dataclass(frozen=True, slots=True)
class ProjectionConsumeRequest:
    """Validated caller-owned input for one deterministic consumer application."""

    consumer: RegisteredProjectionConsumer
    generation_id: str
    source_offset: int
    event: OutboxEvent
    now_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.consumer, RegisteredProjectionConsumer):
            raise ProjectionConsumerContractError("consumer must be a registered projection consumer")
        object.__setattr__(self, "generation_id", _text(self.generation_id, "generation_id"))
        if isinstance(self.source_offset, bool) or not isinstance(self.source_offset, int) or self.source_offset < 0:
            raise ProjectionConsumerContractError("source_offset must be a non-negative integer")
        if not isinstance(self.event, OutboxEvent):
            raise ProjectionConsumerContractError("event must be an OutboxEvent")
        self.consumer.accepts(self.event)
        object.__setattr__(self, "now_utc", _zero_utc(self.now_utc, "now_utc"))


__all__ = [
    "PROJECTION_CONSUMER_CONTRACT_VERSION",
    "ProjectionConsumeRequest",
    "ProjectionConsumerContractError",
    "RegisteredProjectionConsumer",
    "UnsupportedProjectionEvent",
]
