"""Immutable audit manifest for a non-live research/testnet run.

The manifest is an evidence record, not an execution permission.  It is
explicitly restricted to Gate TestNet read-only plus PAPER/SHADOW modes and
cannot carry a network-write or live authorization flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from .gate_readonly_contracts import GateEnvironment
from .paper_shadow_contracts import SimulationMode


NON_LIVE_RUN_MANIFEST_VERSION = "non-live-run-manifest-v1"


class NonLiveRunManifestError(ValueError):
    """Run evidence is incomplete, non-canonical, or attempts live access."""


class NonLiveRunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise NonLiveRunManifestError(f"{field_name} must be canonical ASCII text")
    return value


def _sha(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or value != value.lower() or any(ch not in "0123456789abcdef" for ch in value):
        raise NonLiveRunManifestError(f"{field_name} must be lowercase SHA-256")
    return value


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise NonLiveRunManifestError("observed_at must use a zero-offset UTC datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class NonLiveRunManifest:
    run_id: str
    environment: GateEnvironment
    mode: SimulationMode
    status: NonLiveRunStatus
    input_fingerprint: str
    dataset_fingerprint: str | None
    pipeline_fingerprint: str | None
    observed_at: datetime
    reason: str = ""
    network_access: bool = False
    live_enabled: bool = False
    manifest_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        if self.environment is not GateEnvironment.TESTNET:
            raise NonLiveRunManifestError("manifest environment must be Gate TESTNET")
        if self.mode not in (SimulationMode.PAPER, SimulationMode.SHADOW):
            raise NonLiveRunManifestError("manifest mode must be PAPER or SHADOW")
        if not isinstance(self.status, NonLiveRunStatus):
            raise NonLiveRunManifestError("status must be typed")
        _sha(self.input_fingerprint, "input_fingerprint")
        object.__setattr__(self, "dataset_fingerprint", _sha(self.dataset_fingerprint, "dataset_fingerprint", optional=True))
        object.__setattr__(self, "pipeline_fingerprint", _sha(self.pipeline_fingerprint, "pipeline_fingerprint", optional=True))
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if not isinstance(self.reason, str) or self.reason.strip() != self.reason or any(ord(ch) < 32 for ch in self.reason):
            raise NonLiveRunManifestError("reason must be canonical text")
        if not isinstance(self.network_access, bool) or self.network_access:
            raise NonLiveRunManifestError("non-live manifest cannot authorize network access")
        if not isinstance(self.live_enabled, bool) or self.live_enabled:
            raise NonLiveRunManifestError("non-live manifest cannot authorize live trading")
        if self.status is NonLiveRunStatus.COMPLETED:
            if self.dataset_fingerprint is None or self.pipeline_fingerprint is None:
                raise NonLiveRunManifestError("completed run requires dataset and pipeline fingerprints")
            if not self.reason:
                raise NonLiveRunManifestError("completed run requires a reason")
        elif not self.reason:
            raise NonLiveRunManifestError("blocked/failed run requires a reason")
        material = {
            "version": NON_LIVE_RUN_MANIFEST_VERSION,
            "run_id": self.run_id,
            "environment": self.environment.value,
            "mode": self.mode.value,
            "status": self.status.value,
            "input_fingerprint": self.input_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "pipeline_fingerprint": self.pipeline_fingerprint,
            "observed_at": self.observed_at.isoformat(),
            "reason": self.reason,
            "network_access": False,
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "manifest_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "contract_version": NON_LIVE_RUN_MANIFEST_VERSION,
            "run_id": self.run_id,
            "environment": self.environment.value,
            "mode": self.mode.value,
            "status": self.status.value,
            "input_fingerprint": self.input_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "pipeline_fingerprint": self.pipeline_fingerprint,
            "observed_at": self.observed_at.isoformat(),
            "reason": self.reason,
            "network_access": False,
            "live_enabled": False,
            "manifest_fingerprint": self.manifest_fingerprint,
        }


def input_fingerprint(material: object) -> str:
    """Hash canonical run inputs without including credentials or time-now."""

    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "NON_LIVE_RUN_MANIFEST_VERSION",
    "NonLiveRunManifestError",
    "NonLiveRunStatus",
    "NonLiveRunManifest",
    "input_fingerprint",
]
