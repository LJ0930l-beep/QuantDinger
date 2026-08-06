"""Non-live Gate market evidence assembly.

This service composes the injected, read-only Gate adapter with the existing
payload formatters.  It deliberately stops at immutable market facts: there
is no credential lookup, HTTP client, persistence, scheduling, order
submission, or live-trading authority in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from app.domain.gate_market_payload_contracts import (
    GateMarketPayloadError,
    gate_candle_interval_seconds,
    normalize_gate_candles,
    normalize_gate_order_book,
)
from app.domain.gate_market_read_contracts import (
    GateCandleFact,
    GateOrderBookSnapshot,
    gate_market_fingerprint,
)
from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter, GateReadonlyAdapterError
from app.domain.multi_asset_capability_contracts import AssetMarketType


GATE_MARKET_RESEARCH_SERVICE_VERSION = "gate-market-research-v1"


class GateMarketResearchServiceError(ValueError):
    """The injected read evidence cannot become a deterministic fact bundle."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateMarketResearchServiceError("observed_at must use a zero UTC offset")
    return value.astimezone(timezone.utc)


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class GateMarketEvidenceBundle:
    """Immutable candles plus order-book evidence for one scoped read."""

    market_type: AssetMarketType
    instrument_id: str
    interval: str
    candles: tuple[GateCandleFact, ...]
    order_book: GateOrderBookSnapshot
    observed_at: datetime
    snapshot_id: str
    rule_version: str
    bundle_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, AssetMarketType):
            raise GateMarketResearchServiceError("market_type must be typed")
        if not isinstance(self.instrument_id, str) or not self.instrument_id or self.instrument_id.strip() != self.instrument_id or not self.instrument_id.isascii():
            raise GateMarketResearchServiceError("instrument_id must be canonical ASCII text")
        if not isinstance(self.interval, str) or not self.interval:
            raise GateMarketResearchServiceError("interval is required")
        if not isinstance(self.candles, tuple) or any(not isinstance(item, GateCandleFact) for item in self.candles):
            raise GateMarketResearchServiceError("candles must be typed facts")
        if not self.candles:
            raise GateMarketResearchServiceError("candles must not be empty")
        if not isinstance(self.order_book, GateOrderBookSnapshot):
            raise GateMarketResearchServiceError("order_book must be typed")
        observed = _utc(self.observed_at)
        try:
            interval_seconds = gate_candle_interval_seconds(self.interval)
        except GateMarketPayloadError as exc:
            raise GateMarketResearchServiceError("interval is not supported for deterministic evidence") from exc
        if self.order_book.market_type is not self.market_type or self.order_book.instrument_id != self.instrument_id:
            raise GateMarketResearchServiceError("order_book scope does not match bundle")
        if any(
            item.market_type is not self.market_type
            or item.instrument_id != self.instrument_id
            or item.interval != self.interval
            or item.snapshot_id != self.snapshot_id
            or item.rule_version != self.rule_version
            for item in self.candles
        ):
            raise GateMarketResearchServiceError("candle scope does not match bundle")
        if self.order_book.observed_at > observed or any(item.observed_at > observed for item in self.candles):
            raise GateMarketResearchServiceError("bundle observed_at precedes a fact")
        for field_name in ("snapshot_id", "rule_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value.strip() != value or not value.isascii():
                raise GateMarketResearchServiceError(f"{field_name} must be canonical ASCII text")
        previous: GateCandleFact | None = None
        source_event_ids: set[str] = set()
        for item in self.candles:
            if item.close_time - item.open_time != timedelta(seconds=interval_seconds):
                raise GateMarketResearchServiceError("candle duration does not match bundle interval")
            if item.occurred_at != item.open_time:
                raise GateMarketResearchServiceError("candle occurred_at must equal open_time")
            if item.source_event_id in source_event_ids:
                raise GateMarketResearchServiceError("candle source event identity is duplicated")
            source_event_ids.add(item.source_event_id)
            if previous is not None:
                if item.sequence <= previous.sequence:
                    raise GateMarketResearchServiceError("candle sequence must be strictly increasing")
                if item.open_time != previous.close_time:
                    raise GateMarketResearchServiceError("candle evidence contains a gap or overlap")
            previous = item
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "bundle_fingerprint", _fingerprint({
            "version": GATE_MARKET_RESEARCH_SERVICE_VERSION,
            "market_type": self.market_type.value,
            "instrument_id": self.instrument_id,
            "interval": self.interval,
            "candles": [gate_market_fingerprint(item) for item in self.candles],
            "order_book": gate_market_fingerprint(self.order_book),
            "observed_at": observed.isoformat(),
            "snapshot_id": self.snapshot_id,
            "rule_version": self.rule_version,
        }))


