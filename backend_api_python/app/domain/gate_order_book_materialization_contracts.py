"""Pure, fail-closed materialization of verified Gate order-book frames.

This module operates entirely on immutable REST snapshots and typed WebSocket
frames.  It has no socket, HTTP, database, credential, scheduling, or order
submission code.  A caller may only expose its result after sequence and
market-scope checks succeed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import hashlib
import json

from .gate_market_read_contracts import GateMarketContractError, GateOrderBookLevel, GateOrderBookSnapshot
from .gate_order_book_stream_contracts import (
    GateOrderBookDeltaLevel,
    GateOrderBookStreamDisposition,
    GateOrderBookStreamError,
    GateOrderBookStreamFrame,
    GateOrderBookStreamResult,
    GateOrderBookStreamState,
    GateOrderBookStreamSubscription,
    apply_gate_order_book_stream_frame,
    gate_order_book_stream_state_from_snapshot,
)


GATE_ORDER_BOOK_MATERIALIZATION_CONTRACT_VERSION = "gate-order-book-materialization-v1"


class GateOrderBookMaterializationError(GateOrderBookStreamError):
    """A typed stream cannot safely become a materialized local book."""


class GateOrderBookMaterializationDisposition(str, Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"
    RESEED_REQUIRED = "RESEED_REQUIRED"
    CONFLICT = "CONFLICT"


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _levels_from_mapping(values: dict[Decimal, Decimal], *, reverse: bool) -> tuple[GateOrderBookLevel, ...]:
    return tuple(
        GateOrderBookLevel(price=price, quantity=quantity)
        for price, quantity in sorted(values.items(), key=lambda item: item[0], reverse=reverse)
    )


def _apply_levels(values: dict[Decimal, Decimal], updates: tuple[GateOrderBookDeltaLevel, ...]) -> dict[Decimal, Decimal]:
    result = dict(values)
    for update in updates:
        if update.quantity == 0:
            result.pop(update.price, None)
        else:
            result[update.price] = update.quantity
    return result


@dataclass(frozen=True, slots=True)
class GateOrderBookMaterializedState:
    """One verified local depth image and its stream checkpoint."""

    subscription: GateOrderBookStreamSubscription
    stream_state: GateOrderBookStreamState
    snapshot: GateOrderBookSnapshot
    materialization_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subscription, GateOrderBookStreamSubscription)
            or not isinstance(self.stream_state, GateOrderBookStreamState)
            or not isinstance(self.snapshot, GateOrderBookSnapshot)
        ):
            raise GateOrderBookMaterializationError("materialized state requires typed subscription, stream state, and snapshot")
        if self.snapshot.depth_limit is None:
            raise GateOrderBookMaterializationError("materialized snapshot must retain depth_limit")
        if (
            self.stream_state.market_type is not self.subscription.market_type
            or self.stream_state.instrument_id != self.subscription.instrument_id
            or self.stream_state.snapshot_id != self.subscription.snapshot_id
            or self.stream_state.rule_version != self.subscription.rule_version
            or self.stream_state.depth_limit != self.subscription.depth_limit
            or self.stream_state.update_interval != self.subscription.update_interval
            or self.snapshot.market_type is not self.subscription.market_type
            or self.snapshot.instrument_id != self.subscription.instrument_id
            or self.snapshot.snapshot_id != self.subscription.snapshot_id
            or self.snapshot.rule_version != self.subscription.rule_version
            or self.snapshot.depth_limit != self.subscription.depth_limit
            or self.snapshot.sequence != self.stream_state.next_update_id - 1
        ):
            raise GateOrderBookMaterializationError("materialized state scope or sequence is inconsistent")
        if len(self.snapshot.bids) > self.subscription.depth_limit or len(self.snapshot.asks) > self.subscription.depth_limit:
            raise GateOrderBookMaterializationError("materialized depth exceeds the negotiated subscription limit")
        object.__setattr__(self, "materialization_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_MATERIALIZATION_CONTRACT_VERSION,
            "subscription_fingerprint": self.subscription.subscription_fingerprint,
            "stream_state_fingerprint": self.stream_state.state_fingerprint,
            "snapshot_sequence": self.snapshot.sequence,
            "snapshot_evidence_hash": self.snapshot.evidence_hash,
            "bids": [[_decimal_text(level.price), _decimal_text(level.quantity)] for level in self.snapshot.bids],
            "asks": [[_decimal_text(level.price), _decimal_text(level.quantity)] for level in self.snapshot.asks],
        }))


@dataclass(frozen=True, slots=True)
class GateOrderBookMaterializationResult:
    disposition: GateOrderBookMaterializationDisposition
    state: GateOrderBookMaterializedState
    stream_result: GateOrderBookStreamResult
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, GateOrderBookMaterializationDisposition):
            raise GateOrderBookMaterializationError("materialization disposition must be typed")
        if not isinstance(self.state, GateOrderBookMaterializedState) or not isinstance(self.stream_result, GateOrderBookStreamResult):
            raise GateOrderBookMaterializationError("materialization result requires typed state and stream result")
        if not isinstance(self.reason, str) or not self.reason or not self.reason.isascii():
            raise GateOrderBookMaterializationError("reason must be canonical ASCII text")


def gate_order_book_materialized_state_from_snapshot(
    snapshot: GateOrderBookSnapshot,
    subscription: GateOrderBookStreamSubscription,
) -> GateOrderBookMaterializedState:
    """Build local depth only from a REST snapshot with retained request limit."""

    stream_state = gate_order_book_stream_state_from_snapshot(snapshot, subscription)
    return GateOrderBookMaterializedState(subscription=subscription, stream_state=stream_state, snapshot=snapshot)


def _mapped_disposition(disposition: GateOrderBookStreamDisposition) -> GateOrderBookMaterializationDisposition:
    return GateOrderBookMaterializationDisposition(disposition.value)


def apply_gate_order_book_materialized_frame(
    state: GateOrderBookMaterializedState,
    frame: GateOrderBookStreamFrame,
) -> GateOrderBookMaterializationResult:
    """Apply a verified frame, or preserve the prior book and request reseed.

    A full frame replaces all price levels.  An incremental frame applies only
    its absolute level changes.  Any result that would leave an empty, crossed,
    over-depth, or otherwise invalid book is fail-closed as ``RESEED_REQUIRED``
    without mutating the returned materialized state.
    """

    if not isinstance(state, GateOrderBookMaterializedState) or not isinstance(frame, GateOrderBookStreamFrame):
        raise GateOrderBookMaterializationError("typed materialized state and frame are required")
    stream_result = apply_gate_order_book_stream_frame(state.stream_state, frame)
    disposition = _mapped_disposition(stream_result.disposition)
    if disposition is not GateOrderBookMaterializationDisposition.APPLIED:
        return GateOrderBookMaterializationResult(disposition, state, stream_result, stream_result.reason)
    bids = {} if frame.is_full_snapshot else {level.price: level.quantity for level in state.snapshot.bids}
    asks = {} if frame.is_full_snapshot else {level.price: level.quantity for level in state.snapshot.asks}
    bids = _apply_levels(bids, frame.delta.bids)
    asks = _apply_levels(asks, frame.delta.asks)
    if not bids or not asks or len(bids) > state.subscription.depth_limit or len(asks) > state.subscription.depth_limit:
        return GateOrderBookMaterializationResult(
            GateOrderBookMaterializationDisposition.RESEED_REQUIRED,
            state,
            stream_result,
            "materialized_depth_is_empty_or_exceeds_limit",
        )
    try:
        next_snapshot = GateOrderBookSnapshot(
            market_type=state.subscription.market_type,
            instrument_id=state.subscription.instrument_id,
            bids=_levels_from_mapping(bids, reverse=True),
            asks=_levels_from_mapping(asks, reverse=False),
            occurred_at=frame.delta.occurred_at,
            observed_at=frame.delta.observed_at,
            sequence=frame.delta.last_update_id,
            source_event_id=frame.delta.source_event_id,
            snapshot_id=state.subscription.snapshot_id,
            rule_version=state.subscription.rule_version,
            evidence_hash=frame.delta.payload_fingerprint,
            depth_limit=state.subscription.depth_limit,
        )
    except GateMarketContractError:
        return GateOrderBookMaterializationResult(
            GateOrderBookMaterializationDisposition.RESEED_REQUIRED,
            state,
            stream_result,
            "materialized_depth_is_not_a_valid_order_book",
        )
    next_state = GateOrderBookMaterializedState(
        subscription=state.subscription,
        stream_state=stream_result.state,
        snapshot=next_snapshot,
    )
    return GateOrderBookMaterializationResult(
        GateOrderBookMaterializationDisposition.APPLIED,
        next_state,
        stream_result,
        stream_result.reason,
    )


__all__ = [
    "GATE_ORDER_BOOK_MATERIALIZATION_CONTRACT_VERSION",
    "GateOrderBookMaterializationDisposition",
    "GateOrderBookMaterializationError",
    "GateOrderBookMaterializationResult",
    "GateOrderBookMaterializedState",
    "apply_gate_order_book_materialized_frame",
    "gate_order_book_materialized_state_from_snapshot",
]
