"""Fail-closed deployment and rollback evidence for the non-live product.

This contract describes whether a reviewed artifact is prepared for a stage;
it never enables that stage, performs a deployment, or authorizes live orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any

from .production_readiness_contracts import ProductionReadinessStatus


DEPLOYMENT_READINESS_CONTRACT_VERSION = "deployment-readiness-v1"


class DeploymentReadinessError(ValueError):
    """Artifact or rollback evidence is invalid or unsafe."""


class DeploymentEnvironment(str, Enum):
    TESTNET = "TESTNET"
    STAGING = "STAGING"
    CANARY = "CANARY"
    PRODUCTION = "PRODUCTION"


class DeploymentReadinessStatus(str, Enum):
    BLOCKED = "BLOCKED"
    TESTNET_READY = "TESTNET_READY"
    CANARY_READY = "CANARY_READY"
    PRODUCTION_READY = "PRODUCTION_READY"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise DeploymentReadinessError(f"{field_name} must be canonical ASCII text")
    return value


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower() or any(ch not in "0123456789abcdef" for ch in value):
        raise DeploymentReadinessError(f"{field_name} must be lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class DeploymentReleaseProfile:
    release_id: str
    environment: DeploymentEnvironment
    artifact_digest: str
    schema_fingerprint: str
    config_fingerprint: str
    rollback_release_id: str
    rollback_verified: bool
    live_enabled: bool = False
    deployment_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.release_id, "release_id")
        if not isinstance(self.environment, DeploymentEnvironment):
            raise DeploymentReadinessError("environment must be typed")
        _sha(self.artifact_digest, "artifact_digest")
        _sha(self.schema_fingerprint, "schema_fingerprint")
        _sha(self.config_fingerprint, "config_fingerprint")
        _text(self.rollback_release_id, "rollback_release_id")
        if not isinstance(self.rollback_verified, bool):
            raise DeploymentReadinessError("rollback_verified must be boolean")
        if not isinstance(self.live_enabled, bool) or self.live_enabled:
            raise DeploymentReadinessError("deployment profile cannot enable live trading")
        material = {
            "version": DEPLOYMENT_READINESS_CONTRACT_VERSION,
            "release_id": self.release_id,
            "environment": self.environment.value,
            "artifact_digest": self.artifact_digest,
            "schema_fingerprint": self.schema_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "rollback_release_id": self.rollback_release_id,
            "rollback_verified": self.rollback_verified,
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "deployment_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "contract_version": DEPLOYMENT_READINESS_CONTRACT_VERSION,
            "release_id": self.release_id,
            "environment": self.environment.value,
            "artifact_digest": self.artifact_digest,
            "schema_fingerprint": self.schema_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "rollback_release_id": self.rollback_release_id,
            "rollback_verified": self.rollback_verified,
            "deployment_fingerprint": self.deployment_fingerprint,
            "live_enabled": False,
        }


def derive_deployment_readiness(
    profile: DeploymentReleaseProfile,
    production_status: ProductionReadinessStatus,
) -> DeploymentReadinessStatus:
    if not isinstance(profile, DeploymentReleaseProfile):
        raise DeploymentReadinessError("typed deployment profile is required")
    if not isinstance(production_status, ProductionReadinessStatus):
        raise DeploymentReadinessError("typed production readiness status is required")
    if production_status is ProductionReadinessStatus.BLOCKED:
        return DeploymentReadinessStatus.BLOCKED
    if profile.environment in (DeploymentEnvironment.TESTNET, DeploymentEnvironment.STAGING):
        return DeploymentReadinessStatus.TESTNET_READY
    if not profile.rollback_verified:
        return DeploymentReadinessStatus.BLOCKED
    if profile.environment is DeploymentEnvironment.CANARY:
        return DeploymentReadinessStatus.CANARY_READY
    if production_status is ProductionReadinessStatus.PRODUCTION_READY:
        return DeploymentReadinessStatus.PRODUCTION_READY
    return DeploymentReadinessStatus.CANARY_READY


__all__ = [
    "DEPLOYMENT_READINESS_CONTRACT_VERSION",
    "DeploymentReadinessError",
    "DeploymentEnvironment",
    "DeploymentReadinessStatus",
    "DeploymentReleaseProfile",
    "derive_deployment_readiness",
]
