"""Point-in-time dataset boundary between DATA-01 and BT-01.

This is an offline, immutable hand-off.  A future Gate adapter may produce
the market facts, but this contract never fetches them or opens a connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Tuple

from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.market_data_quality_contracts import DataQualityAssessment, DataQualityStatus, MarketDataEventFact


BACKTEST_DATASET_CONTRACT_VERSION = "backtest-dataset-v1"


class BacktestDatasetError(ValueError):
    """The dataset cannot be used as a point-in-time backtest input."""


_SUPPORTED_TIMEFRAMES = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d"})


def _text(value: object, field_name: str, *, lower: bool = False) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise BacktestDatasetError(f"{field_name} must be canonical ASCII text")
    return value.lower() if lower else value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise BacktestDatasetError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class BacktestDatasetSnapshot:
    dataset_snapshot_id: str
    venue: str
    market_type: str
    instrument_id: str
    rule_version: str
    bars: Tuple[BacktestBar, ...]
    quality: DataQualityAssessment
    as_of: datetime
    timeframe: str | None = None
    dataset_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_snapshot_id", _text(self.dataset_snapshot_id, "dataset_snapshot_id"))
        object.__setattr__(self, "venue", _text(self.venue, "venue", lower=True))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lower=True))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "rule_version", _text(self.rule_version, "rule_version"))
        if self.timeframe is not None:
            object.__setattr__(self, "timeframe", _text(self.timeframe, "timeframe", lower=True))
            if self.timeframe not in _SUPPORTED_TIMEFRAMES:
                raise BacktestDatasetError("timeframe is not an approved canonical value")
        if not isinstance(self.quality, DataQualityAssessment) or self.quality.status is not DataQualityStatus.COMPLETE:
            raise BacktestDatasetError("dataset requires COMPLETE point-in-time quality")
        bars = tuple(self.bars)
        if not bars or any(not isinstance(item, BacktestBar) for item in bars):
            raise BacktestDatasetError("dataset requires typed bars")
        if any(item.snapshot_id != self.dataset_snapshot_id or item.instrument_id != self.instrument_id for item in bars):
            raise BacktestDatasetError("bars must bind the dataset snapshot and instrument")
        if any(left.sequence >= right.sequence or left.open_time >= right.open_time for left, right in zip(bars, bars[1:])):
            raise BacktestDatasetError("bars must be strictly ordered by sequence and time")
        accepted = self.quality.accepted_events
        if len(accepted) != len(bars) or any(event.dataset_snapshot_id != self.dataset_snapshot_id for event in accepted):
            raise BacktestDatasetError("quality assessment must cover every dataset bar")
        object.__setattr__(self, "bars", bars)
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        if any(item.close_time > self.as_of for item in bars):
            raise BacktestDatasetError("dataset contains facts after as_of")
        object.__setattr__(self, "dataset_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "version": BACKTEST_DATASET_CONTRACT_VERSION,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "venue": self.venue,
            "market_type": self.market_type,
            "instrument_id": self.instrument_id,
            "rule_version": self.rule_version,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat(),
            "quality": self.quality.assessment_fingerprint,
            "bars": [{"sequence": item.sequence, "open_time": item.open_time.isoformat(), "snapshot_id": item.snapshot_id} for item in self.bars],
        }


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def dataset_fingerprint(dataset: BacktestDatasetSnapshot) -> str:
    if not isinstance(dataset, BacktestDatasetSnapshot):
        raise BacktestDatasetError("dataset must be a typed snapshot")
    return dataset.dataset_fingerprint


def coerce_backtest_dataset(value: object) -> BacktestDatasetSnapshot:
    """Reconstruct an equivalent isolated fixture through local validators."""

    if isinstance(value, BacktestDatasetSnapshot):
        return value
    if type(value).__name__ != "BacktestDatasetSnapshot":
        raise BacktestDatasetError("dataset must be a typed snapshot")
    try:
        bars = tuple(BacktestBar(
            item.instrument_id, item.open_time, item.close_time,
            item.open_price, item.high_price, item.low_price, item.close_price,
            item.volume, item.sequence, item.snapshot_id,
        ) for item in value.bars)
        quality = value.quality
        accepted = tuple(MarketDataEventFact(
            event.event_id, event.source, event.instrument_id, event.occurred_at,
            event.observed_at, event.sequence, event.dataset_snapshot_id,
            event.rule_version, event.payload_fingerprint,
        ) for event in quality.accepted_events)
        local_quality = DataQualityAssessment(
            DataQualityStatus(getattr(quality.status, "value", quality.status)),
            accepted,
            tuple(quality.rejected_event_ids),
            quality.as_of,
            quality.assessment_fingerprint,
            getattr(quality, "expected_sequences", None),
            getattr(quality, "disconnected", False),
        )
        return BacktestDatasetSnapshot(
            value.dataset_snapshot_id, value.venue, value.market_type,
            value.instrument_id, value.rule_version, bars, local_quality, value.as_of,
            getattr(value, "timeframe", None),
        )
    except (AttributeError, TypeError, ValueError, BacktestDatasetError) as exc:
        raise BacktestDatasetError("dataset must be a complete typed snapshot") from exc


__all__ = ["BACKTEST_DATASET_CONTRACT_VERSION", "BacktestDatasetError", "BacktestDatasetSnapshot", "coerce_backtest_dataset", "dataset_fingerprint"]
