"""Transactional storage for Wave 2 outbox and projection contracts.

The repository owns only durable outbox, lease, inbox, checkpoint, and rebuild
generation facts.  It deliberately does not start a worker, publish a message,
or alter any trading execution path.  Callers provide an already-open DB-API
connection so a future aggregate mutation can share the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Protocol
from uuid import uuid4

from app.domain.outbox_projection_contracts import (
    OutboxConflict,
    OutboxEvent,
    OutboxProjectionContractError,
    ProjectionApplyResult,
    ProjectionCheckpoint,
    ProjectionGap,
    canonical_payload_json,
    apply_projection_event,
)


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class OutboxRepositoryError(RuntimeError):
    """Base typed failure for durable outbox and projection storage."""


class OutboxLeaseConflict(OutboxRepositoryError):
    """A caller no longer owns the exact leased outbox event."""


class ProjectionRepositoryConflict(OutboxRepositoryError):
    """Persisted projection facts cannot safely be advanced."""


class ProjectionGenerationConflict(OutboxRepositoryError):
    """A rebuild generation cannot be reused with different immutable facts."""


class OutboxPersistDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class OutboxPersistResult:
    event: OutboxEvent
    disposition: OutboxPersistDisposition


@dataclass(frozen=True, slots=True)
class LeasedOutboxEvent:
    event: OutboxEvent
    lease_owner: str
    lease_fencing_token: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectionPersistResult:
    result: ProjectionApplyResult
    generation_id: str | None


@dataclass(frozen=True, slots=True)
class ProjectionGeneration:
    generation_id: str
    consumer_name: str
    build_fingerprint: str
    source_high_watermark: int
    state: str


def _row(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


def _zero_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise OutboxRepositoryError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


def _text(value: object, field_name: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > max_length or not value.isascii():
        raise OutboxRepositoryError(f"{field_name} must be canonical ASCII text")
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def outbox_event_fingerprint(event: OutboxEvent) -> str:
    """Hash all immutable event facts, including canonical payload content."""

    material = "|".join((
        "outbox-persistence-v1", event.event_id, event.aggregate_type,
        event.aggregate_id, str(event.aggregate_version), event.event_type,
        event.schema_version, event.payload_hash,
    ))
    return _sha256(material)


def _event_from_row(row: Any) -> OutboxEvent:
    payload = _row(row, 5, "payload_json")
    if isinstance(payload, str):
        payload = json.loads(payload)
    event = OutboxEvent(
        aggregate_type=str(_row(row, 1, "aggregate_type")),
        aggregate_id=str(_row(row, 2, "aggregate_id")),
        aggregate_version=int(_row(row, 3, "aggregate_version")),
        event_type=str(_row(row, 4, "event_type")),
        schema_version=str(_row(row, 6, "schema_version")),
        payload=payload,
    )
    if event.event_id != str(_row(row, 0, "event_id")):
        raise OutboxRepositoryError("persisted outbox event identity is non-canonical")
    if event.payload_hash != str(_row(row, 7, "payload_hash")):
        raise OutboxRepositoryError("persisted outbox payload hash is non-canonical")
    return event


class OutboxProjectionRepository:
    """Atomic, replay-safe persistence over the Wave 2 append-only schema."""

    def persist_event(self, connection: Connection, event: OutboxEvent, *, available_at: datetime) -> OutboxPersistResult:
        available_at = _zero_utc(available_at, "available_at")
        cursor = connection.cursor()
        try:
            fingerprint = outbox_event_fingerprint(event)
            cursor.execute(
                """
                INSERT INTO qd_transactional_outbox (
                    event_id, aggregate_type, aggregate_id, aggregate_version,
                    event_type, payload_json, available_at, schema_version,
                    payload_hash, event_fingerprint
                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING event_id
                """,
                (event.event_id, event.aggregate_type, event.aggregate_id,
                 event.aggregate_version, event.event_type,
                 event.canonical_payload, available_at, event.schema_version,
                 event.payload_hash, fingerprint),
            )
            if cursor.fetchone() is not None:
                connection.commit()
                return OutboxPersistResult(event, OutboxPersistDisposition.CREATED)
            existing = self._load_event(cursor, event.event_id, lock=True)
            if existing is None:
                raise OutboxConflict("outbox uniqueness conflict is not visible")
            if existing != event:
                raise OutboxConflict("outbox event identity names different immutable facts")
            connection.commit()
            return OutboxPersistResult(event, OutboxPersistDisposition.REPLAYED)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def lease_next(
        self,
        connection: Connection,
        *,
        lease_owner: str,
        now_utc: datetime,
        lease_duration: timedelta,
    ) -> LeasedOutboxEvent | None:
        owner = _text(lease_owner, "lease_owner")
        now_utc = _zero_utc(now_utc, "now_utc")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise OutboxRepositoryError("lease_duration must be positive")
        expires_at = now_utc + lease_duration
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT event_id
                      FROM qd_transactional_outbox
                     WHERE published_at IS NULL
                       AND available_at <= %s
                       AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
                     ORDER BY available_at, event_id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                )
                UPDATE qd_transactional_outbox AS outbox
                   SET lease_owner = %s,
                       lease_expires_at = %s,
                       lease_fencing_token = outbox.lease_fencing_token + 1,
                       publish_attempts = outbox.publish_attempts + 1
                  FROM candidate
                 WHERE outbox.event_id = candidate.event_id
                RETURNING outbox.event_id, outbox.aggregate_type, outbox.aggregate_id,
                          outbox.aggregate_version, outbox.event_type, outbox.payload_json,
                          outbox.schema_version, outbox.payload_hash, outbox.lease_fencing_token
                """,
                (now_utc, now_utc, owner, expires_at),
            )
            row = cursor.fetchone()
            if row is None:
                connection.commit()
                return None
            event = _event_from_row(row)
            token = int(_row(row, 8, "lease_fencing_token"))
            connection.commit()
            return LeasedOutboxEvent(event, owner, token, expires_at)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def mark_published(
        self,
        connection: Connection,
        lease: LeasedOutboxEvent,
        *,
        published_at: datetime,
    ) -> None:
        published_at = _zero_utc(published_at, "published_at")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE qd_transactional_outbox
                   SET published_at = %s, lease_expires_at = NULL
                 WHERE event_id = %s
                   AND published_at IS NULL
                   AND lease_owner = %s
                   AND lease_fencing_token = %s
                RETURNING event_id
                """,
                (published_at, lease.event.event_id, lease.lease_owner, lease.lease_fencing_token),
            )
            if cursor.fetchone() is None:
                raise OutboxLeaseConflict("outbox lease is no longer owned by this caller")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def apply_to_projection(
        self,
        connection: Connection,
        *,
        consumer_name: str,
        event: OutboxEvent,
        supported_schemas: Iterable[tuple[str, str]],
        now_utc: datetime,
        generation_id: str | None = None,
    ) -> ProjectionPersistResult:
        consumer_name = _text(consumer_name, "consumer_name")
        now_utc = _zero_utc(now_utc, "now_utc")
        if generation_id is not None:
            generation_id = str(generation_id)
        cursor = connection.cursor()
        try:
            persisted = self._load_event(cursor, event.event_id, lock=False)
            if persisted is None or persisted != event:
                raise ProjectionRepositoryConflict("projection event is absent or differs from persisted facts")
            checkpoint_id, checkpoint = self._load_or_create_checkpoint(cursor, consumer_name, event)
            result = apply_projection_event(
                checkpoint, event, supported_schemas=supported_schemas, now_utc=now_utc,
            )
            if not result.idempotent_replay:
                cursor.execute(
                    """
                    UPDATE qd_projection_checkpoints
                       SET last_applied_version = %s, last_event_id = %s,
                           last_payload_hash = %s, generation_id = COALESCE(%s, generation_id),
                           updated_at = %s
                     WHERE id = %s
                    """,
                    (result.checkpoint.last_applied_version, result.checkpoint.last_event_id,
                     result.checkpoint.last_payload_hash, generation_id, now_utc, checkpoint_id),
                )
            cursor.execute(
                """
                INSERT INTO qd_consumer_inbox (consumer_name, event_id, result_hash)
                VALUES (%s,%s,%s)
                ON CONFLICT (consumer_name, event_id) DO NOTHING
                """,
                (consumer_name, event.event_id, result.checkpoint.last_payload_hash),
            )
            connection.commit()
            return ProjectionPersistResult(result, generation_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def start_rebuild(
        self,
        connection: Connection,
        *,
        consumer_name: str,
        build_fingerprint: str,
        source_high_watermark: int,
    ) -> ProjectionGeneration:
        consumer_name = _text(consumer_name, "consumer_name")
        if not isinstance(build_fingerprint, str) or len(build_fingerprint) != 64:
            raise ProjectionGenerationConflict("build_fingerprint must be SHA-256")
        if isinstance(source_high_watermark, bool) or not isinstance(source_high_watermark, int) or source_high_watermark < 0:
            raise ProjectionGenerationConflict("source_high_watermark must be non-negative")
        cursor = connection.cursor()
        try:
            generation_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO qd_projection_generations (
                    id, consumer_name, build_fingerprint, state, source_high_watermark
                ) VALUES (%s,%s,%s,'BUILDING',%s)
                ON CONFLICT (consumer_name, build_fingerprint) DO NOTHING
                RETURNING id, state
                """,
                (generation_id, consumer_name, build_fingerprint, source_high_watermark),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT id, state, source_high_watermark
                      FROM qd_projection_generations
                     WHERE consumer_name = %s AND build_fingerprint = %s
                     FOR UPDATE
                    """,
                    (consumer_name, build_fingerprint),
                )
                row = cursor.fetchone()
                if row is None or int(_row(row, 2, "source_high_watermark")) != source_high_watermark:
                    raise ProjectionGenerationConflict("rebuild fingerprint names different immutable source facts")
                generation_id = str(_row(row, 0, "id"))
                state = str(_row(row, 1, "state"))
            else:
                state = str(_row(row, 1, "state"))
            connection.commit()
            return ProjectionGeneration(generation_id, consumer_name, build_fingerprint, source_high_watermark, state)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def complete_rebuild(self, connection: Connection, generation: ProjectionGeneration, *, now_utc: datetime) -> ProjectionGeneration:
        now_utc = _zero_utc(now_utc, "now_utc")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE qd_projection_generations
                   SET state = 'READY', completed_at = %s
                 WHERE id = %s AND consumer_name = %s AND build_fingerprint = %s
                   AND state = 'BUILDING'
                RETURNING id
                """,
                (now_utc, generation.generation_id, generation.consumer_name, generation.build_fingerprint),
            )
            if cursor.fetchone() is None:
                raise ProjectionGenerationConflict("rebuild generation is not in BUILDING state")
            connection.commit()
            return ProjectionGeneration(
                generation.generation_id, generation.consumer_name,
                generation.build_fingerprint, generation.source_high_watermark, "READY",
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _load_event(self, cursor: Cursor, event_id: str, *, lock: bool) -> OutboxEvent | None:
        cursor.execute(
            """
            SELECT event_id, aggregate_type, aggregate_id, aggregate_version,
                   event_type, payload_json, schema_version, payload_hash
              FROM qd_transactional_outbox
             WHERE event_id = %s
            """ + (" FOR UPDATE" if lock else ""),
            (event_id,),
        )
        row = cursor.fetchone()
        return None if row is None else _event_from_row(row)

    def _load_or_create_checkpoint(self, cursor: Cursor, consumer_name: str, event: OutboxEvent) -> tuple[str, ProjectionCheckpoint]:
        checkpoint_id = str(uuid4())
        cursor.execute(
            """
            INSERT INTO qd_projection_checkpoints (
                id, consumer_name, aggregate_type, aggregate_id, last_applied_version
            ) VALUES (%s,%s,%s,%s,-1)
            ON CONFLICT (consumer_name, aggregate_type, aggregate_id) DO NOTHING
            RETURNING id
            """,
            (checkpoint_id, consumer_name, event.aggregate_type, event.aggregate_id),
        )
        created = cursor.fetchone()
        if created is not None:
            return checkpoint_id, ProjectionCheckpoint(consumer_name, event.aggregate_type, event.aggregate_id)
        cursor.execute(
            """
            SELECT id, last_applied_version, last_event_id, last_payload_hash
              FROM qd_projection_checkpoints
             WHERE consumer_name = %s AND aggregate_type = %s AND aggregate_id = %s
             FOR UPDATE
            """,
            (consumer_name, event.aggregate_type, event.aggregate_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ProjectionRepositoryConflict("projection checkpoint conflict is not visible")
        version = int(_row(row, 1, "last_applied_version"))
        return str(_row(row, 0, "id")), ProjectionCheckpoint(
            consumer_name, event.aggregate_type, event.aggregate_id, version,
            "" if version == -1 else str(_row(row, 2, "last_event_id")),
            "" if version == -1 else str(_row(row, 3, "last_payload_hash")),
        )
