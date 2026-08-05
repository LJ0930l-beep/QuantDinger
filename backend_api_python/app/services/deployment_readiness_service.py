"""Read-only adapter for deployment and rollback readiness evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.deployment_readiness_contracts import (
    DeploymentReadinessStatus,
    DeploymentReleaseProfile,
)


class DeploymentReadinessServiceError(RuntimeError):
    """Deployment evidence is unavailable or not typed."""


DeploymentProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class DeploymentReadinessService:
    provider: Optional[DeploymentProvider] = None

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise DeploymentReadinessServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            value = self.provider()
        except Exception as exc:
            raise DeploymentReadinessServiceError("deployment provider failed") from exc
        if not isinstance(value, tuple) or len(value) != 2:
            raise DeploymentReadinessServiceError("provider must return profile and readiness")
        profile, status = value
        if not isinstance(profile, DeploymentReleaseProfile) or not isinstance(status, DeploymentReadinessStatus):
            raise DeploymentReadinessServiceError("provider returned untyped deployment facts")
        body = profile.to_public_dict()
        body["status"] = status.value
        return 200, body


def service_from_app(app) -> DeploymentReadinessService:
    return DeploymentReadinessService(app.extensions.get("readonly_deployment_readiness_provider"))


__all__ = ["DeploymentReadinessService", "DeploymentReadinessServiceError", "service_from_app"]
