"""Load a reviewed deployment/rollback evidence artifact without side effects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.deployment_readiness_contracts import (
    DEPLOYMENT_READINESS_CONTRACT_VERSION,
    DeploymentEnvironment,
    DeploymentReadinessError,
    DeploymentReleaseProfile,
    derive_deployment_readiness,
)
from app.domain.production_readiness_contracts import ProductionReadinessStatus


class DeploymentReadinessArtifactError(ValueError):
    """Evidence file is missing, unsafe, or malformed."""


_MAX_BYTES = 1024 * 1024
_SENSITIVE_KEYS = {"api_key", "apikey", "secret", "api_secret", "password", "token", "private_key"}


def _reject_sensitive_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _SENSITIVE_KEYS or any(part in str(key).lower() for part in ("secret", "password", "private_key")):
                raise DeploymentReadinessArtifactError("deployment evidence contains a sensitive field")
            _reject_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_keys(child)


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower() or any(ch not in "0123456789abcdef" for ch in value):
        raise DeploymentReadinessArtifactError(f"{name} must be a lowercase SHA-256")
    return value


def load_deployment_readiness_artifact(path: str | Path) -> tuple[DeploymentReleaseProfile, object]:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise DeploymentReadinessArtifactError("deployment evidence path must be absolute")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DeploymentReadinessArtifactError("deployment evidence is unavailable") from exc
    if len(raw) > _MAX_BYTES:
        raise DeploymentReadinessArtifactError("deployment evidence is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentReadinessArtifactError("deployment evidence is not valid JSON") from exc
    _reject_sensitive_keys(payload)
    if not isinstance(payload, dict) or payload.get("contract_version") != DEPLOYMENT_READINESS_CONTRACT_VERSION:
        raise DeploymentReadinessArtifactError("unsupported deployment evidence contract")
    profile_payload = payload.get("profile")
    if not isinstance(profile_payload, dict):
        raise DeploymentReadinessArtifactError("deployment profile is missing")
    if profile_payload.get("live_enabled") is not False:
        raise DeploymentReadinessArtifactError("deployment evidence cannot enable live trading")
    try:
        profile = DeploymentReleaseProfile(
            release_id=profile_payload["release_id"],
            environment=DeploymentEnvironment(profile_payload["environment"]),
            artifact_digest=_sha(profile_payload["artifact_digest"], "artifact_digest"),
            schema_fingerprint=_sha(profile_payload["schema_fingerprint"], "schema_fingerprint"),
            config_fingerprint=_sha(profile_payload["config_fingerprint"], "config_fingerprint"),
            rollback_release_id=profile_payload["rollback_release_id"],
            rollback_verified=profile_payload["rollback_verified"],
            live_enabled=False,
        )
    except (KeyError, TypeError, ValueError, DeploymentReadinessError) as exc:
        raise DeploymentReadinessArtifactError("deployment profile is invalid") from exc
    expected_fingerprint = profile_payload.get("deployment_fingerprint")
    if expected_fingerprint != profile.deployment_fingerprint:
        raise DeploymentReadinessArtifactError("deployment fingerprint mismatch")
    try:
        production_status = ProductionReadinessStatus(payload["production_status"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DeploymentReadinessArtifactError("production readiness status is invalid") from exc
    return profile, derive_deployment_readiness(profile, production_status)


def provider_from_path(path: str | Path):
    def provider():
        return load_deployment_readiness_artifact(path)

    return provider


__all__ = ["DeploymentReadinessArtifactError", "load_deployment_readiness_artifact", "provider_from_path"]
