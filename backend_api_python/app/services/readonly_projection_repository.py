"""Caller-owned, SELECT-only projection generation reader."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from app.domain.readonly_projection_summary_contracts import (
    ReadonlyProjectionGenerationSummary,
    ReadonlyProjectionSummaryError,
)


class ReadonlyProjectionRepositoryError(RuntimeError):
    """A read-only projection query failed or returned unsafe facts."""


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def _row(row: Any, index: int, key: str) -> Any:
    try:
        return row[key] if isinstance(row, dict) else row[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReadonlyProjectionRepositoryError("database returned an incomplete projection row") from exc


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyProjectionRepositoryError("database as_of must use zero-offset UTC")
    return value.astimezone(timezone.utc)


class ReadonlyProjectionRepository:
    """Read one generation and its checkpoint count without transaction control."""

    def read_latest_generation(
        self,
        connection: Connection,
        *,
        consumer_name: str,
        as_of: datetime,
    ) -> ReadonlyProjectionGenerationSummary | None:
        if not isinstance(consumer_name, str) or not consumer_name or consumer_name.strip() != consumer_name or not consumer_name.isascii():
            raise ReadonlyProjectionRepositoryError("consumer_name must be canonical ASCII text")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyProjectionRepositoryError("as_of must use zero-offset UTC")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT id, consumer_name, build_fingerprint, state,
                       source_high_watermark, processed_high_watermark,
                       expected_event_count, applied_event_count
                  FROM qd_projection_generations
                 WHERE consumer_name = %s
                 ORDER BY created_at DESC, id DESC
                 LIMIT 1
                """,
                (consumer_name,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            generation_id = str(_row(row, 0, "id"))
            cursor.execute(
                """
                SELECT COUNT(*)
                  FROM qd_projection_checkpoints
                 WHERE generation_id = %s AND consumer_name = %s
                """,
                (generation_id, consumer_name),
            )
            count_row = cursor.fetchone()
            if count_row is None:
                raise ReadonlyProjectionRepositoryError("database returned no checkpoint count")
            checkpoint_count = int(_row(count_row, 0, "count"))
            try:
                return ReadonlyProjectionGenerationSummary(
                    generation_id=generation_id,
                    consumer_name=str(_row(row, 1, "consumer_name")),
                    build_fingerprint=str(_row(row, 2, "build_fingerprint")),
                    state=str(_row(row, 3, "state")),
                    source_high_watermark=int(_row(row, 4, "source_high_watermark")),
                    processed_high_watermark=int(_row(row, 5, "processed_high_watermark")),
                    expected_event_count=int(_row(row, 6, "expected_event_count")),
                    applied_event_count=int(_row(row, 7, "applied_event_count")),
                    checkpoint_count=checkpoint_count,
                    as_of=_utc(as_of),
                )
            except (ReadonlyProjectionSummaryError, ValueError, TypeError) as exc:
                raise ReadonlyProjectionRepositoryError("database returned invalid projection facts") from exc
        except ReadonlyProjectionRepositoryError:
            raise
        except Exception as exc:
            raise ReadonlyProjectionRepositoryError("projection read failed") from exc
        finally:
            cursor.close()


__all__ = ["ReadonlyProjectionRepository", "ReadonlyProjectionRepositoryError"]
