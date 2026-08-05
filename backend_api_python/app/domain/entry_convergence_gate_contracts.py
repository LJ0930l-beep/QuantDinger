"""Pure SC-13 entry-convergence gate contracts.

This boundary validates source, actor and safe mode before any persistence call.
It performs no I/O and has no executor, exchange, worker or live dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.canonical_entry_contracts import EntryMode, EntrySource
from app.domain.canonical_entry_v2_contracts import CanonicalEntryRequestV2


ENTRY_CONVERGENCE_CONTRACT_VERSION = "entry-convergence-v1"


class EntryConvergenceError(ValueError):
    """Typed fail-closed error for an entry surface outside its contract."""


class EntrySurfaceDisposition(str, Enum):
    DISABLED = "DISABLED"
    CANONICAL_ONLY = "CANONICAL_ONLY"
    ADMISSION_REQUIRED = "ADMISSION_REQUIRED"


@dataclass(frozen=True, slots=True)
class EntrySurfacePolicy:
    source: EntrySource
    default_mode: EntryMode
    allowed_modes: tuple[EntryMode, ...]
    disposition: EntrySurfaceDisposition

    def __post_init__(self) -> None:
        if not isinstance(self.source, EntrySource) or not isinstance(self.default_mode, EntryMode):
            raise EntryConvergenceError("entry surface policy requires typed source and mode")
        if not self.allowed_modes or any(not isinstance(mode, EntryMode) for mode in self.allowed_modes):
            raise EntryConvergenceError("allowed_modes must use EntryMode")
        if any(mode.value == "LIVE" for mode in self.allowed_modes):
            raise EntryConvergenceError("LIVE is not an allowed entry mode")
        if not isinstance(self.disposition, EntrySurfaceDisposition):
            raise EntryConvergenceError("disposition must be typed")


@dataclass(frozen=True, slots=True)
class EntrySurfaceDecision:
    policy: EntrySurfacePolicy
    mode: EntryMode
    request: CanonicalEntryRequestV2
    disposition: EntrySurfaceDisposition
    admission_required: bool


_SAFE_MODES = (EntryMode.DISABLED, EntryMode.PAPER, EntryMode.SHADOW)
_RESTRICTED = frozenset({EntrySource.AGENT, EntrySource.MCP, EntrySource.GRID})


def _policy(source: EntrySource) -> EntrySurfacePolicy:
    if source in _RESTRICTED:
        return EntrySurfacePolicy(source, EntryMode.DISABLED, _SAFE_MODES, EntrySurfaceDisposition.CANONICAL_ONLY)
    return EntrySurfacePolicy(source, EntryMode.PAPER, _SAFE_MODES, EntrySurfaceDisposition.ADMISSION_REQUIRED)


def validate_entry_surface(source: EntrySource, mode: EntryMode, request: CanonicalEntryRequestV2) -> EntrySurfaceDecision:
    """Validate source, mode, and canonical request before persistence."""

    if not isinstance(source, EntrySource) or not isinstance(mode, EntryMode):
        raise EntryConvergenceError("source and mode must use canonical enums")
    if not isinstance(request, CanonicalEntryRequestV2):
        raise EntryConvergenceError("request must use CanonicalEntryRequestV2")
    if request.actor.entry_source is not source:
        raise EntryConvergenceError("request source does not match entry surface")
    policy = _policy(source)
    if mode not in policy.allowed_modes:
        raise EntryConvergenceError("entry mode is not allowed")
    disposition = EntrySurfaceDisposition.DISABLED if mode is EntryMode.DISABLED else policy.disposition
    return EntrySurfaceDecision(policy, mode, request, disposition, disposition is EntrySurfaceDisposition.ADMISSION_REQUIRED)


def default_entry_surface_policy(source: EntrySource) -> EntrySurfacePolicy:
    if not isinstance(source, EntrySource):
        raise EntryConvergenceError("source must use EntrySource")
    return _policy(source)


__all__ = [
    "ENTRY_CONVERGENCE_CONTRACT_VERSION", "EntryConvergenceError", "EntrySurfaceDecision",
    "EntrySurfaceDisposition", "EntrySurfacePolicy", "default_entry_surface_policy", "validate_entry_surface",
]