@dataclass(frozen=True, slots=True)
class GateMarketResearchService:
    """Build market evidence using a caller-owned, read-only Gate adapter."""

    adapter: GateReadonlyAdapter
    source_event_prefix: str
    evidence_hash_prefix: str

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, GateReadonlyAdapter):
            raise GateMarketResearchServiceError("a typed GateReadonlyAdapter is required")
        for field_name in ("source_event_prefix", "evidence_hash_prefix"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value.strip() != value or not value.isascii():
                raise GateMarketResearchServiceError(f"{field_name} must be canonical ASCII text")

    def read_market_evidence(
        self,
        instrument_id: str,
        *,
        interval: str = "1m",
        candle_limit: int = 100,
        depth_limit: int = 20,
        observed_at: datetime,
        snapshot_id: str,
        rule_version: str,
    ) -> GateMarketEvidenceBundle:
        """Read and normalize one immutable market evidence bundle.

        The adapter may be backed by a fixture or a separately approved
        public read transport.  This method never creates that transport.
        """

        observed = _utc(observed_at)
        try:
            # Gate read transport uses its own profile enum; market evidence
            # facts use the shared multi-asset enum.  Convert by canonical
            # value, never by object identity or a guessed default.
            market_type = AssetMarketType(self.adapter.profile.market_type.value)
            candle_response = self.adapter.candles(instrument_id, interval=interval, limit=candle_limit)
            book_response = self.adapter.order_book(instrument_id, limit=depth_limit)
            if candle_response.status_code != 200 or book_response.status_code != 200:
                raise GateMarketResearchServiceError("Gate market read did not return success")
            if candle_response.payload is None or book_response.payload is None:
                raise GateMarketResearchServiceError("Gate market read returned no payload")
            candles = normalize_gate_candles(
                candle_response.payload,
                market_type=market_type,
                instrument_id=instrument_id,
                interval=interval,
                observed_at=observed,
                source_event_prefix=self.source_event_prefix,
                snapshot_id=snapshot_id,
                rule_version=rule_version,
                evidence_hash_prefix=self.evidence_hash_prefix,
            )
            order_book = normalize_gate_order_book(
                book_response.payload,
                market_type=market_type,
                instrument_id=instrument_id,
                source_event_prefix=self.source_event_prefix,
                snapshot_id=snapshot_id,
                rule_version=rule_version,
                evidence_hash_prefix=self.evidence_hash_prefix,
                depth_limit=depth_limit,
            )
            return GateMarketEvidenceBundle(
                market_type,
                instrument_id,
                interval,
                candles,
                order_book,
                observed,
                snapshot_id,
                rule_version,
            )
        except GateMarketResearchServiceError:
            raise
        except (GateReadonlyAdapterError, GateMarketPayloadError) as exc:
            raise GateMarketResearchServiceError("Gate market evidence is invalid or unavailable") from exc
        except Exception as exc:
            raise GateMarketResearchServiceError("Gate market evidence could not be assembled") from exc


__all__ = [
    "GATE_MARKET_RESEARCH_SERVICE_VERSION",
    "GateMarketEvidenceBundle",
    "GateMarketResearchService",
    "GateMarketResearchServiceError",
]
