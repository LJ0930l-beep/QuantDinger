"""Pure read-surface selection policy for a future cutover.

The policy only selects between already-built read facts. It does not fetch
data, mutate a checkpoint, create a connection, or change trading behavior.
Candidate reads require a READY view; stale/unauthorized states fail closed
unless an explicit legacy fallback policy is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.readonly_quant_state_contracts import ReadonlyQuantStateView, ReadonlyViewStatus


READONLY_CUTOVER_CONTRACT_VERSION = "readonly-cutover-v1"


class ReadonlyCutoverError(ValueError):
    """Invalid or unsafe read-surface selection facts."""


class ReadSurface(str, Enum):
    CANDIDATE = "CANDIDATE"
    LEGACY = "LEGACY"
    UNAVAILABLE = "UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"


@dataclass(frozen=True, slots=True)
class ReadonlyCutoverPolicy:
    candidate_enabled: bool = False
    allow_legacy_fallback: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_enabled, bool) or not isinstance(self.allow_legacy_fallback, bool):
            raise ReadonlyCutoverError("cutover policy flags must be boolean")


@dataclass(frozen=True, slots=True)
class ReadSurfaceSelection:
    surface: ReadSurface
    reason: str
    view_fingerprint: str | None = None


def select_read_surface(view: ReadonlyQuantStateView, policy: ReadonlyCutoverPolicy) -> ReadSurfaceSelection:
    """Select a read surface without silently treating stale data as ready."""

    if not isinstance(view, ReadonlyQuantStateView) or not isinstance(policy, ReadonlyCutoverPolicy):
        raise ReadonlyCutoverError("typed view and policy are required")
    if view.status is ReadonlyViewStatus.UNAUTHORIZED:
        return ReadSurfaceSelection(ReadSurface.UNAUTHORIZED, "authorization_required")
    if view.status is ReadonlyViewStatus.UNAVAILABLE:
        return ReadSurfaceSelection(ReadSurface.UNAVAILABLE, "candidate_unavailable")
    if view.status is ReadonlyViewStatus.READY:
        if policy.candidate_enabled:
            return ReadSurfaceSelection(ReadSurface.CANDIDATE, "candidate_ready", view.view_fingerprint)
        return ReadSurfaceSelection(ReadSurface.LEGACY, "candidate_disabled", view.view_fingerprint)
    if view.status is ReadonlyViewStatus.STALE:
        if policy.allow_legacy_fallback:
            return ReadSurfaceSelection(ReadSurface.LEGACY, "candidate_stale", view.view_fingerprint)
        return ReadSurfaceSelection(ReadSurface.UNAVAILABLE, "candidate_stale_no_fallback", view.view_fingerprint)
    raise ReadonlyCutoverError("unknown read view status")


__all__ = ["READONLY_CUTOVER_CONTRACT_VERSION", "ReadonlyCutoverError", "ReadonlyCutoverPolicy", "ReadSurface", "ReadSurfaceSelection", "select_read_surface"]
