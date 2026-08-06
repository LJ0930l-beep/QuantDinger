"""Strict, transport-free formatting for Gate order-book update frames.

Only an already-received JSON-compatible WebSocket frame may enter this
module.  It does not connect to Gate, hold credentials, persist state, or
perform order actions.  Its only responsibility is preserving official
``order_book_update`` facts before the stream sequencer decides whether the
event can advance a local checkpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

from .gate_order_book_stream_contracts import (
    GateOrderBookDelta,
    GateOrderBookDeltaLevel,
    GateOrderBookStreamError,
    GateOrderBookStreamFrame,
    GateOrderBookStreamSubscription,
)
from .multi_asset_capability_contracts import AssetMarketType


GATE_ORDER_BOOK_STREAM_PAYLOAD_CONTRACT_VERSION = "gate-order-book-stream-payload-v1"


class GateOrderBookStreamPayloadError(GateOrderBookStreamError):
    """A supplied Gate WebSocket frame cannot become authoritative evidence."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(char.isspace() for char in value):
        raise GateOrderBookStreamPayloadError(f"{field_name} must be canonical ASCII text")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateOrderBookStreamPayloadError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _integer(value: object, field_name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool):
        raise GateOrderBookStreamPayloadError(f"{field_name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value and value.isascii() and value.isdecimal() and (value == "0" or not value.startswith("0")):
        parsed = int(value)
    else:
        raise GateOrderBookStreamPayloadError(f"{field_name} must be a canonical integer")
    if parsed < 0 or (positive and parsed <= 0):
        raise GateOrderBookStreamPayloadError(f"{field_name} has invalid bounds")
    return parsed


def _millis_timestamp(value: object, field_name: str) -> datetime:
    milliseconds = _integer(value, field_name)
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _level_rows(value: object, field_name: str, market_type: AssetMarketType) -> tuple[GateOrderBookDeltaLevel, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise GateOrderBookStreamPayloadError(f"{field_name} must be an array")
    result: list[GateOrderBookDeltaLevel] = []
    for row in value:
        if market_type is AssetMarketType.SPOT:
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)) or len(row) != 2:
                raise GateOrderBookStreamPayloadError(f"{field_name} spot level must be a price/quantity pair")
            price, quantity = row
        else:
            if not isinstance(row, Mapping) or set(row) != {"p", "s"}:
                raise GateOrderBookStreamPayloadError(f"{field_name} perpetual level must contain only p and s")
            price, quantity = row["p"], row["s"]
        try:
            result.append(GateOrderBookDeltaLevel(price=price, quantity=quantity))
        except GateOrderBookStreamError as exc:
            raise GateOrderBookStreamPayloadError(f"{field_name} contains an invalid price level") from exc
    return tuple(result)


def _payload_fingerprint(
    *,
    subscription: GateOrderBookStreamSubscription,
    channel: str,
    occurred_at: datetime,
    first_update_id: int,
    last_update_id: int,
    is_full_snapshot: bool,
    bids: tuple[GateOrderBookDeltaLevel, ...],
    asks: tuple[GateOrderBookDeltaLevel, ...],
) -> str:
    material = {
        "version": GATE_ORDER_BOOK_STREAM_PAYLOAD_CONTRACT_VERSION,
        "market_type": subscription.market_type.value,
        "channel": channel,
        "instrument_id": subscription.instrument_id,
        "occurred_at": occurred_at.isoformat(),
        "first_update_id": first_update_id,
        "last_update_id": last_update_id,
        "is_full_snapshot": is_full_snapshot,
        "depth_limit": subscription.depth_limit,
        "bids": [[_canonical_decimal(level.price), _canonical_decimal(level.quantity)] for level in bids],
        "asks": [[_canonical_decimal(level.price), _canonical_decimal(level.quantity)] for level in asks],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def normalize_gate_order_book_update_frame(
    frame: Mapping[str, Any],
    *,
    subscription: GateOrderBookStreamSubscription,
    observed_at: datetime,
    source_event_prefix: str,
) -> GateOrderBookStreamFrame:
    """Format one documented Gate Spot or Futures ``order_book_update`` frame.

    ``full=true`` remains explicit in the returned envelope.  Consumers that
    eventually materialize local price levels must replace their depth for a
    full frame rather than treating it as a normal incremental merge.
    """

    if not isinstance(frame, Mapping) or not isinstance(subscription, GateOrderBookStreamSubscription):
        raise GateOrderBookStreamPayloadError("typed frame mapping and subscription are required")
    if frame.get("error") is not None:
        raise GateOrderBookStreamPayloadError("Gate WebSocket error frame cannot become order book evidence")
    expected_channel = "spot.order_book_update" if subscription.market_type is AssetMarketType.SPOT else "futures.order_book_update"
    if _text(frame.get("channel"), "channel") != expected_channel:
        raise GateOrderBookStreamPayloadError("WebSocket channel does not match subscription market type")
    if frame.get("event") != "update":
        raise GateOrderBookStreamPayloadError("only server update events can become order book evidence")
    result = frame.get("result")
    if not isinstance(result, Mapping):
        raise GateOrderBookStreamPayloadError("WebSocket update result must be an object")
    if _text(result.get("s"), "result.s") != subscription.instrument_id:
        raise GateOrderBookStreamPayloadError("WebSocket update instrument does not match subscription")
    occurred = _millis_timestamp(result.get("t"), "result.t")
    observed = _utc(observed_at, "observed_at")
    if observed < occurred:
        raise GateOrderBookStreamPayloadError("observed_at cannot precede result.t")
    first_update_id = _integer(result.get("U"), "result.U")
    last_update_id = _integer(result.get("u"), "result.u")
    if last_update_id < first_update_id:
        raise GateOrderBookStreamPayloadError("result.u cannot precede result.U")
    if "full" in result and not isinstance(result["full"], bool):
        raise GateOrderBookStreamPayloadError("result.full must be bool when supplied")
    is_full_snapshot = result.get("full", False)
    if _integer(result.get("l"), "result.l", positive=True) != subscription.depth_limit:
        raise GateOrderBookStreamPayloadError("WebSocket update depth does not match REST snapshot depth")
    bids = _level_rows(result.get("b"), "result.b", subscription.market_type)
    asks = _level_rows(result.get("a"), "result.a", subscription.market_type)
    channel = expected_channel
    payload_fingerprint = _payload_fingerprint(
        subscription=subscription,
        channel=channel,
        occurred_at=occurred,
        first_update_id=first_update_id,
        last_update_id=last_update_id,
        is_full_snapshot=is_full_snapshot,
        bids=bids,
        asks=asks,
    )
    prefix = _text(source_event_prefix, "source_event_prefix")
    delta = GateOrderBookDelta(
        market_type=subscription.market_type,
        instrument_id=subscription.instrument_id,
        first_update_id=first_update_id,
        last_update_id=last_update_id,
        bids=bids,
        asks=asks,
        occurred_at=occurred,
        observed_at=observed,
        source_event_id=f"{prefix}:{channel}:{subscription.instrument_id}:{first_update_id}:{last_update_id}",
        snapshot_id=subscription.snapshot_id,
        rule_version=subscription.rule_version,
        payload_fingerprint=payload_fingerprint,
    )
    return GateOrderBookStreamFrame(
        subscription=subscription,
        delta=delta,
        channel=channel,
        is_full_snapshot=is_full_snapshot,
    )


__all__ = [
    "GATE_ORDER_BOOK_STREAM_PAYLOAD_CONTRACT_VERSION",
    "GateOrderBookStreamPayloadError",
    "normalize_gate_order_book_update_frame",
]
