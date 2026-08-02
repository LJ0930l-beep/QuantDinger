"""Read-only adapter for an injected non-live run manifest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.non_live_run_manifest_contracts import NonLiveRunManifest


class NonLiveRunManifestServiceError(RuntimeError):
    """A manifest provider is unavailable or returned unsafe facts."""


ManifestProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class NonLiveRunManifestService:
    provider: Optional[ManifestProvider] = None

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise NonLiveRunManifestServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            manifest = self.provider()
        except Exception as exc:
            raise NonLiveRunManifestServiceError("manifest provider failed") from exc
        if not isinstance(manifest, NonLiveRunManifest):
            raise NonLiveRunManifestServiceError("provider returned invalid non-live manifest")
        return 200, manifest.to_public_dict()


def service_from_app(app) -> NonLiveRunManifestService:
    return NonLiveRunManifestService(app.extensions.get("readonly_non_live_run_manifest_provider"))


__all__ = ["NonLiveRunManifestService", "NonLiveRunManifestServiceError", "service_from_app"]
