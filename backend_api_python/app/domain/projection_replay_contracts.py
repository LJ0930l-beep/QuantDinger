"""Pure deterministic replay reducer for registered projection consumers.

The reducer applies caller-owned Outbox events in source-offset order and
keeps only immutable in-memory facts. It does not run a worker, open a
database transaction, publish events, or update a trading projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Tuple

from app.domain.outbox_projection_contracts import (
    OutboxEvent,
    ProjectionCheckpoint,
    ProjectionGap,
    ProjectionVersionConflict,
    apply_projection_event,
)
from app.domain.projection_consumer_contracts import RegisteredProjectionConsumer


PROJECTION_REPLAY_CONTRACT_VERSION = "projection-replay-v1"


class ProjectionReplayError(ValueError):
    """Invalid source-offset or projection replay facts."""


class ProjectionReplayDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ProjectionReplayError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ProjectionReplayEvent:
    source_offset: int
    event: OutboxEvent

    def __post_init__(self) -> None:
        if isinstance(self.source_offset, bool) or not isinstance(self.source_offset, int) or self.source_offset < 0:
            raise ProjectionReplayError("source_offset must be non-negative")
        if not isinstance(self.event, OutboxEvent):
            raise ProjectionReplayError("event must be typed")


@dataclass(frozen=True, slots=True)
class ProjectionReplayState:
    consumer: RegisteredProjectionConsumer
    events: Tuple[ProjectionReplayEvent, ...] = ()
    checkpoints: Tuple[ProjectionCheckpoint, ...] = ()
    replay_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.consumer, RegisteredProjectionConsumer):
            raise ProjectionReplayError("consumer must be registered")
        if not isinstance(self.events, tuple) or any(not isinstance(item, ProjectionReplayEvent) for item in self.events):
            raise ProjectionReplayError("events must be an explicit typed tuple")
        offsets = tuple(item.source_offset for item in self.events)
        if offsets != tuple(range(len(offsets))):
            raise ProjectionReplayError("source offsets must be contiguous from zero")
        if not isinstance(self.checkpoints, tuple) or any(not isinstance(item, ProjectionCheckpoint) for item in self.checkpoints):
            raise ProjectionReplayError("checkpoints must be typed")
        keys = [(item.aggregate_type, item.aggregate_id) for item in self.checkpoints]
        if len(keys) != len(set(keys)):
            raise ProjectionReplayError("one checkpoint per aggregate is required")
        material = {
            "version": PROJECTION_REPLAY_CONTRACT_VERSION,
            "consumer": self.consumer.fingerprint,
            "events": [(item.source_offset, item.event.event_id, item.event.payload_hash) for item in self.events],
            "checkpoints": [(item.aggregate_type, item.aggregate_id, item.last_applied_version, item.last_event_id, item.last_payload_hash) for item in self.checkpoints],
        }
        object.__setattr__(self, "replay_fingerprint", hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest())


@dataclass(frozen=True, slots=True)
class ProjectionReplayResult:
    disposition: ProjectionReplayDisposition
    state: ProjectionReplayState


def _find_checkpoint(state: ProjectionReplayState, event: OutboxEvent) -> ProjectionCheckpoint:
    for checkpoint in state.checkpoints:
        if (checkpoint.aggregate_type, checkpoint.aggregate_id) == (event.aggregate_type, event.aggregate_id):
            return checkpoint
    return ProjectionCheckpoint(state.consumer.consumer_name, event.aggregate_type, event.aggregate_id)


def apply_projection_replay(state: ProjectionReplayState, item: ProjectionReplayEvent, *, now_utc: datetime) -> ProjectionReplayResult:
    """Apply one event, or return exact replay for an already applied offset."""

    if not isinstance(state, ProjectionReplayState) or not isinstance(item, ProjectionReplayEvent):
        raise ProjectionReplayError("typed replay state and event are required")
    now = _utc(now_utc, "now_utc")
    try:
        state.consumer.accepts(item.event)
    except Exception as exc:
        raise ProjectionReplayError("event is not accepted by the registered consumer") from exc
    expected_offset = len(state.events)
    if item.source_offset < expected_offset:
        prior = state.events[item.source_offset]
        if (prior.event.event_id, prior.event.payload_hash) == (item.event.event_id, item.event.payload_hash):
            return ProjectionReplayResult(ProjectionReplayDisposition.REPLAYED, state)
        raise ProjectionReplayError("source offset was reused with different event facts")
    if item.source_offset > expected_offset:
        raise ProjectionReplayError("source offset has a gap")
    checkpoint = _find_checkpoint(state, item.event)
    try:
        applied = apply_projection_event(checkpoint, item.event, supported_schemas=state.consumer.supported_schemas, now_utc=now)
    except (ProjectionGap, ProjectionVersionConflict) as exc:
        raise ProjectionReplayError("aggregate event sequence is not replayable") from exc
    updated = tuple(checkpoint for checkpoint in state.checkpoints if (checkpoint.aggregate_type, checkpoint.aggregate_id) != (item.event.aggregate_type, item.event.aggregate_id)) + (applied.checkpoint,)
    updated = tuple(sorted(updated, key=lambda value: (value.aggregate_type, value.aggregate_id)))
    next_state = ProjectionReplayState(state.consumer, state.events + (item,), updated)
    return ProjectionReplayResult(ProjectionReplayDisposition.CREATED, next_state)


__all__ = ["PROJECTION_REPLAY_CONTRACT_VERSION", "ProjectionReplayDisposition", "ProjectionReplayError", "ProjectionReplayEvent", "ProjectionReplayResult", "ProjectionReplayState", "apply_projection_replay"]
