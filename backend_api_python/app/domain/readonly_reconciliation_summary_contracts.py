"""Typed, scoped read-only reconciliation checkpoint summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from app.domain.order_contracts import (
    ReconciliationCheckpointStatus,
    ReconciliationHealth,
    derive_reconciliation_health,
)


READONLY_RECONCILIATION_SUMMARY_VERSION = "readonly-reconciliation-summary-v1"


class ReadonlyReconciliationSummaryError(ValueError):
    """Persisted reconciliation facts are incomplete or out of scope."""


def _uuid(value: object, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value))).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReadonlyReconciliationSummaryError(f"{field_name} must be a UUID") from exc


def _scope_text(value: object, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or not value.isascii():
        raise ReadonlyReconciliationSummaryError(f"{field_name} must be canonical ASCII text")
    if not allow_empty and not value:
        raise ReadonlyReconciliationSummaryError(f"{field_name} must not be empty")
    if len(value) > 160:
        raise ReadonlyReconciliationSummaryError(f"{field_name} is too long")
    return value


def _venue(value: object, field_name: str) -> str:
    text = _scope_text(value, field_name).lower()
    if text != value:
        raise ReadonlyReconciliationSummaryError(f"{field_name} must be lowercase")
    return text


def _non_negative(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReadonlyReconciliationSummaryError(f"{field_name} must be non-negative")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyReconciliationSummaryError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReadonlyReconciliationCheckpointSummary:
    checkpoint_id: str
    credential_id: int
    exchange: str
    market_type: str
    account_scope: str
    instrument_id: str
    status: ReconciliationCheckpointStatus | str
    unresolved_count: int
    version: int
    last_success_at: datetime | None
    sla_deadline: datetime | None
    updated_at: datetime
    as_of: datetime
    summary_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkpoint_id", _uuid(self.checkpoint_id, "checkpoint_id"))
        if isinstance(self.credential_id, bool) or not isinstance(self.credential_id, int) or self.credential_id <= 0:
            raise ReadonlyReconciliationSummaryError("credential_id must be a positive integer")
        object.__setattr__(self, "exchange", _venue(self.exchange, "exchange"))
        object.__setattr__(self, "market_type", _venue(self.market_type, "market_type"))
        object.__setattr__(self, "account_scope", _scope_text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _scope_text(self.instrument_id, "instrument_id", allow_empty=True))
        try:
            status = ReconciliationCheckpointStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ReadonlyReconciliationSummaryError("status is not canonical") from exc
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "unresolved_count", _non_negative(self.unresolved_count, "unresolved_count"))
        object.__setattr__(self, "version", _non_negative(self.version, "version"))
        for name in ("last_success_at", "sla_deadline"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value, name))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        as_of = _utc(self.as_of, "as_of")
        object.__setattr__(self, "as_of", as_of)
        expired = self.sla_deadline is not None and self.sla_deadline <= as_of
        health = derive_reconciliation_health(status, sla_expired=expired)
        material = {
            "version": READONLY_RECONCILIATION_SUMMARY_VERSION,
            "checkpoint_id": self.checkpoint_id,
            "credential_id": self.credential_id,
            "exchange": self.exchange,
            "market_type": self.market_type,
            "account_scope": self.account_scope,
            "instrument_id": self.instrument_id,
            "status": status.value,
            "derived_health": health.value,
            "unresolved_count": self.unresolved_count,
            "version_number": self.version,
            "last_success_at": None if self.last_success_at is None else self.last_success_at.isoformat(),
            "sla_deadline": None if self.sla_deadline is None else self.sla_deadline.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "as_of": as_of.isoformat(),
        }
        object.__setattr__(self, "summary_fingerprint", hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest())

    @property
    def derived_health(self) -> ReconciliationHealth:
        expired = self.sla_deadline is not None and self.sla_deadline <= self.as_of
        return derive_reconciliation_health(self.status, sla_expired=expired)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": READONLY_RECONCILIATION_SUMMARY_VERSION,
            "checkpoint_id": self.checkpoint_id,
            "credential_id": self.credential_id,
            "exchange": self.exchange,
            "market_type": self.market_type,
            "account_scope": self.account_scope,
            "instrument_id": self.instrument_id,
            "checkpoint_status": self.status.value,
            "derived_health": self.derived_health.value,
            "unresolved_count": self.unresolved_count,
            "version": self.version,
            "last_success_at": None if self.last_success_at is None else self.last_success_at.isoformat(),
            "sla_deadline": None if self.sla_deadline is None else self.sla_deadline.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "as_of": self.as_of.isoformat(),
            "summary_fingerprint": self.summary_fingerprint,
            "live_enabled": False,
        }


__all__ = [
    "READONLY_RECONCILIATION_SUMMARY_VERSION",
    "ReadonlyReconciliationCheckpointSummary",
    "ReadonlyReconciliationSummaryError",
]
