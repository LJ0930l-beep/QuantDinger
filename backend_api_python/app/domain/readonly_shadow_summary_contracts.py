"""Typed, scope-bound read-only summaries for persisted Shadow Diff runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import UUID


READONLY_SHADOW_SUMMARY_VERSION = "readonly-shadow-summary-v1"


class ReadonlyShadowSummaryError(ValueError):
    """Persisted Shadow Diff facts are incomplete or out of scope."""


def _uuid(value: object, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value))).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReadonlyShadowSummaryError(f"{field_name} must be a UUID") from exc


def _text(value: object, field_name: str, *, lower: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or not value or not value.isascii() or any(ch.isspace() for ch in value):
        raise ReadonlyShadowSummaryError(f"{field_name} must be canonical ASCII text")
    if len(value) > 160:
        raise ReadonlyShadowSummaryError(f"{field_name} is too long")
    if lower and value != value.lower():
        raise ReadonlyShadowSummaryError(f"{field_name} must be lowercase")
    return value


def _sha(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ReadonlyShadowSummaryError(f"{field_name} must be lowercase SHA-256")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ReadonlyShadowSummaryError(f"{field_name} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReadonlyShadowSummaryError(f"{field_name} must be Decimal") from exc
    if not result.is_finite() or result < 0:
        raise ReadonlyShadowSummaryError(f"{field_name} must be non-negative and finite")
    return result


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReadonlyShadowSummaryError(f"{field_name} must be non-negative")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyShadowSummaryError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReadonlyShadowComparisonSummary:
    run_id: str
    credential_id: int
    exchange: str
    market_type: str
    account_scope: str
    instrument_id: str
    candidate_generation_id: str
    candidate_consumer_name: str
    candidate_generation_build_fingerprint: str
    candidate_checkpoint_watermark: int
    as_of: datetime
    tolerance_policy_version: str
    quantity_absolute: Decimal
    quantity_relative: Decimal
    monetary_absolute: Decimal
    monetary_relative: Decimal
    ratio_absolute: Decimal
    tolerance_policy_fingerprint: str
    build_fingerprint: str
    replay_fingerprint: str
    completed_at: datetime
    diff_count: int
    blocking_diff_count: int
    summary_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        object.__setattr__(self, "candidate_generation_id", _uuid(self.candidate_generation_id, "candidate_generation_id"))
        if isinstance(self.credential_id, bool) or not isinstance(self.credential_id, int) or self.credential_id <= 0:
            raise ReadonlyShadowSummaryError("credential_id must be a positive integer")
        object.__setattr__(self, "exchange", _text(self.exchange, "exchange", lower=True))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lower=True))
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "candidate_consumer_name", _text(self.candidate_consumer_name, "candidate_consumer_name"))
        object.__setattr__(self, "candidate_generation_build_fingerprint", _sha(self.candidate_generation_build_fingerprint, "candidate_generation_build_fingerprint"))
        if isinstance(self.candidate_checkpoint_watermark, bool) or not isinstance(self.candidate_checkpoint_watermark, int) or self.candidate_checkpoint_watermark < 0:
            raise ReadonlyShadowSummaryError("candidate_checkpoint_watermark must be non-negative")
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        object.__setattr__(self, "completed_at", _utc(self.completed_at, "completed_at"))
        object.__setattr__(self, "tolerance_policy_version", _text(self.tolerance_policy_version, "tolerance_policy_version"))
        for name in ("quantity_absolute", "quantity_relative", "monetary_absolute", "monetary_relative", "ratio_absolute"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        for name in ("tolerance_policy_fingerprint", "build_fingerprint", "replay_fingerprint"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        object.__setattr__(self, "diff_count", _count(self.diff_count, "diff_count"))
        object.__setattr__(self, "blocking_diff_count", _count(self.blocking_diff_count, "blocking_diff_count"))
        if self.blocking_diff_count > self.diff_count:
            raise ReadonlyShadowSummaryError("blocking_diff_count cannot exceed diff_count")
        material = {
            "version": READONLY_SHADOW_SUMMARY_VERSION,
            "run_id": self.run_id,
            "credential_id": self.credential_id,
            "exchange": self.exchange,
            "market_type": self.market_type,
            "account_scope": self.account_scope,
            "instrument_id": self.instrument_id,
            "candidate_generation_id": self.candidate_generation_id,
            "candidate_consumer_name": self.candidate_consumer_name,
            "candidate_generation_build_fingerprint": self.candidate_generation_build_fingerprint,
            "candidate_checkpoint_watermark": self.candidate_checkpoint_watermark,
            "as_of": self.as_of.isoformat(),
            "tolerance_policy_version": self.tolerance_policy_version,
            "quantity_absolute": format(self.quantity_absolute.normalize(), "f"),
            "quantity_relative": format(self.quantity_relative.normalize(), "f"),
            "monetary_absolute": format(self.monetary_absolute.normalize(), "f"),
            "monetary_relative": format(self.monetary_relative.normalize(), "f"),
            "ratio_absolute": format(self.ratio_absolute.normalize(), "f"),
            "tolerance_policy_fingerprint": self.tolerance_policy_fingerprint,
            "build_fingerprint": self.build_fingerprint,
            "replay_fingerprint": self.replay_fingerprint,
            "completed_at": self.completed_at.isoformat(),
            "diff_count": self.diff_count,
            "blocking_diff_count": self.blocking_diff_count,
        }
        object.__setattr__(self, "summary_fingerprint", hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest())

    @property
    def match_status(self) -> str:
        if self.blocking_diff_count:
            return "BLOCKING"
        return "MATCH" if self.diff_count == 0 else "MISMATCH"

    def to_public_dict(self) -> dict[str, object]:
        def dec(value: Decimal) -> str:
            return format(value.normalize(), "f")

        return {
            "contract_version": READONLY_SHADOW_SUMMARY_VERSION,
            "run_id": self.run_id,
            "credential_id": self.credential_id,
            "exchange": self.exchange,
            "market_type": self.market_type,
            "account_scope": self.account_scope,
            "instrument_id": self.instrument_id,
            "candidate_generation_id": self.candidate_generation_id,
            "candidate_consumer_name": self.candidate_consumer_name,
            "candidate_checkpoint_watermark": self.candidate_checkpoint_watermark,
            "as_of": self.as_of.isoformat(),
            "tolerance_policy_version": self.tolerance_policy_version,
            "quantity_absolute": dec(self.quantity_absolute),
            "quantity_relative": dec(self.quantity_relative),
            "monetary_absolute": dec(self.monetary_absolute),
            "monetary_relative": dec(self.monetary_relative),
            "ratio_absolute": dec(self.ratio_absolute),
            "tolerance_policy_fingerprint": self.tolerance_policy_fingerprint,
            "build_fingerprint": self.build_fingerprint,
            "replay_fingerprint": self.replay_fingerprint,
            "completed_at": self.completed_at.isoformat(),
            "diff_count": self.diff_count,
            "blocking_diff_count": self.blocking_diff_count,
            "match_status": self.match_status,
            "summary_fingerprint": self.summary_fingerprint,
            "live_enabled": False,
        }


__all__ = ["READONLY_SHADOW_SUMMARY_VERSION", "ReadonlyShadowComparisonSummary", "ReadonlyShadowSummaryError"]
