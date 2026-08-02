"""Read a sanitized Gate TestNet rehearsal artifact.

The artifact is deliberately a one-way hand-off from the public-read
rehearsal command to the read-only HTTP surface.  This provider never loads
credentials, creates a venue client, opens a socket, or authorizes writes.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from app.domain.gate_testnet_rehearsal_contracts import (
    GateTestnetRehearsalError,
    GateTestnetRehearsalResult,
    GateTestnetRehearsalSnapshot,
    GateTestnetRehearsalStatus,
)


MAX_ARTIFACT_BYTES = 1_048_576
_SENSITIVE_KEYS = frozenset({"api_key", "apikey", "secret", "secret_key", "token", "password", "private_key"})


class GateTestnetRehearsalArtifactError(GateTestnetRehearsalError):
    """The supplied file is not a safe canonical rehearsal artifact."""


def _reject_sensitive_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                raise GateTestnetRehearsalArtifactError("rehearsal artifact contains a credential field")
            _reject_sensitive_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_keys(item)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise GateTestnetRehearsalArtifactError(f"{field_name} must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateTestnetRehearsalArtifactError(f"{field_name} is invalid") from exc


def load_gate_testnet_rehearsal_artifact(path: str | Path) -> GateTestnetRehearsalResult:
    """Load and fingerprint-verify one sanitized rehearsal JSON file."""
    try:
        artifact_path = Path(path)
    except (TypeError, ValueError) as exc:
        raise GateTestnetRehearsalArtifactError("artifact path is invalid") from exc
    if not artifact_path.is_absolute():
        raise GateTestnetRehearsalArtifactError("artifact path must be absolute")
    try:
        size = artifact_path.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise GateTestnetRehearsalArtifactError("artifact is too large")
        raw = artifact_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except GateTestnetRehearsalArtifactError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateTestnetRehearsalArtifactError("artifact cannot be read") from exc
    if not isinstance(payload, dict):
        raise GateTestnetRehearsalArtifactError("artifact root must be an object")
    _reject_sensitive_keys(payload)
    if payload.get("contract_version") != "gate-testnet-rehearsal-v1":
        raise GateTestnetRehearsalArtifactError("unsupported rehearsal contract version")
    try:
        status = GateTestnetRehearsalStatus(str(payload["status"]))
        snapshots_payload = payload.get("snapshots", [])
        if not isinstance(snapshots_payload, list):
            raise GateTestnetRehearsalArtifactError("snapshots must be a list")
        snapshots = tuple(
            GateTestnetRehearsalSnapshot(
                str(item["snapshot_id"]),
                str(item["session_fingerprint"]),
                str(item["instrument_id"]),
                _parse_datetime(item["observed_at"], "snapshot observed_at"),
                str(item["dataset_fingerprint"]),
            )
            for item in snapshots_payload
            if isinstance(item, dict)
        )
        if len(snapshots) != len(snapshots_payload):
            raise GateTestnetRehearsalArtifactError("snapshot entries must be objects")
        result = GateTestnetRehearsalResult(status, snapshots, str(payload.get("reason", "")))
    except GateTestnetRehearsalArtifactError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise GateTestnetRehearsalArtifactError("artifact contains invalid rehearsal facts") from exc
    if payload.get("rehearsal_fingerprint") != result.rehearsal_fingerprint:
        raise GateTestnetRehearsalArtifactError("artifact fingerprint does not match facts")
    if payload.get("live_enabled") is not False:
        raise GateTestnetRehearsalArtifactError("rehearsal artifact cannot enable live trading")
    return result


def provider_from_path(path: str | Path):
    """Return a no-argument provider suitable for Flask extensions."""
    artifact_path = Path(path)

    def provider() -> GateTestnetRehearsalResult:
        return load_gate_testnet_rehearsal_artifact(artifact_path)

    return provider


__all__ = [
    "GateTestnetRehearsalArtifactError",
    "MAX_ARTIFACT_BYTES",
    "load_gate_testnet_rehearsal_artifact",
    "provider_from_path",
]
