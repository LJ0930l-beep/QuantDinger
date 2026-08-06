"""Deterministic Gate order-book stream sequencing contracts.

This module models the documented REST-snapshot plus WebSocket-increment
boundary for Gate public order books.  It deliberately does not open a socket,
perform HTTP I/O, hold credentials, or submit orders.  A transport adapter can
only apply an update after it has supplied typed, immutable facts here.

Gate's ``order_book_update`` notifications identify an inclusive update range
with ``U`` and ``u``.  A REST snapshot establishes the starting ID.  The first
stream update must cover ``snapshot_id + 1``; every later update must cover the
next expected ID.  Missing or unverifiable ranges fail closed and produce a
reseed plan instead of silently advancing local market state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
from typing import Any

from .gate_market_read_contracts import GateOrderBookSnapshot
from .multi_asset_capability_contracts import AssetMarketType


GATE_ORDER_BOOK_STREAM_CONTRACT_VERSION = "gate-order-book-stream-v1"


class GateOrderBookStreamError(ValueError):
    """A Gate order-book stream fact is malformed or unsafe."""


class GateOrderBookStreamScopeConflict(GateOrderBookStreamError):
    """A delta belongs to a different immutable market evidence scope."""


class GateOrderBookStreamDisposition(str, Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"
    RESEED_REQUIRED = "RESEED_REQUIRED"
    CONFLICT = "CONFLICT"


def _text(value: object, field_name: str, *, fingerprint: bool = False) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(char.isspace() for char in value):
        raise GateOrderBookStreamError(f"{field_name} must be canonical ASCII text")
    if fingerprint and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
        raise GateOrderBookStreamError(f"{field_name} must be a lowercase SHA-256 fingerprint")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateOrderBookStreamError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _update_id(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GateOrderBookStreamError(f"{field_name} must be a non-negative integer")
    return value


def _decimal(value: object, field_name: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise GateOrderBookStreamError(f"{field_name} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateOrderBookStreamError(f"{field_name} must be Decimal-compatible") from exc
    if not result.is_finite() or (positive and result <= 0) or (not positive and result < 0):
        raise GateOrderBookStreamError(f"{field_name} has invalid numeric bounds")
    return result


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _depth_limit(market_type: AssetMarketType, value: object, field_name: str = "depth_limit") -> int:
    if not isinstance(market_type, AssetMarketType) or market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
        raise GateOrderBookStreamError("depth_limit is only supported for typed spot or perpetual markets")
    if isinstance(value, bool) or not isinstance(value, int):
        raise GateOrderBookStreamError(f"{field_name} must be an integer")
    allowed = {20, 100} if market_type is AssetMarketType.SPOT else {20, 50, 100}
    if value not in allowed:
        raise GateOrderBookStreamError(f"{field_name} is not supported for the market type")
    return value


def _update_interval(market_type: AssetMarketType, depth_limit: int, value: object) -> str:
    interval = _text(value, "update_interval")
    if interval not in {"20ms", "100ms"}:
        raise GateOrderBookStreamError("update_interval must be 20ms or 100ms")
    # Gate's spot channel maps 20ms to level 20 and 100ms to level 100.
    # Futures allows 20/50/100 at 100ms, but only level 20 at 20ms.
    if interval == "20ms" and depth_limit != 20:
        raise GateOrderBookStreamError("20ms updates require depth_limit 20")
    if market_type is AssetMarketType.SPOT and ((interval == "20ms" and depth_limit != 20) or (interval == "100ms" and depth_limit != 100)):
        raise GateOrderBookStreamError("spot update interval and depth_limit must match Gate's documented pair")
    return interval


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamSubscription:
    """Immutable public-market depth subscription facts.

    ``depth_limit`` represents both the REST snapshot limit and the WebSocket
    level.  Their equality is required before a stream is allowed to anchor a
    local book, rather than inferred from the number of available levels.
    """

    market_type: AssetMarketType
    instrument_id: str
    snapshot_id: str
    rule_version: str
    depth_limit: int
    update_interval: str
    subscription_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateOrderBookStreamError("market_type must be typed spot or perpetual")
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "snapshot_id", _text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "rule_version", _text(self.rule_version, "rule_version"))
        depth = _depth_limit(self.market_type, self.depth_limit)
        interval = _update_interval(self.market_type, depth, self.update_interval)
        object.__setattr__(self, "depth_limit", depth)
        object.__setattr__(self, "update_interval", interval)
        object.__setattr__(self, "subscription_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_STREAM_CONTRACT_VERSION,
            "market_type": self.market_type.value,
            "instrument_id": self.instrument_id,
            "snapshot_id": self.snapshot_id,
            "rule_version": self.rule_version,
            "depth_limit": depth,
            "update_interval": interval,
        }))


@dataclass(frozen=True, slots=True)
class GateOrderBookDeltaLevel:
    """One absolute Gate book level; zero quantity deletes the price level."""

    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _decimal(self.price, "price", positive=True))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity"))


@dataclass(frozen=True, slots=True)
class GateOrderBookDelta:
    """A typed Gate ``U``/``u`` order-book delta range.

    Both level tuples may be empty.  Gate documents futures updates that carry
    no price-level change while still advancing the authoritative update range;
    treating those updates as malformed would manufacture a false stream gap.
    The range, source identity, and payload fingerprint remain mandatory.
    """

    market_type: AssetMarketType
    instrument_id: str
    first_update_id: int
    last_update_id: int
    bids: tuple[GateOrderBookDeltaLevel, ...]
    asks: tuple[GateOrderBookDeltaLevel, ...]
    occurred_at: datetime
    observed_at: datetime
    source_event_id: str
    snapshot_id: str
    rule_version: str
    payload_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateOrderBookStreamError("market_type must be typed spot or perpetual")
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id"))
        first = _update_id(self.first_update_id, "first_update_id")
        last = _update_id(self.last_update_id, "last_update_id")
        if last < first:
            raise GateOrderBookStreamError("last_update_id cannot precede first_update_id")
        if not isinstance(self.bids, tuple) or not isinstance(self.asks, tuple) or any(not isinstance(item, GateOrderBookDeltaLevel) for item in (*self.bids, *self.asks)):
            raise GateOrderBookStreamError("bids and asks must be explicit typed tuples")
        occurred = _utc(self.occurred_at, "occurred_at")
        observed = _utc(self.observed_at, "observed_at")
        if observed < occurred:
            raise GateOrderBookStreamError("observed_at cannot precede occurred_at")
        object.__setattr__(self, "first_update_id", first)
        object.__setattr__(self, "last_update_id", last)
        object.__setattr__(self, "occurred_at", occurred)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "source_event_id", _text(self.source_event_id, "source_event_id"))
        object.__setattr__(self, "snapshot_id", _text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "rule_version", _text(self.rule_version, "rule_version"))
        object.__setattr__(self, "payload_fingerprint", _text(self.payload_fingerprint, "payload_fingerprint", fingerprint=True))

    @property
    def identity(self) -> tuple[str, int, int, str]:
        return (self.source_event_id, self.first_update_id, self.last_update_id, self.payload_fingerprint)


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamFrame:
    """A parsed authoritative WebSocket frame, distinct from a delta range.

    ``is_full_snapshot`` is deliberately retained outside ``GateOrderBookDelta``:
    a future local-book reducer must replace its depth for a full frame and
    must never merge it as if it were a normal incremental update.
    """

    subscription: GateOrderBookStreamSubscription
    delta: GateOrderBookDelta
    channel: str
    is_full_snapshot: bool
    frame_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.subscription, GateOrderBookStreamSubscription) or not isinstance(self.delta, GateOrderBookDelta):
            raise GateOrderBookStreamError("frame requires typed subscription and delta")
        if not isinstance(self.is_full_snapshot, bool):
            raise GateOrderBookStreamError("is_full_snapshot must be bool")
        expected_channel = "spot.order_book_update" if self.subscription.market_type is AssetMarketType.SPOT else "futures.order_book_update"
        if _text(self.channel, "channel") != expected_channel:
            raise GateOrderBookStreamScopeConflict("stream frame channel does not match subscription market type")
        if (
            self.delta.market_type is not self.subscription.market_type
            or self.delta.instrument_id != self.subscription.instrument_id
            or self.delta.snapshot_id != self.subscription.snapshot_id
            or self.delta.rule_version != self.subscription.rule_version
        ):
            raise GateOrderBookStreamScopeConflict("stream frame delta does not match subscription scope")
        object.__setattr__(self, "frame_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_STREAM_CONTRACT_VERSION,
            "subscription_fingerprint": self.subscription.subscription_fingerprint,
            "channel": self.channel,
            "is_full_snapshot": self.is_full_snapshot,
            "delta_payload_fingerprint": self.delta.payload_fingerprint,
        }))


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamState:
    """Checkpoint after a REST snapshot and zero or more accepted deltas."""

    market_type: AssetMarketType
    instrument_id: str
    snapshot_id: str
    rule_version: str
    depth_limit: int
    update_interval: str
    base_update_id: int
    last_applied_update_id: int | None = None
    last_first_update_id: int | None = None
    last_source_event_id: str | None = None
    last_payload_fingerprint: str | None = None
    state_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateOrderBookStreamError("market_type must be typed spot or perpetual")
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "snapshot_id", _text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "rule_version", _text(self.rule_version, "rule_version"))
        depth = _depth_limit(self.market_type, self.depth_limit)
        interval = _update_interval(self.market_type, depth, self.update_interval)
        object.__setattr__(self, "depth_limit", depth)
        object.__setattr__(self, "update_interval", interval)
        base = _update_id(self.base_update_id, "base_update_id")
        object.__setattr__(self, "base_update_id", base)
        values = (
            self.last_applied_update_id,
            self.last_first_update_id,
            self.last_source_event_id,
            self.last_payload_fingerprint,
        )
        if all(value is None for value in values):
            last_applied = base
        elif any(value is None for value in values):
            raise GateOrderBookStreamError("last applied delta checkpoint must be complete or absent")
        else:
            last_applied = _update_id(self.last_applied_update_id, "last_applied_update_id")
            first = _update_id(self.last_first_update_id, "last_first_update_id")
            if last_applied < first or last_applied <= base:
                raise GateOrderBookStreamError("last applied delta checkpoint is inconsistent")
            object.__setattr__(self, "last_applied_update_id", last_applied)
            object.__setattr__(self, "last_first_update_id", first)
            object.__setattr__(self, "last_source_event_id", _text(self.last_source_event_id, "last_source_event_id"))
            object.__setattr__(self, "last_payload_fingerprint", _text(self.last_payload_fingerprint, "last_payload_fingerprint", fingerprint=True))
        object.__setattr__(self, "state_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_STREAM_CONTRACT_VERSION,
            "market_type": self.market_type.value,
            "instrument_id": self.instrument_id,
            "snapshot_id": self.snapshot_id,
            "rule_version": self.rule_version,
            "depth_limit": depth,
            "update_interval": interval,
            "base_update_id": base,
            "last_applied_update_id": last_applied,
            "last_first_update_id": self.last_first_update_id,
            "last_source_event_id": self.last_source_event_id,
            "last_payload_fingerprint": self.last_payload_fingerprint,
        }))

    @property
    def next_update_id(self) -> int:
        return (self.last_applied_update_id if self.last_applied_update_id is not None else self.base_update_id) + 1


@dataclass(frozen=True, slots=True)
class GateOrderBookStreamResult:
    disposition: GateOrderBookStreamDisposition
    state: GateOrderBookStreamState
    delta: GateOrderBookDelta
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, GateOrderBookStreamDisposition):
            raise GateOrderBookStreamError("disposition must be typed")
        if not isinstance(self.state, GateOrderBookStreamState) or not isinstance(self.delta, GateOrderBookDelta):
            raise GateOrderBookStreamError("result requires typed state and delta")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))


@dataclass(frozen=True, slots=True)
class GateOrderBookReseedPlan:
    """An immutable instruction to recover a stale local book from REST."""

    market_type: AssetMarketType
    instrument_id: str
    snapshot_id: str
    rule_version: str
    depth_limit: int
    update_interval: str
    last_verified_update_id: int
    expected_next_update_id: int
    trigger_first_update_id: int
    trigger_last_update_id: int
    trigger_fingerprint: str
    plan_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, AssetMarketType):
            raise GateOrderBookStreamError("market_type must be typed")
        for name in ("instrument_id", "snapshot_id", "rule_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        depth = _depth_limit(self.market_type, self.depth_limit)
        interval = _update_interval(self.market_type, depth, self.update_interval)
        last = _update_id(self.last_verified_update_id, "last_verified_update_id")
        expected = _update_id(self.expected_next_update_id, "expected_next_update_id")
        first = _update_id(self.trigger_first_update_id, "trigger_first_update_id")
        trigger_last = _update_id(self.trigger_last_update_id, "trigger_last_update_id")
        if expected != last + 1 or trigger_last < first:
            raise GateOrderBookStreamError("reseed plan update ranges are inconsistent")
        object.__setattr__(self, "last_verified_update_id", last)
        object.__setattr__(self, "expected_next_update_id", expected)
        object.__setattr__(self, "trigger_first_update_id", first)
        object.__setattr__(self, "trigger_last_update_id", trigger_last)
        object.__setattr__(self, "depth_limit", depth)
        object.__setattr__(self, "update_interval", interval)
        object.__setattr__(self, "trigger_fingerprint", _text(self.trigger_fingerprint, "trigger_fingerprint", fingerprint=True))
        object.__setattr__(self, "plan_fingerprint", _fingerprint({
            "version": GATE_ORDER_BOOK_STREAM_CONTRACT_VERSION,
            "market_type": self.market_type.value,
            "instrument_id": self.instrument_id,
            "snapshot_id": self.snapshot_id,
            "rule_version": self.rule_version,
            "depth_limit": depth,
            "update_interval": interval,
            "last_verified_update_id": last,
            "expected_next_update_id": expected,
            "trigger_first_update_id": first,
            "trigger_last_update_id": trigger_last,
            "trigger_fingerprint": self.trigger_fingerprint,
        }))


def gate_order_book_stream_state_from_snapshot(
    snapshot: GateOrderBookSnapshot,
    subscription: GateOrderBookStreamSubscription,
) -> GateOrderBookStreamState:
    """Anchor local stream sequencing at a typed Gate REST order-book snapshot."""

    if not isinstance(snapshot, GateOrderBookSnapshot) or not isinstance(subscription, GateOrderBookStreamSubscription):
        raise GateOrderBookStreamError("typed Gate order book snapshot and subscription are required")
    if snapshot.depth_limit is None:
        raise GateOrderBookStreamError("REST snapshot must retain depth_limit before it can anchor a stream")
    if (
        snapshot.market_type is not subscription.market_type
        or snapshot.instrument_id != subscription.instrument_id
        or snapshot.snapshot_id != subscription.snapshot_id
        or snapshot.rule_version != subscription.rule_version
        or snapshot.depth_limit != subscription.depth_limit
    ):
        raise GateOrderBookStreamScopeConflict("REST snapshot does not prove the stream subscription scope")
    return GateOrderBookStreamState(
        market_type=snapshot.market_type,
        instrument_id=snapshot.instrument_id,
        snapshot_id=snapshot.snapshot_id,
        rule_version=snapshot.rule_version,
        depth_limit=subscription.depth_limit,
        update_interval=subscription.update_interval,
        base_update_id=snapshot.sequence,
    )


def _require_scope(state: GateOrderBookStreamState, delta: GateOrderBookDelta) -> None:
    if (
        state.market_type is not delta.market_type
        or state.instrument_id != delta.instrument_id
        or state.snapshot_id != delta.snapshot_id
        or state.rule_version != delta.rule_version
    ):
        raise GateOrderBookStreamScopeConflict("order book delta scope does not match stream state")


def apply_gate_order_book_delta(state: GateOrderBookStreamState, delta: GateOrderBookDelta) -> GateOrderBookStreamResult:
    """Advance one stream checkpoint only when ``U``/``u`` covers the next ID.

    The update values themselves are deliberately not merged into a mutable
    book here.  They remain immutable evidence.  A later consumer can apply
    their absolute price-level updates only after this function returns
    ``APPLIED``.
    """

    if not isinstance(state, GateOrderBookStreamState) or not isinstance(delta, GateOrderBookDelta):
        raise GateOrderBookStreamError("typed stream state and delta are required")
    _require_scope(state, delta)
    expected = state.next_update_id
    exact_last_replay = (
        state.last_applied_update_id == delta.last_update_id
        and state.last_first_update_id == delta.first_update_id
        and state.last_source_event_id == delta.source_event_id
        and state.last_payload_fingerprint == delta.payload_fingerprint
    )
    if exact_last_replay:
        return GateOrderBookStreamResult(GateOrderBookStreamDisposition.REPLAYED, state, delta, "exact_last_delta_replay")
    if (
        state.last_applied_update_id == delta.last_update_id
        or state.last_source_event_id == delta.source_event_id
    ):
        return GateOrderBookStreamResult(GateOrderBookStreamDisposition.CONFLICT, state, delta, "last_delta_identity_conflict")
    if delta.last_update_id < expected:
        return GateOrderBookStreamResult(GateOrderBookStreamDisposition.RESEED_REQUIRED, state, delta, "stale_delta_not_verifiable")
    if delta.first_update_id > expected:
        return GateOrderBookStreamResult(GateOrderBookStreamDisposition.RESEED_REQUIRED, state, delta, "stream_gap_requires_rest_reseed")
    next_state = GateOrderBookStreamState(
        market_type=state.market_type,
        instrument_id=state.instrument_id,
        snapshot_id=state.snapshot_id,
        rule_version=state.rule_version,
        depth_limit=state.depth_limit,
        update_interval=state.update_interval,
        base_update_id=state.base_update_id,
        last_applied_update_id=delta.last_update_id,
        last_first_update_id=delta.first_update_id,
        last_source_event_id=delta.source_event_id,
        last_payload_fingerprint=delta.payload_fingerprint,
    )
    return GateOrderBookStreamResult(GateOrderBookStreamDisposition.APPLIED, next_state, delta, "delta_covers_expected_update")


def apply_gate_order_book_stream_frame(
    state: GateOrderBookStreamState,
    frame: GateOrderBookStreamFrame,
) -> GateOrderBookStreamResult:
    """Apply a parsed frame only when the retained subscription facts match."""

    if not isinstance(state, GateOrderBookStreamState) or not isinstance(frame, GateOrderBookStreamFrame):
        raise GateOrderBookStreamError("typed stream state and frame are required")
    subscription = frame.subscription
    if (
        state.market_type is not subscription.market_type
        or state.instrument_id != subscription.instrument_id
        or state.snapshot_id != subscription.snapshot_id
        or state.rule_version != subscription.rule_version
        or state.depth_limit != subscription.depth_limit
        or state.update_interval != subscription.update_interval
    ):
        raise GateOrderBookStreamScopeConflict("stream frame subscription does not match state")
    result = apply_gate_order_book_delta(state, frame.delta)
    if (
        result.disposition is GateOrderBookStreamDisposition.RESEED_REQUIRED
        and frame.is_full_snapshot
        and frame.delta.last_update_id >= state.next_update_id
    ):
        # Gate documents ``full=true`` as a complete subscribed-depth image.
        # It is a legitimate recovery anchor when a normal incremental range
        # cannot cover the expected ID.  Do not accept an older full frame: it
        # would roll a verified stream checkpoint backwards.
        reanchored = GateOrderBookStreamState(
            market_type=state.market_type,
            instrument_id=state.instrument_id,
            snapshot_id=state.snapshot_id,
            rule_version=state.rule_version,
            depth_limit=state.depth_limit,
            update_interval=state.update_interval,
            base_update_id=state.base_update_id,
            last_applied_update_id=frame.delta.last_update_id,
            last_first_update_id=frame.delta.first_update_id,
            last_source_event_id=frame.delta.source_event_id,
            last_payload_fingerprint=frame.delta.payload_fingerprint,
        )
        return GateOrderBookStreamResult(
            GateOrderBookStreamDisposition.APPLIED,
            reanchored,
            frame.delta,
            "full_depth_snapshot_reanchors_stream",
        )
    return result


def plan_gate_order_book_reseed(state: GateOrderBookStreamState, trigger: GateOrderBookDelta) -> GateOrderBookReseedPlan:
    """Create a deterministic REST-reseed plan after a non-applied delta."""

    if not isinstance(state, GateOrderBookStreamState) or not isinstance(trigger, GateOrderBookDelta):
        raise GateOrderBookStreamError("typed stream state and trigger delta are required")
    _require_scope(state, trigger)
    last = state.next_update_id - 1
    return GateOrderBookReseedPlan(
        market_type=state.market_type,
        instrument_id=state.instrument_id,
        snapshot_id=state.snapshot_id,
        rule_version=state.rule_version,
        depth_limit=state.depth_limit,
        update_interval=state.update_interval,
        last_verified_update_id=last,
        expected_next_update_id=state.next_update_id,
        trigger_first_update_id=trigger.first_update_id,
        trigger_last_update_id=trigger.last_update_id,
        trigger_fingerprint=trigger.payload_fingerprint,
    )


__all__ = [
    "GATE_ORDER_BOOK_STREAM_CONTRACT_VERSION",
    "GateOrderBookDelta",
    "GateOrderBookDeltaLevel",
    "GateOrderBookReseedPlan",
    "GateOrderBookStreamFrame",
    "GateOrderBookStreamDisposition",
    "GateOrderBookStreamError",
    "GateOrderBookStreamResult",
    "GateOrderBookStreamScopeConflict",
    "GateOrderBookStreamState",
    "GateOrderBookStreamSubscription",
    "apply_gate_order_book_stream_frame",
    "apply_gate_order_book_delta",
    "gate_order_book_stream_state_from_snapshot",
    "plan_gate_order_book_reseed",
]
