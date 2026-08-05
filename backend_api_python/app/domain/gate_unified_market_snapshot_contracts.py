"""Immutable Spot + Perpetual Gate market evidence aggregate.

The aggregate is deliberately read-only and keeps product books separate.  It
is suitable for a dashboard or research consumer, but it is not an execution
authority and never combines Spot and Perpetual quantities or balances.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .multi_asset_capability_contracts import AssetMarketType

if TYPE_CHECKING:
    from app.services.gate_market_research_service import GateMarketEvidenceBundle


GATE_UNIFIED_MARKET_SNAPSHOT_CONTRACT_VERSION = "gate-unified-market-snapshot-v1"


class GateUnifiedMarketSnapshotError(ValueError):
    """Malformed, incomplete, or cross-scope market aggregate."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateUnifiedMarketSnapshotError("observed_at must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _fingerprint(material: Any) -> str:
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GateUnifiedMarketSnapshot:
    """An all-or-nothing, same-instrument Spot + Perpetual snapshot."""

    bundles: tuple["GateMarketEvidenceBundle", ...]
    instrument_id: str
    interval: str
    observed_at: datetime
    snapshot_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.bundles, tuple) or not self.bundles:
            raise GateUnifiedMarketSnapshotError("at least one market bundle is required")
        if any(not _is_bundle(item) for item in self.bundles):
            raise GateUnifiedMarketSnapshotError("bundles must be typed GateMarketEvidenceBundle values")
        if not isinstance(self.instrument_id, str) or not self.instrument_id or self.instrument_id.strip() != self.instrument_id or not self.instrument_id.isascii():
            raise GateUnifiedMarketSnapshotError("instrument_id must be canonical ASCII text")
        if not isinstance(self.interval, str) or not self.interval or self.interval.strip() != self.interval or not self.interval.isascii():
            raise GateUnifiedMarketSnapshotError("interval must be canonical ASCII text")
        markets = [_market_value(item.market_type) for item in self.bundles]
        if len(set(markets)) != len(markets):
            raise GateUnifiedMarketSnapshotError("market_type must be unique")
        observed = _utc(self.observed_at)
        for bundle in self.bundles:
            if bundle.instrument_id != self.instrument_id or bundle.interval != self.interval:
                raise GateUnifiedMarketSnapshotError("market bundle scope mismatch")
            if bundle.observed_at > observed:
                raise GateUnifiedMarketSnapshotError("bundle observed_at cannot exceed aggregate observed_at")
        expected = build_gate_unified_market_snapshot_fingerprint(self.bundles, self.instrument_id, self.interval, observed)
        if self.snapshot_fingerprint != expected:
            raise GateUnifiedMarketSnapshotError("snapshot_fingerprint does not match immutable facts")
        object.__setattr__(self, "observed_at", observed)

    def to_public_dict(self) -> dict[str, Any]:
        """Return sanitized, market-separated evidence for read-only clients."""

        def decimal(value: Any) -> str:
            return format(value, "f")

        def bundle_dict(bundle: GateMarketEvidenceBundle) -> dict[str, Any]:
            return {
                "market_type": bundle.market_type.value,
                "instrument_id": bundle.instrument_id,
                "interval": bundle.interval,
                "snapshot_id": bundle.snapshot_id,
                "rule_version": bundle.rule_version,
                "bundle_fingerprint": bundle.bundle_fingerprint,
                "observed_at": bundle.observed_at.isoformat(),
                "candles": [
                    {"open_time": item.open_time.isoformat(), "close_time": item.close_time.isoformat(),
                     "open": decimal(item.open_price), "high": decimal(item.high_price),
                     "low": decimal(item.low_price), "close": decimal(item.close_price),
                     "volume": decimal(item.volume), "sequence": item.sequence, "evidence_hash": item.evidence_hash}
                    for item in bundle.candles
                ],
                "order_book": {
                    "occurred_at": bundle.order_book.occurred_at.isoformat(),
                    "observed_at": bundle.order_book.observed_at.isoformat(),
                    "sequence": bundle.order_book.sequence,
                    "bids": [[decimal(level.price), decimal(level.quantity)] for level in bundle.order_book.bids],
                    "asks": [[decimal(level.price), decimal(level.quantity)] for level in bundle.order_book.asks],
                    "evidence_hash": bundle.order_book.evidence_hash,
                },
            }

        return {
            "contract_version": GATE_UNIFIED_MARKET_SNAPSHOT_CONTRACT_VERSION,
            "status": "READY",
            "venue_id": "gate",
            "environment": "TESTNET",
            "instrument_id": self.instrument_id,
            "interval": self.interval,
            "market_types": [item.market_type.value for item in self.bundles],
            "observed_at": self.observed_at.isoformat(),
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "markets": {item.market_type.value: bundle_dict(item) for item in self.bundles},
            "network_access": True,
            "live_enabled": False,
        }


def build_gate_unified_market_snapshot_fingerprint(
    bundles: tuple["GateMarketEvidenceBundle", ...], instrument_id: str, interval: str, observed_at: datetime
) -> str:
    if not isinstance(bundles, tuple) or any(not _is_bundle(item) for item in bundles):
        raise GateUnifiedMarketSnapshotError("bundles must be typed")
    observed = _utc(observed_at)
    return _fingerprint({
        "version": GATE_UNIFIED_MARKET_SNAPSHOT_CONTRACT_VERSION,
        "instrument_id": instrument_id,
        "interval": interval,
        "observed_at": observed.isoformat(),
            "markets": sorted((_market_value(item.market_type), item.bundle_fingerprint) for item in bundles),
    })


def build_gate_unified_market_snapshot(
    bundles: tuple["GateMarketEvidenceBundle", ...], *, instrument_id: str, interval: str, observed_at: datetime
) -> GateUnifiedMarketSnapshot:
    observed = _utc(observed_at)
    return GateUnifiedMarketSnapshot(
        bundles=bundles,
        instrument_id=instrument_id,
        interval=interval,
        observed_at=observed,
        snapshot_fingerprint=build_gate_unified_market_snapshot_fingerprint(bundles, instrument_id, interval, observed),
    )


def _is_bundle(value: Any) -> bool:
    """Validate the service-owned immutable bundle without importing services."""

    return (
        value.__class__.__name__ == "GateMarketEvidenceBundle"
        and value.__class__.__module__.endswith("gate_market_research_service")
        and _market_value(getattr(value, "market_type", None)) is not None
        and isinstance(getattr(value, "instrument_id", None), str)
        and isinstance(getattr(value, "interval", None), str)
        and isinstance(getattr(value, "bundle_fingerprint", None), str)
    )


def _market_value(value: Any) -> str | None:
    """Accept equivalent enum instances from isolated test import sandboxes."""

    raw = getattr(value, "value", None)
    return raw if raw in {"spot", "perpetual"} else None


__all__ = [
    "GATE_UNIFIED_MARKET_SNAPSHOT_CONTRACT_VERSION",
    "GateUnifiedMarketSnapshot",
    "GateUnifiedMarketSnapshotError",
    "build_gate_unified_market_snapshot",
    "build_gate_unified_market_snapshot_fingerprint",
]
