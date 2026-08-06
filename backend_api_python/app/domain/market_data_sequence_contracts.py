"""Deterministic in-memory market-data sequence reducer.

The reducer is a pure boundary for stream consumers and dataset builders.  It
does not reconnect, fetch missing events, persist state, or infer a value for a
gap.  A caller must explicitly resolve a gap before accepting later facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .market_data_quality_contracts import MarketDataEventFact, quality_fingerprint


MARKET_DATA_SEQUENCE_CONTRACT_VERSION = "market-data-sequence-v1"


class MarketDataSequenceError(ValueError):
    """Invalid stream state or event identity."""


class SequenceDisposition(str, Enum):
    APPENDED = "APPENDED"
    REPLAYED = "REPLAYED"
    GAP = "GAP"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class MarketDataGapRecoveryPlan:
    """A bounded request for the facts missing before a stream event.

    A gap is not repaired by advancing ``next_sequence`` or by inventing a
    value.  The caller must use this plan to obtain the missing facts from an
    authoritative REST snapshot/backfill, then apply them in order.  Keeping
    the scope and trigger sequence in the immutable plan prevents a response
    for another instrument or stream from being accidentally accepted.
    """

    source: str
    instrument_id: str
    dataset_snapshot_id: str
    rule_version: str
    missing_start: int
    missing_end: int
    trigger_sequence: int
    state_fingerprint: str
    recovery_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("source", "instrument_id", "dataset_snapshot_id", "rule_version", "state_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
                raise MarketDataSequenceError(f"{name} must be canonical ASCII text")
        for name in ("missing_start", "missing_end", "trigger_sequence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MarketDataSequenceError(f"{name} must be a non-negative integer")
        if self.missing_end < self.missing_start:
            raise MarketDataSequenceError("gap recovery range must not be empty")
        if self.trigger_sequence <= self.missing_end:
            raise MarketDataSequenceError("trigger_sequence must follow the missing range")
        material = {
            "version": MARKET_DATA_SEQUENCE_CONTRACT_VERSION,
            "kind": "gap_recovery",
            "source": self.source,
            "instrument_id": self.instrument_id,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "rule_version": self.rule_version,
            "missing_start": self.missing_start,
            "missing_end": self.missing_end,
            "trigger_sequence": self.trigger_sequence,
            "state_fingerprint": self.state_fingerprint,
        }
        object.__setattr__(self, "recovery_fingerprint", quality_fingerprint(material))

    @property
    def missing_count(self) -> int:
        return self.missing_end - self.missing_start + 1


@dataclass(frozen=True, slots=True)
class MarketDataSequenceState:
    source: str
    instrument_id: str
    dataset_snapshot_id: str
    rule_version: str
    next_sequence: int = 0
    accepted_event_ids: tuple[str, ...] = ()
    state_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("source", "instrument_id", "dataset_snapshot_id", "rule_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
                raise MarketDataSequenceError(f"{name} must be canonical ASCII text")
        if isinstance(self.next_sequence, bool) or not isinstance(self.next_sequence, int) or self.next_sequence < 0:
            raise MarketDataSequenceError("next_sequence must be non-negative")
        ids = tuple(self.accepted_event_ids)
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
            raise MarketDataSequenceError("accepted_event_ids must be unique canonical text")
        object.__setattr__(self, "accepted_event_ids", ids)
        object.__setattr__(self, "state_fingerprint", quality_fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, object]:
        return {
            "version": MARKET_DATA_SEQUENCE_CONTRACT_VERSION,
            "source": self.source,
            "instrument_id": self.instrument_id,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "rule_version": self.rule_version,
            "next_sequence": self.next_sequence,
            "accepted_event_ids": list(self.accepted_event_ids),
        }


@dataclass(frozen=True, slots=True)
class MarketDataSequenceResult:
    disposition: SequenceDisposition
    state: MarketDataSequenceState
    event: MarketDataEventFact
    reason: str


def apply_market_data_event(state: MarketDataSequenceState, event: MarketDataEventFact) -> MarketDataSequenceResult:
    """Apply one event only when it is the exact next sequence value."""

    if not isinstance(state, MarketDataSequenceState) or not isinstance(event, MarketDataEventFact):
        raise MarketDataSequenceError("typed state and event are required")
    if (event.source, event.instrument_id, event.dataset_snapshot_id, event.rule_version) != (state.source, state.instrument_id, state.dataset_snapshot_id, state.rule_version):
        raise MarketDataSequenceError("event scope does not match sequence state")
    if event.event_id in state.accepted_event_ids:
        if event.sequence < state.next_sequence:
            return MarketDataSequenceResult(SequenceDisposition.REPLAYED, state, event, "exact_event_replay")
        raise MarketDataSequenceError("event identity appears at an invalid sequence")
    if event.sequence > state.next_sequence:
        return MarketDataSequenceResult(SequenceDisposition.GAP, state, event, "sequence_gap_requires_explicit_recovery")
    if event.sequence < state.next_sequence:
        return MarketDataSequenceResult(SequenceDisposition.CONFLICT, state, event, "out_of_order_event")
    next_state = MarketDataSequenceState(
        state.source,
        state.instrument_id,
        state.dataset_snapshot_id,
        state.rule_version,
        state.next_sequence + 1,
        (*state.accepted_event_ids, event.event_id),
    )
    return MarketDataSequenceResult(SequenceDisposition.APPENDED, next_state, event, "sequence_contiguous")


def plan_market_data_gap_recovery(
    state: MarketDataSequenceState,
    event: MarketDataEventFact,
    *,
    max_missing_events: int = 1000,
) -> MarketDataGapRecoveryPlan:
    """Create a bounded, scoped plan for a detected stream gap.

    The function intentionally accepts only a typed state and event.  It does
    not perform I/O and cannot mark the gap as recovered; a caller must fetch
    and validate every sequence in the returned inclusive range before
    retrying the trigger event.
    """

    if not isinstance(state, MarketDataSequenceState) or not isinstance(event, MarketDataEventFact):
        raise MarketDataSequenceError("typed state and event are required")
    if isinstance(max_missing_events, bool) or not isinstance(max_missing_events, int) or max_missing_events <= 0:
        raise MarketDataSequenceError("max_missing_events must be positive")
    scope = (event.source, event.instrument_id, event.dataset_snapshot_id, event.rule_version)
    expected_scope = (state.source, state.instrument_id, state.dataset_snapshot_id, state.rule_version)
    if scope != expected_scope:
        raise MarketDataSequenceError("event scope does not match sequence state")
    if event.sequence <= state.next_sequence:
        raise MarketDataSequenceError("event does not represent a forward gap")
    missing_start = state.next_sequence
    missing_end = event.sequence - 1
    if missing_end - missing_start + 1 > max_missing_events:
        raise MarketDataSequenceError("sequence gap exceeds recovery bound")
    return MarketDataGapRecoveryPlan(
        source=state.source,
        instrument_id=state.instrument_id,
        dataset_snapshot_id=state.dataset_snapshot_id,
        rule_version=state.rule_version,
        missing_start=missing_start,
        missing_end=missing_end,
        trigger_sequence=event.sequence,
        state_fingerprint=state.state_fingerprint,
    )


def apply_market_data_gap_recovery(
    state: MarketDataSequenceState,
    recovered_events: tuple[MarketDataEventFact, ...],
    trigger_event: MarketDataEventFact,
    *,
    max_missing_events: int = 1000,
) -> MarketDataSequenceResult:
    """Apply a complete backfill and then the event that exposed its gap.

    The caller supplies the backfill explicitly; this function performs no
    network access and refuses partial, duplicated, out-of-scope, or
    out-of-order recovery data.  The returned result is the application of
    ``trigger_event`` after every missing sequence has been accepted.
    """

    plan = plan_market_data_gap_recovery(state, trigger_event, max_missing_events=max_missing_events)
    if not isinstance(recovered_events, tuple) or len(recovered_events) != plan.missing_count:
        raise MarketDataSequenceError("recovery must provide every missing event")
    current = state
    for expected_sequence, recovered in zip(range(plan.missing_start, plan.missing_end + 1), recovered_events):
        if not isinstance(recovered, MarketDataEventFact) or recovered.sequence != expected_sequence:
            raise MarketDataSequenceError("recovery events must be contiguous and ordered")
        result = apply_market_data_event(current, recovered)
        if result.disposition is not SequenceDisposition.APPENDED:
            raise MarketDataSequenceError("recovery event was not appended")
        current = result.state
    result = apply_market_data_event(current, trigger_event)
    if result.disposition is not SequenceDisposition.APPENDED:
        raise MarketDataSequenceError("trigger event was not appended after recovery")
    return result


__all__ = [
    "MARKET_DATA_SEQUENCE_CONTRACT_VERSION",
    "MarketDataSequenceError",
    "MarketDataGapRecoveryPlan",
    "MarketDataSequenceResult",
    "MarketDataSequenceState",
    "SequenceDisposition",
    "apply_market_data_event",
    "apply_market_data_gap_recovery",
    "plan_market_data_gap_recovery",
]
