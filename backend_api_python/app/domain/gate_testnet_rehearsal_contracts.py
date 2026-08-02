"""Immutable evidence for a Gate TestNet read-only rehearsal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json


GATE_TESTNET_REHEARSAL_CONTRACT_VERSION = "gate-testnet-rehearsal-v1"


class GateTestnetRehearsalError(ValueError):
    """Invalid or incomplete rehearsal evidence."""


class GateTestnetRehearsalStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateTestnetRehearsalError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class GateTestnetRehearsalSnapshot:
    snapshot_id: str
    session_fingerprint: str
    instrument_id: str
    observed_at: datetime
    dataset_fingerprint: str

    def __post_init__(self) -> None:
        for field_name in ("snapshot_id", "session_fingerprint", "instrument_id", "dataset_fingerprint"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value.strip() != value or not value.isascii():
                raise GateTestnetRehearsalError(f"{field_name} must be canonical ASCII text")
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))


@dataclass(frozen=True, slots=True)
class GateTestnetRehearsalResult:
    status: GateTestnetRehearsalStatus
    snapshots: tuple[GateTestnetRehearsalSnapshot, ...] = ()
    reason: str = ""
    rehearsal_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, GateTestnetRehearsalStatus):
            raise GateTestnetRehearsalError("status must be typed")
        if not isinstance(self.snapshots, tuple) or any(not isinstance(item, GateTestnetRehearsalSnapshot) for item in self.snapshots):
            raise GateTestnetRehearsalError("snapshots must be typed")
        if len({item.snapshot_id for item in self.snapshots}) != len(self.snapshots):
            raise GateTestnetRehearsalError("snapshot ids must be unique")
        if self.status is GateTestnetRehearsalStatus.READY and not self.snapshots:
            raise GateTestnetRehearsalError("READY rehearsal requires snapshots")
        if self.status is not GateTestnetRehearsalStatus.READY and self.snapshots:
            raise GateTestnetRehearsalError("blocked/failed rehearsal cannot expose snapshots")
        if not isinstance(self.reason, str) or self.reason.strip() != self.reason:
            raise GateTestnetRehearsalError("reason must be canonical text")
        material = {
            "version": GATE_TESTNET_REHEARSAL_CONTRACT_VERSION,
            "status": self.status.value,
            "snapshots": [
                {"id": item.snapshot_id, "session": item.session_fingerprint, "instrument": item.instrument_id,
                 "observed_at": item.observed_at.isoformat(), "dataset": item.dataset_fingerprint}
                for item in self.snapshots
            ],
            "reason": self.reason,
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "rehearsal_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": GATE_TESTNET_REHEARSAL_CONTRACT_VERSION,
            "status": self.status.value,
            "reason": self.reason,
            "rehearsal_fingerprint": self.rehearsal_fingerprint,
            "snapshot_count": len(self.snapshots),
            "snapshots": [
                {"snapshot_id": item.snapshot_id, "instrument_id": item.instrument_id,
                 "observed_at": item.observed_at.isoformat(), "dataset_fingerprint": item.dataset_fingerprint}
                for item in self.snapshots
            ],
            "live_enabled": False,
        }


__all__ = [
    "GATE_TESTNET_REHEARSAL_CONTRACT_VERSION",
    "GateTestnetRehearsalError",
    "GateTestnetRehearsalResult",
    "GateTestnetRehearsalSnapshot",
    "GateTestnetRehearsalStatus",
]
