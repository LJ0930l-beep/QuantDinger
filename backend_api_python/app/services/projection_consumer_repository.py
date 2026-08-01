"""Caller-owned persistence for one registered projection consumer.

This boundary is intentionally small: it validates the already-typed consume
request, delegates to :class:`OutboxProjectionRepository`, and never owns the
surrounding database transaction.  A future consumer may compose this call
with other durable facts on the same connection.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.domain.outbox_projection_contracts import (
    OutboxConflict,
    OutboxProjectionContractError,
)
from app.domain.projection_consumer_contracts import (
    ConsumerApplyDisposition,
    ProjectionConsumeRequest,
    ProjectionConsumeResult,
    ProjectionConsumerContractError,
)
from app.services.outbox_projection_repository import (
    OutboxProjectionRepository,
    OutboxRepositoryError,
    ProjectionPersistResult,
)


class ProjectionConsumerRepositoryError(RuntimeError):
    """Typed failure for consumer persistence and database-driver failures."""


class Connection(Protocol):
    def cursor(self) -> Any: ...


class ProjectionConsumerRepository:
    """Apply one typed event without committing or rolling back the connection."""

    def __init__(self, *, repository: OutboxProjectionRepository | None = None) -> None:
        self._repository = repository or OutboxProjectionRepository()

    def consume(
        self,
        connection: Connection,
        request: ProjectionConsumeRequest,
    ) -> ProjectionConsumeResult:
        """Apply ``request`` through the shared projection repository.

        Business conflicts and typed contract errors are deliberately allowed
        to propagate unchanged.  Only an unclassified database/driver failure
        is wrapped; neither path changes transaction ownership.
        """

        if not isinstance(request, ProjectionConsumeRequest):
            raise ProjectionConsumerContractError(
                "projection consumer persistence requires ProjectionConsumeRequest"
            )
        request.consumer.accepts(request.event)
        try:
            persisted = self._repository.apply_to_projection(
                connection,
                consumer_name=request.consumer.consumer_name,
                event=request.event,
                supported_schemas=request.consumer.supported_schemas,
                now_utc=request.now_utc,
                generation_id=request.generation_id,
                source_offset=request.source_offset,
            )
        except (
            OutboxRepositoryError,
            OutboxConflict,
            OutboxProjectionContractError,
            ProjectionConsumerContractError,
        ):
            raise
        except Exception as exc:
            raise ProjectionConsumerRepositoryError(
                "projection consumer database operation failed"
            ) from exc

        if not isinstance(persisted, ProjectionPersistResult):
            raise ProjectionConsumerRepositoryError(
                "projection repository returned an untyped result"
            )
        disposition = (
            ConsumerApplyDisposition.REPLAYED
            if persisted.result.idempotent_replay
            else ConsumerApplyDisposition.CREATED
        )
        return ProjectionConsumeResult(
            request,
            disposition,
            persisted.result.checkpoint.last_applied_version,
        )

    # ``apply`` is an explicit spelling for callers that model consumption as
    # event application; it delegates to the one implementation above.
    def apply(
        self,
        connection: Connection,
        request: ProjectionConsumeRequest,
    ) -> ProjectionConsumeResult:
        return self.consume(connection, request)


__all__ = [
    "ProjectionConsumerRepository",
    "ProjectionConsumerRepositoryError",
]
