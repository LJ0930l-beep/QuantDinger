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
    def fetchall(self) -> list[Any]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


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
    expected_event_count: int
    applied_event_count: int
    processed_high_watermark: int
    is_current: bool


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


def _generation_from_row(row: Any) -> ProjectionGeneration:
    """Build the public generation view only from persisted database facts."""

    return ProjectionGeneration(
        generation_id=str(_row(row, 0, "id")),
        consumer_name=str(_row(row, 1, "consumer_name")),
        build_fingerprint=str(_row(row, 2, "build_fingerprint")),
        source_high_watermark=int(_row(row, 3, "source_high_watermark")),
        state=str(_row(row, 4, "state")),
        expected_event_count=int(_row(row, 5, "expected_event_count")),
        applied_event_count=int(_row(row, 6, "applied_event_count")),
        processed_high_watermark=int(_row(row, 7, "processed_high_watermark")),
        is_current=bool(_row(row, 8, "is_current")),
    )


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
                return OutboxPersistResult(event, OutboxPersistDisposition.CREATED)
            existing = self._load_event(cursor, event.event_id, lock=True)
            if existing is None:
                raise OutboxConflict("outbox uniqueness conflict is not visible")
            if existing != event:
                raise OutboxConflict("outbox event identity names different immutable facts")
            return OutboxPersistResult(event, OutboxPersistDisposition.REPLAYED)
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
                return None
            event = _event_from_row(row)
            token = int(_row(row, 8, "lease_fencing_token"))
            return LeasedOutboxEvent(event, owner, token, expires_at)
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
        generation_id: str,
        source_offset: int,
    ) -> ProjectionPersistResult:
        consumer_name = _text(consumer_name, "consumer_name")
        now_utc = _zero_utc(now_utc, "now_utc")
        generation_id = str(generation_id)
        if isinstance(source_offset, bool) or not isinstance(source_offset, int) or source_offset < 0:
            raise ProjectionRepositoryConflict("source_offset must be non-negative")
        cursor = connection.cursor()
        try:
            persisted = self._load_event(cursor, event.event_id, lock=False)
            if persisted is None or persisted != event:
                raise ProjectionRepositoryConflict("projection event is absent or differs from persisted facts")
            self._load_generation_for_apply(cursor, generation_id, consumer_name)
            checkpoint_id, checkpoint = self._load_or_create_checkpoint(cursor, generation_id, consumer_name, event)
            result = apply_projection_event(
                checkpoint, event, supported_schemas=supported_schemas, now_utc=now_utc,
            )
            if not result.idempotent_replay:
                recorded = self._record_generation_event(
                    cursor, generation_id=generation_id, source_offset=source_offset,
                    event=event, applied_at=now_utc,
                )
                if not recorded:
                    raise ProjectionGenerationConflict(
                        "generation event identity replays but projection checkpoint does not"
                    )
                cursor.execute(
                    """
                    UPDATE qd_projection_checkpoints
                       SET last_applied_version = %s, last_event_id = %s,
                           last_payload_hash = %s, updated_at = %s
                     WHERE id = %s AND generation_id = %s
                    """,
                    (result.checkpoint.last_applied_version, result.checkpoint.last_event_id,
                     result.checkpoint.last_payload_hash, now_utc, checkpoint_id, generation_id),
                )
                cursor.execute(
                    """
                    UPDATE qd_projection_generations
                       SET applied_event_count = applied_event_count + 1,
                           processed_high_watermark = GREATEST(processed_high_watermark, %s)
                     WHERE id = %s
                    """,
                    (source_offset, generation_id),
                )
            elif not self._generation_event_matches(
                cursor, generation_id=generation_id, source_offset=source_offset, event=event,
            ):
                raise ProjectionGenerationConflict(
                    "projection replay lacks an identical immutable generation event fact"
                )
            cursor.execute(
                """
                INSERT INTO qd_consumer_inbox (consumer_name, event_id, result_hash)
                VALUES (%s,%s,%s)
                ON CONFLICT (consumer_name, event_id) DO NOTHING
                """,
                (consumer_name, event.event_id, result.checkpoint.last_payload_hash),
            )
            return ProjectionPersistResult(result, generation_id)
        finally:
            cursor.close()

    def start_rebuild(
        self,
        connection: Connection,
        *,
        consumer_name: str,
        build_fingerprint: str,
        source_high_watermark: int,
        expected_event_count: int,
    ) -> ProjectionGeneration:
        consumer_name = _text(consumer_name, "consumer_name")
        if not isinstance(build_fingerprint, str) or len(build_fingerprint) != 64:
            raise ProjectionGenerationConflict("build_fingerprint must be SHA-256")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (source_high_watermark, expected_event_count)):
            raise ProjectionGenerationConflict("rebuild high watermark and expected count must be non-negative")
        if expected_event_count != source_high_watermark + 1:
            raise ProjectionGenerationConflict(
                "rebuild expected_event_count must describe every offset through the source high watermark"
            )
        cursor = connection.cursor()
        try:
            generation_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO qd_projection_generations (
                    id, consumer_name, build_fingerprint, state, source_high_watermark, expected_event_count
                ) VALUES (%s,%s,%s,'BUILDING',%s,%s)
                ON CONFLICT (consumer_name, build_fingerprint) DO NOTHING
                RETURNING id, consumer_name, build_fingerprint, source_high_watermark,
                          state, expected_event_count, applied_event_count,
                          processed_high_watermark, is_current
                """,
                (generation_id, consumer_name, build_fingerprint, source_high_watermark, expected_event_count),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT id, consumer_name, build_fingerprint, source_high_watermark,
                           state, expected_event_count, applied_event_count,
                           processed_high_watermark, is_current
                      FROM qd_projection_generations
                     WHERE consumer_name = %s AND build_fingerprint = %s
                     FOR UPDATE
                    """,
                    (consumer_name, build_fingerprint),
                )
                row = cursor.fetchone()
                if row is None or (
                    int(_row(row, 3, "source_high_watermark")),
                    int(_row(row, 5, "expected_event_count")),
                ) != (source_high_watermark, expected_event_count):
                    raise ProjectionGenerationConflict("rebuild fingerprint names different immutable source facts")
            return _generation_from_row(row)
        finally:
            cursor.close()

    def complete_rebuild(self, connection: Connection, generation: ProjectionGeneration, *, now_utc: datetime) -> ProjectionGeneration:
        now_utc = _zero_utc(now_utc, "now_utc")
        cursor = connection.cursor()
        try:
            persisted = self._load_generation(
                cursor, generation, required_state="BUILDING",
            )
            cursor.execute(
                "SELECT source_offset FROM qd_projection_generation_events "
                "WHERE generation_id = %s ORDER BY source_offset FOR UPDATE",
                (persisted.generation_id,),
            )
            offsets = [int(_row(row, 0, "source_offset")) for row in cursor.fetchall()]
            event_count = len(offsets)
            first_offset = offsets[0] if offsets else -1
            last_offset = offsets[-1] if offsets else -1
            distinct_offset_count = len(set(offsets))
            contiguous = offsets == list(range(event_count))
            if not (
                event_count == persisted.expected_event_count == persisted.applied_event_count
                and event_count == distinct_offset_count
                and first_offset == 0
                and last_offset == persisted.source_high_watermark == persisted.processed_high_watermark
                and contiguous
            ):
                raise ProjectionGenerationConflict(
                    "rebuild event count, contiguous offsets, and exact high watermark are required"
                )
            cursor.execute(
                "UPDATE qd_projection_generations SET state = 'READY', completed_at = %s WHERE id = %s",
                (now_utc, persisted.generation_id),
            )
            return self._load_generation(cursor, persisted, required_state="READY")
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

    def fail_rebuild(self, connection: Connection, generation: ProjectionGeneration, *, failure_reason: str) -> ProjectionGeneration:
        reason = _text(failure_reason, "failure_reason", max_length=512)
        cursor = connection.cursor()
        try:
            persisted = self._load_generation(cursor, generation, required_state="BUILDING")
            cursor.execute(
                "UPDATE qd_projection_generations SET state = 'FAILED', failure_reason = %s WHERE id = %s",
                (reason, persisted.generation_id),
            )
            return self._load_generation(cursor, persisted, required_state="FAILED")
        finally:
            cursor.close()

    def promote_rebuild(self, connection: Connection, generation: ProjectionGeneration, *, now_utc: datetime) -> ProjectionGeneration:
        now_utc = _zero_utc(now_utc, "now_utc")
        cursor = connection.cursor()
        try:
            persisted = self._load_generation(cursor, generation, required_state="READY")
            cursor.execute("UPDATE qd_projection_generations SET is_current = FALSE WHERE consumer_name = %s AND is_current", (persisted.consumer_name,))
            cursor.execute("UPDATE qd_projection_generations SET is_current = TRUE, promoted_at = %s WHERE id = %s", (now_utc, persisted.generation_id))
            return self._load_generation(cursor, persisted, required_state="READY")
        finally:
            cursor.close()

    def _load_generation_for_apply(self, cursor: Cursor, generation_id: str, consumer_name: str) -> None:
        cursor.execute("SELECT state FROM qd_projection_generations WHERE id = %s AND consumer_name = %s FOR UPDATE", (generation_id, consumer_name))
        row = cursor.fetchone()
        if row is None or str(_row(row, 0, "state")) != "BUILDING":
            raise ProjectionRepositoryConflict("only a BUILDING generation may accept source events")

    def _load_generation(
        self,
        cursor: Cursor,
        generation: ProjectionGeneration,
        *,
        required_state: str,
    ) -> ProjectionGeneration:
        """Lock the canonical generation identity and discard caller-provided counters."""

        cursor.execute(
            """
            SELECT id, consumer_name, build_fingerprint, source_high_watermark,
                   state, expected_event_count, applied_event_count,
                   processed_high_watermark, is_current
              FROM qd_projection_generations
             WHERE id = %s AND consumer_name = %s AND build_fingerprint = %s
               AND state = %s
             FOR UPDATE
            """,
            (
                generation.generation_id, generation.consumer_name,
                generation.build_fingerprint, required_state,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise ProjectionGenerationConflict(
                f"generation identity is absent or not {required_state}"
            )
        return _generation_from_row(row)

    def _record_generation_event(
        self,
        cursor: Cursor,
        *,
        generation_id: str,
        source_offset: int,
        event: OutboxEvent,
        applied_at: datetime,
    ) -> bool:
        """Append one immutable source application or classify its exact replay."""

        cursor.execute(
            """
            INSERT INTO qd_projection_generation_events (
                generation_id, source_offset, event_id, payload_hash, applied_at
            ) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            RETURNING generation_id
            """,
            (generation_id, source_offset, event.event_id, event.payload_hash, applied_at),
        )
        if cursor.fetchone() is not None:
            return True
        if self._generation_event_matches(
            cursor, generation_id=generation_id, source_offset=source_offset, event=event,
        ):
            return False
        raise ProjectionGenerationConflict(
            "generation source offset or event identity names different immutable facts"
        )

    def _generation_event_matches(
        self,
        cursor: Cursor,
        *,
        generation_id: str,
        source_offset: int,
        event: OutboxEvent,
    ) -> bool:
        cursor.execute(
            """
            SELECT source_offset, event_id, payload_hash
              FROM qd_projection_generation_events
             WHERE generation_id = %s AND (source_offset = %s OR event_id = %s)
             ORDER BY source_offset
             FOR UPDATE
            """,
            (generation_id, source_offset, event.event_id),
        )
        rows = cursor.fetchall()
        return len(rows) == 1 and (
            int(_row(rows[0], 0, "source_offset")),
            str(_row(rows[0], 1, "event_id")),
            str(_row(rows[0], 2, "payload_hash")),
        ) == (source_offset, event.event_id, event.payload_hash)

    def _load_or_create_checkpoint(self, cursor: Cursor, generation_id: str, consumer_name: str, event: OutboxEvent) -> tuple[str, ProjectionCheckpoint]:
        checkpoint_id = str(uuid4())
        cursor.execute(
            """
            INSERT INTO qd_projection_checkpoints (
                id, generation_id, consumer_name, aggregate_type, aggregate_id, last_applied_version
            ) VALUES (%s,%s,%s,%s,%s,-1)
            ON CONFLICT (generation_id, consumer_name, aggregate_type, aggregate_id) DO NOTHING
            RETURNING id
            """,
            (checkpoint_id, generation_id, consumer_name, event.aggregate_type, event.aggregate_id),
        )
        created = cursor.fetchone()
        if created is not None:
            return checkpoint_id, ProjectionCheckpoint(consumer_name, event.aggregate_type, event.aggregate_id)
        cursor.execute(
            """
            SELECT id, last_applied_version, last_event_id, last_payload_hash
              FROM qd_projection_checkpoints
             WHERE generation_id = %s AND consumer_name = %s AND aggregate_type = %s AND aggregate_id = %s
             FOR UPDATE
            """,
            (generation_id, consumer_name, event.aggregate_type, event.aggregate_id),
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
