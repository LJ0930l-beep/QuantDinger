"""Offline formatters for official Gate public market payload shapes.

Only supplied payloads are accepted.  This module has no HTTP transport,
credential handling, persistence, or order functionality.  Ambiguous rows,
open candles, and non-finite numeric values fail closed before becoming Gate
market facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping, Sequence, Tuple

from app.domain.gate_market_read_contracts import (
    GateCandleFact,
    GateMarketContractError,
    GateOrderBookLevel,
    GateOrderBookSnapshot,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType


GATE_MARKET_PAYLOAD_CONTRACT_VERSION = "gate-market-payload-v1"
_CANDLE_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "8h": 28800,
    "1d": 86400,
}


class GateMarketPayloadError(GateMarketContractError):
    """A Gate public payload cannot be normalized safely."""


def _is_closed_candle(value: Any) -> bool:
    """Accept Gate's documented boolean and JSON-string encodings only."""

    if value is True:
        return True
    if isinstance(value, str) and value == "true":
        return True
    return False


def _rows(payload: Any, field: str) -> tuple[Sequence[Any], ...]:
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise GateMarketPayloadError(f"{field} payload must be a list")
    rows = tuple(payload)
    if not rows:
        raise GateMarketPayloadError(f"{field} payload must not be empty")
    return rows


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise GateMarketPayloadError(f"{field} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateMarketPayloadError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise GateMarketPayloadError(f"{field} must be finite")
    return result


def _timestamp_seconds(value: Any, field: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GateMarketPayloadError(f"{field} must be an integer Unix timestamp in seconds")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GateMarketPayloadError(f"{field} must be an integer Unix timestamp in seconds") from exc
    if parsed < 0:
        raise GateMarketPayloadError(f"{field} must be non-negative")
    return datetime.fromtimestamp(parsed, tz=timezone.utc)


def _timestamp_millis(value: Any, field: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GateMarketPayloadError(f"{field} must be an integer Unix timestamp in milliseconds")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GateMarketPayloadError(f"{field} must be an integer Unix timestamp in milliseconds") from exc
    if parsed < 0:
        raise GateMarketPayloadError(f"{field} must be non-negative")
    return datetime.fromtimestamp(parsed / 1000, tz=timezone.utc)


def _gate_clock(value: Any, field: str) -> tuple[datetime, int]:
    """Normalize Gate Spot millisecond or Futures fractional-second clocks."""

    if isinstance(value, bool) or not isinstance(value, (int, str, float, Decimal)):
        raise GateMarketPayloadError(f"{field} must be a numeric Unix timestamp")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateMarketPayloadError(f"{field} must be a numeric Unix timestamp") from exc
    if not parsed.is_finite() or parsed < 0:
        raise GateMarketPayloadError(f"{field} must be a finite Unix timestamp")
    # Futures currently returns seconds with fractional milliseconds (for
    # example 1785732681.039), while Spot returns integer milliseconds.
    seconds = parsed if parsed < Decimal("100000000000") else parsed / Decimal("1000")
    whole = int(seconds)
    micros = int((seconds - Decimal(whole)) * Decimal("1000000"))
    occurred = datetime.fromtimestamp(whole, tz=timezone.utc) + timedelta(microseconds=micros)
    return occurred, int(seconds * Decimal("1000"))


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateMarketPayloadError(f"{field} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise GateMarketPayloadError(f"{field} must be canonical ASCII text")
    return value


def _evidence(prefix: str, row: Any) -> str:
    _text(prefix, "evidence_hash_prefix")
    material = json.dumps(row, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    return f"{prefix}:{digest}"


def gate_candle_interval_seconds(interval: str) -> int:
    """Return the approved Gate candle interval without inferring a default."""

    value = _text(interval, "interval")
    if value not in _CANDLE_INTERVAL_SECONDS:
        raise GateMarketPayloadError("interval is not supported for deterministic candles")
    return _CANDLE_INTERVAL_SECONDS[value]


def normalize_gate_candles(
    payload: Any,
    *,
    market_type: AssetMarketType,
    instrument_id: str,
    interval: str,
    observed_at: datetime,
    source_event_prefix: str,
    snapshot_id: str,
    rule_version: str,
    evidence_hash_prefix: str,
) -> Tuple[GateCandleFact, ...]:
    """Normalize Gate ``[timestamp, quote, close, high, low, open, base, closed]`` rows."""

    if not isinstance(market_type, AssetMarketType):
        raise GateMarketPayloadError("market_type must be typed")
    instrument = _text(instrument_id, "instrument_id")
    observed = _utc(observed_at, "observed_at")
    interval_seconds = gate_candle_interval_seconds(interval)
    rows = _rows(payload, "candles")
    result = []
    previous_timestamp: int | None = None
    for row in rows:
        # Spot returns the legacy eight-element array.  The current futures
        # endpoint returns an object with canonical short keys instead; keep
        # both shapes typed so Spot and Perpetual share the same evidence
        # contract without guessing at missing values.
        if isinstance(row, Mapping):
            if market_type is not AssetMarketType.PERPETUAL:
                raise GateMarketPayloadError("object candle rows are only valid for perpetual futures")
            required = ("t", "o", "h", "l", "c", "v")
            if any(key not in row for key in required):
                raise GateMarketPayloadError("Gate perpetual candle object is incomplete")
            opened = _timestamp_seconds(row["t"], "candle timestamp")
            try:
                timestamp = int(row["t"])
            except (TypeError, ValueError) as exc:
                raise GateMarketPayloadError("candle timestamp must be an integer") from exc
            open_value, high_value, low_value = row["o"], row["h"], row["l"]
            close_value, volume_value = row["c"], row["v"]
            row_for_evidence = row
        else:
            if len(row) != 8:
                raise GateMarketPayloadError("Gate candle row must contain eight fields")
            # Gate returns the currently forming candle together with closed
            # candles.  It is not a complete fact and must not enter the durable
            # evidence bundle, but its presence should not make an otherwise
            # usable response unavailable.  A payload containing only forming
            # candles still fails closed below.
            if not _is_closed_candle(row[7]):
                continue
            opened = _timestamp_seconds(row[0], "candle timestamp")
            timestamp = int(row[0])
            open_value, high_value, low_value = row[5], row[3], row[4]
            close_value, volume_value = row[2], row[6]
            row_for_evidence = row
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise GateMarketPayloadError("candle timestamps must be strictly increasing")
        if previous_timestamp is not None and timestamp - previous_timestamp != interval_seconds:
            raise GateMarketPayloadError("candle timestamps contain a gap")
        closed = _timestamp_seconds(timestamp + interval_seconds, "candle close")
        if observed < closed:
            # The futures object shape does not carry a closed flag; the
            # interval boundary is the authoritative completeness check.
            if isinstance(row, Mapping):
                continue
            raise GateMarketPayloadError("observed_at cannot precede candle close")
        result.append(GateCandleFact(
            market_type=market_type,
            instrument_id=instrument,
            interval=_text(interval, "interval"),
            open_time=opened,
            close_time=closed,
            open_price=_decimal(open_value, "open_price"),
            high_price=_decimal(high_value, "high_price"),
            low_price=_decimal(low_value, "low_price"),
            close_price=_decimal(close_value, "close_price"),
            volume=_decimal(volume_value, "volume"),
            occurred_at=opened,
            observed_at=observed,
            sequence=timestamp,
            source_event_id=f"{_text(source_event_prefix, 'source_event_prefix')}:{timestamp}",
            snapshot_id=_text(snapshot_id, "snapshot_id"),
            rule_version=_text(rule_version, "rule_version"),
            evidence_hash=_evidence(evidence_hash_prefix, row_for_evidence),
        ))
        previous_timestamp = timestamp
    if not result:
        raise GateMarketPayloadError("Gate payload contains no closed candles")
    return tuple(result)


def normalize_gate_order_book(
    payload: Mapping[str, Any],
    *,
    market_type: AssetMarketType,
    instrument_id: str,
    source_event_prefix: str,
    snapshot_id: str,
    rule_version: str,
    evidence_hash_prefix: str,
    depth_limit: int | None = None,
) -> GateOrderBookSnapshot:
    """Normalize Gate ``current/update/id/asks/bids`` depth payload."""

    if not isinstance(payload, Mapping):
        raise GateMarketPayloadError("order book payload must be an object")
    if depth_limit is not None and (isinstance(depth_limit, bool) or not isinstance(depth_limit, int) or depth_limit <= 0):
        raise GateMarketPayloadError("depth_limit must be a positive integer when supplied")
    current, _current_ms = _gate_clock(payload.get("current"), "current")
    update, update_ms = _gate_clock(payload.get("update"), "update")
    if current < update:
        raise GateMarketPayloadError("current cannot precede update")
    def levels(name: str) -> tuple[GateOrderBookLevel, ...]:
        raw = payload.get(name)
        rows = _rows(raw, name)
        parsed = []
        for row in rows:
            if isinstance(row, Mapping):
                if market_type is not AssetMarketType.PERPETUAL or "p" not in row or "s" not in row:
                    raise GateMarketPayloadError(f"{name} level must contain price and quantity")
                price, quantity = row["p"], row["s"]
            else:
                if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 2:
                    raise GateMarketPayloadError(f"{name} level must contain price and quantity")
                price, quantity = row[0], row[1]
            parsed.append(GateOrderBookLevel(_decimal(price, f"{name}.price"), _decimal(quantity, f"{name}.quantity")))
        return tuple(parsed)
    sequence = payload.get("id")
    if sequence is None:
        # Some Gate public responses omit ``id``.  The update clock is the
        # only deterministic fallback and remains scoped to this snapshot.
        sequence = update_ms
    elif isinstance(sequence, bool) or not isinstance(sequence, (int, str)):
        raise GateMarketPayloadError("order book id must be an integer")
    else:
        try:
            sequence = int(sequence)
        except (TypeError, ValueError) as exc:
            raise GateMarketPayloadError("order book id must be an integer") from exc
    return GateOrderBookSnapshot(
        market_type=market_type,
        instrument_id=_text(instrument_id, "instrument_id"),
        bids=levels("bids"),
        asks=levels("asks"),
        occurred_at=update,
        observed_at=current,
        sequence=sequence,
        source_event_id=f"{_text(source_event_prefix, 'source_event_prefix')}:{sequence}",
        snapshot_id=_text(snapshot_id, "snapshot_id"),
        rule_version=_text(rule_version, "rule_version"),
        evidence_hash=_evidence(evidence_hash_prefix, payload),
        depth_limit=depth_limit,
    )


__all__ = [
    "GATE_MARKET_PAYLOAD_CONTRACT_VERSION",
    "GateMarketPayloadError",
    "gate_candle_interval_seconds",
    "normalize_gate_candles",
    "normalize_gate_order_book",
]
