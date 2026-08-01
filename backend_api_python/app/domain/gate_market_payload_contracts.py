"""Offline formatters for official Gate public market payload shapes.

Only supplied payloads are accepted.  This module has no HTTP transport,
credential handling, persistence, or order functionality.  Ambiguous rows,
open candles, and non-finite numeric values fail closed before becoming Gate
market facts.
"""

from __future__ import annotations

from datetime import datetime, timezone
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


class GateMarketPayloadError(GateMarketContractError):
    """A Gate public payload cannot be normalized safely."""


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
    rows = _rows(payload, "candles")
    result = []
    previous_timestamp: int | None = None
    for row in rows:
        if len(row) != 8:
            raise GateMarketPayloadError("Gate candle row must contain eight fields")
        if row[7] is not True:
            raise GateMarketPayloadError("open Gate candle cannot enter a complete fact set")
        opened = _timestamp_seconds(row[0], "candle timestamp")
        timestamp = int(row[0])
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise GateMarketPayloadError("candle timestamps must be strictly increasing")
        closed = _timestamp_seconds(timestamp + _interval_seconds(interval), "candle close")
        if observed < closed:
            raise GateMarketPayloadError("observed_at cannot precede candle close")
        result.append(GateCandleFact(
            market_type=market_type,
            instrument_id=instrument,
            interval=_text(interval, "interval"),
            open_time=opened,
            close_time=closed,
            open_price=_decimal(row[5], "open_price"),
            high_price=_decimal(row[3], "high_price"),
            low_price=_decimal(row[4], "low_price"),
            close_price=_decimal(row[2], "close_price"),
            volume=_decimal(row[6], "volume"),
            occurred_at=opened,
            observed_at=observed,
            sequence=timestamp,
            source_event_id=f"{_text(source_event_prefix, 'source_event_prefix')}:{timestamp}",
            snapshot_id=_text(snapshot_id, "snapshot_id"),
            rule_version=_text(rule_version, "rule_version"),
            evidence_hash=_evidence(evidence_hash_prefix, row),
        ))
        previous_timestamp = timestamp
    return tuple(result)


def _interval_seconds(interval: str) -> int:
    value = _text(interval, "interval")
    units = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "8h": 28800, "1d": 86400}
    if value not in units:
        raise GateMarketPayloadError("interval is not supported for deterministic candles")
    return units[value]


def normalize_gate_order_book(
    payload: Mapping[str, Any],
    *,
    market_type: AssetMarketType,
    instrument_id: str,
    source_event_prefix: str,
    snapshot_id: str,
    rule_version: str,
    evidence_hash_prefix: str,
) -> GateOrderBookSnapshot:
    """Normalize Gate ``current/update/id/asks/bids`` depth payload."""

    if not isinstance(payload, Mapping):
        raise GateMarketPayloadError("order book payload must be an object")
    current = _timestamp_millis(payload.get("current"), "current")
    update = _timestamp_millis(payload.get("update"), "update")
    if current < update:
        raise GateMarketPayloadError("current cannot precede update")
    def levels(name: str) -> tuple[GateOrderBookLevel, ...]:
        raw = payload.get(name)
        rows = _rows(raw, name)
        parsed = []
        for row in rows:
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 2:
                raise GateMarketPayloadError(f"{name} level must contain price and quantity")
            parsed.append(GateOrderBookLevel(_decimal(row[0], f"{name}.price"), _decimal(row[1], f"{name}.quantity")))
        return tuple(parsed)
    sequence = payload.get("id", payload.get("update"))
    if isinstance(sequence, bool) or not isinstance(sequence, (int, str)):
        raise GateMarketPayloadError("order book id must be an integer")
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
    )


__all__ = ["GATE_MARKET_PAYLOAD_CONTRACT_VERSION", "GateMarketPayloadError", "normalize_gate_candles", "normalize_gate_order_book"]
