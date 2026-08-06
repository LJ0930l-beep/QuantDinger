"""Typed read-only projection-generation summaries.

This contract is intentionally narrower than the G4-B read-cutover receipt.
It exposes only persisted generation/checkpoint facts and never claims that a
candidate projection has passed shadow comparison or reconciliation.  The
full quant state endpoint must continue to require a validated G4-B receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID


READONLY_PROJECTION_SUMMARY_VERSION = "readonly-projection-summary-v1"


class ReadonlyProjectionSummaryError(ValueError):
    """Persisted projection facts are incomplete or non-canonical."""


def _text(value: object, field_name: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii() or len(value) > max_length:
        raise ReadonlyProjectionSummaryError(f"{field_name} must be canonical ASCII text")
    return value


def _uuid(value: object, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value))).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReadonlyProjectionSummaryError(f"{field_name} must be a UUID") from exc


def _non_negative(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReadonlyProjectionSummaryError(f"{field_name} must be non-negative")
    return value


def _watermark(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < -1:
        raise ReadonlyProjectionSummaryError(f"{field_name} must be >= -1")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyProjectionSummaryError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReadonlyProjectionGenerationSummary:
    generation_id: str
    consumer_name: str
    build_fingerprint: str
    state: str
    source_high_watermark: int
    processed_high_watermark: int
    expected_event_count: int
    applied_event_count: int
    checkpoint_count: int
    as_of: datetime
    summary_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "generation_id", _uuid(self.generation_id, "generation_id"))
        object.__setattr__(self, "consumer_name", _text(self.consumer_name, "consumer_name"))
        object.__setattr__(self, "build_fingerprint", _text(self.build_fingerprint, "build_fingerprint", max_length=64))
        if len(self.build_fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in self.build_fingerprint):
            raise ReadonlyProjectionSummaryError("build_fingerprint must be lowercase SHA-256")
        object.__setattr__(self, "state", _text(self.state, "state", max_length=16))
        if self.state not in {"BUILDING", "READY", "FAILED"}:
            raise ReadonlyProjectionSummaryError("state is not canonical")
        source = _non_negative(self.source_high_watermark, "source_high_watermark")
        processed = _watermark(self.processed_high_watermark, "processed_high_watermark")
        expected = _non_negative(self.expected_event_count, "expected_event_count")
        applied = _non_negative(self.applied_event_count, "applied_event_count")
        if processed > source:
            raise ReadonlyProjectionSummaryError("processed watermark exceeds source watermark")
        if applied > expected:
            raise ReadonlyProjectionSummaryError("applied event count exceeds expected count")
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        object.__setattr__(self, "checkpoint_count", _non_negative(self.checkpoint_count, "checkpoint_count"))
        material = {
            "version": READONLY_PROJECTION_SUMMARY_VERSION,
            "generation_id": self.generation_id,
            "consumer_name": self.consumer_name,
            "build_fingerprint": self.build_fingerprint,
            "state": self.state,
            "source_high_watermark": source,
            "processed_high_watermark": processed,
            "expected_event_count": expected,
            "applied_event_count": applied,
            "checkpoint_count": self.checkpoint_count,
            "as_of": self.as_of.isoformat(),
        }
        object.__setattr__(self, "summary_fingerprint", hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": READONLY_PROJECTION_SUMMARY_VERSION,
            "generation_id": self.generation_id,
            "consumer_name": self.consumer_name,
            "build_fingerprint": self.build_fingerprint,
            "state": self.state,
            "source_high_watermark": self.source_high_watermark,
            "processed_high_watermark": self.processed_high_watermark,
            "expected_event_count": self.expected_event_count,
            "applied_event_count": self.applied_event_count,
            "checkpoint_count": self.checkpoint_count,
            "as_of": self.as_of.isoformat(),
            "summary_fingerprint": self.summary_fingerprint,
            "live_enabled": False,
        }


__all__ = [
    "READONLY_PROJECTION_SUMMARY_VERSION",
    "ReadonlyProjectionGenerationSummary",
    "ReadonlyProjectionSummaryError",
]
