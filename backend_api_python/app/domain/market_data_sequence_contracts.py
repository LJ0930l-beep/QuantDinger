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


__all__ = [
    "MARKET_DATA_SEQUENCE_CONTRACT_VERSION",
    "MarketDataSequenceError",
    "MarketDataSequenceResult",
    "MarketDataSequenceState",
    "SequenceDisposition",
    "apply_market_data_event",
]
