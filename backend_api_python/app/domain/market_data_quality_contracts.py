"""Point-in-time market-data quality contracts (DATA-01)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Tuple


MARKET_DATA_QUALITY_CONTRACT_VERSION = "market-data-quality-v1"


class MarketDataQualityError(ValueError):
    """A data quality invariant cannot be established."""


class DataQualityStatus(str, Enum):
    COMPLETE = "complete"
    MISSING = "missing"
    LATE = "late"
    OUT_OF_ORDER = "out_of_order"
    CONFLICT = "conflict"
    DUPLICATE = "duplicate"
    DISCONNECTED = "disconnected"
    GAP = "gap"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) > 127 or c.isspace() for c in value): raise MarketDataQualityError(f"{field} must be canonical ASCII text")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value): raise MarketDataQualityError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MarketDataEventFact:
    event_id: str
    source: str
    instrument_id: str
    occurred_at: datetime
    observed_at: datetime
    sequence: int
    dataset_snapshot_id: str
    rule_version: str
    payload_fingerprint: str

    def __post_init__(self) -> None:
        for field in ("event_id", "source", "instrument_id", "dataset_snapshot_id", "rule_version", "payload_fingerprint"): _text(getattr(self, field), field)
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at")); object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.observed_at < self.occurred_at: raise MarketDataQualityError("observed_at cannot precede occurred_at")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0: raise MarketDataQualityError("sequence must be non-negative")


@dataclass(frozen=True)
class DataQualityAssessment:
    status: DataQualityStatus
    accepted_events: Tuple[MarketDataEventFact, ...]
    rejected_event_ids: Tuple[str, ...]
    as_of: datetime
    assessment_fingerprint: str
    expected_sequences: Tuple[int, ...] | None = None
    disconnected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, DataQualityStatus): raise MarketDataQualityError("status must be typed")
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        if any(not isinstance(e, MarketDataEventFact) for e in self.accepted_events): raise MarketDataQualityError("accepted events must be typed")
        if any(not isinstance(e, str) for e in self.rejected_event_ids): raise MarketDataQualityError("rejected ids must be text")
        _text(self.assessment_fingerprint, "assessment_fingerprint")
        if self.expected_sequences is not None:
            if tuple(sorted(set(self.expected_sequences))) != self.expected_sequences or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in self.expected_sequences):
                raise MarketDataQualityError("expected_sequences must be a sorted unique tuple")
        if not isinstance(self.disconnected, bool): raise MarketDataQualityError("disconnected must be boolean")


def assess_point_in_time(
    events: Tuple[MarketDataEventFact, ...],
    *,
    as_of: datetime,
    expected_sequences: Tuple[int, ...] | None = None,
    disconnected: bool = False,
) -> DataQualityAssessment:
    """Assess caller-owned facts without silently repairing missing data.

    ``expected_sequences`` is optional for backwards compatibility, but when
    supplied it is an exact ordered contract.  A missing sequence is reported
    as ``GAP`` and therefore cannot form a COMPLETE backtest dataset.  A
    disconnected source is explicitly non-complete even when the delivered
    rows happen to be contiguous.
    """
    cutoff = _utc(as_of, "as_of")
    if not isinstance(events, tuple): raise MarketDataQualityError("events must be an explicit tuple")
    if expected_sequences is not None:
        if not isinstance(expected_sequences, tuple) or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in expected_sequences) or tuple(sorted(set(expected_sequences))) != expected_sequences:
            raise MarketDataQualityError("expected_sequences must be a sorted unique tuple")
    if not isinstance(disconnected, bool):
        raise MarketDataQualityError("disconnected must be boolean")
    # Sequence uniqueness alone is not sufficient for a point-in-time
    # dataset. A source may replay the same event with a different sequence;
    # accepting that second row would create two facts for one immutable
    # identity. Track both identities and fail closed on a mismatched replay.
    seen: dict[tuple[str, int], MarketDataEventFact] = {}
    seen_event_ids: dict[tuple[str, str], MarketDataEventFact] = {}
    accepted: list[MarketDataEventFact] = []; rejected: list[str] = []; status = DataQualityStatus.COMPLETE
    previous_sequence: int | None = None
    for event in events:
        if not isinstance(event, MarketDataEventFact): raise MarketDataQualityError("events must be typed")
        if event.observed_at > cutoff:
            status = DataQualityStatus.LATE; rejected.append(event.event_id); continue
        key = (event.source, event.sequence)
        event_key = (event.source, event.event_id)
        prior_event = seen_event_ids.get(event_key)
        if prior_event is not None:
            same_identity = (
                prior_event.sequence == event.sequence
                and prior_event.payload_fingerprint == event.payload_fingerprint
                and prior_event.dataset_snapshot_id == event.dataset_snapshot_id
                and prior_event.rule_version == event.rule_version
            )
            status = DataQualityStatus.DUPLICATE if same_identity else DataQualityStatus.CONFLICT
            rejected.append(event.event_id)
            continue
        prior = seen.get(key)
        if prior is not None:
            status = DataQualityStatus.DUPLICATE if prior.payload_fingerprint == event.payload_fingerprint else DataQualityStatus.CONFLICT
            rejected.append(event.event_id); continue
        if previous_sequence is not None and event.sequence < previous_sequence:
            status = DataQualityStatus.OUT_OF_ORDER; rejected.append(event.event_id); continue
        seen[key] = event; seen_event_ids[event_key] = event; accepted.append(event); previous_sequence = event.sequence
    if not events:
        status = DataQualityStatus.MISSING
    elif disconnected:
        status = DataQualityStatus.DISCONNECTED
    elif status is DataQualityStatus.COMPLETE and expected_sequences is not None and tuple(item.sequence for item in accepted) != expected_sequences:
        status = DataQualityStatus.GAP
    material = {
        "version": MARKET_DATA_QUALITY_CONTRACT_VERSION,
        "status": status.value,
        "accepted": [e.event_id for e in accepted],
        "rejected": rejected,
        "as_of": cutoff.isoformat(),
        "expected_sequences": expected_sequences,
        "disconnected": disconnected,
    }
    fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return DataQualityAssessment(status, tuple(accepted), tuple(rejected), cutoff, fingerprint, expected_sequences, disconnected)


def quality_fingerprint(value: Any) -> str:
    def norm(item: Any) -> Any:
        if isinstance(item, Enum): return item.value
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "__dataclass_fields__"): return norm(asdict(item))
        if isinstance(item, dict): return {str(k): norm(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        if isinstance(item, (tuple, list)): return [norm(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


__all__ = ["DataQualityAssessment", "DataQualityStatus", "MARKET_DATA_QUALITY_CONTRACT_VERSION", "MarketDataEventFact", "MarketDataQualityError", "assess_point_in_time", "quality_fingerprint"]
