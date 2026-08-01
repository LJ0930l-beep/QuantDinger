"""Fail-closed contracts for retiring pre-admission trading surfaces.

The retirement record is evidence, not a second execution path.  It makes the
SC-15 boundary machine-readable while the old implementation bodies are
removed in small, reviewable increments.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


LEGACY_RETIREMENT_CONTRACT_VERSION = "legacy-retirement-v1"


class LegacyRetirementError(ValueError):
    """Raised when a legacy surface is not fail-closed."""


class LegacySurface(str, Enum):
    REST_QUICK_TRADE = "REST_QUICK_TRADE"
    ALPACA = "ALPACA"
    IBKR = "IBKR"
    GRID = "GRID"
    STRATEGY_V2_QUEUE = "STRATEGY_V2_QUEUE"
    PROTECTION = "PROTECTION"
    AGENT = "AGENT"
    MCP = "MCP"
    PENDING_WORKER = "PENDING_WORKER"


class LegacySurfaceDisposition(str, Enum):
    RETIRED = "RETIRED"
    DISABLED = "DISABLED"
    ADMISSION_ONLY = "ADMISSION_ONLY"


class FailureDrillKind(str, Enum):
    RESTART = "RESTART"
    ROLLBACK = "ROLLBACK"
    REPLAY = "REPLAY"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    ORPHAN_IDENTITY = "ORPHAN_IDENTITY"


@dataclass(frozen=True, slots=True)
class LegacyRetirementFact:
    surface: LegacySurface
    path: str
    symbol: str
    disposition: LegacySurfaceDisposition
    reachable: bool = False
    creates_order: bool = False
    calls_executor: bool = False
    calls_exchange: bool = False
    writes_legacy_order: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.surface, LegacySurface):
            raise LegacyRetirementError("surface must use LegacySurface")
        if not isinstance(self.disposition, LegacySurfaceDisposition):
            raise LegacyRetirementError("disposition must use LegacySurfaceDisposition")
        for name in ("path", "symbol", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip() or not value.isascii():
                raise LegacyRetirementError(f"{name} must be canonical ASCII text")
        for name in ("reachable", "creates_order", "calls_executor", "calls_exchange", "writes_legacy_order"):
            if not isinstance(getattr(self, name), bool):
                raise LegacyRetirementError(f"{name} must be boolean")
        if self.reachable:
            raise LegacyRetirementError("legacy surface must be unreachable")
        if self.creates_order or self.calls_executor or self.calls_exchange or self.writes_legacy_order:
            raise LegacyRetirementError("retired surface cannot retain side effects")


def validate_legacy_retirement(facts: Iterable[LegacyRetirementFact]) -> tuple[LegacyRetirementFact, ...]:
    """Validate the complete SC-15 surface inventory with no I/O."""

    values = tuple(facts)
    if not values:
        raise LegacyRetirementError("retirement inventory cannot be empty")
    if any(not isinstance(item, LegacyRetirementFact) for item in values):
        raise LegacyRetirementError("retirement inventory must use typed facts")
    surfaces = [item.surface for item in values]
    if len(set(surfaces)) != len(surfaces):
        raise LegacyRetirementError("retirement inventory has duplicate surfaces")
    required = set(LegacySurface)
    missing = required - set(surfaces)
    if missing:
        raise LegacyRetirementError("retirement inventory is incomplete")
    return tuple(sorted(values, key=lambda item: item.surface.value))


def failure_drill_disposition(kind: FailureDrillKind, *, durable_fact_exists: bool) -> LegacySurfaceDisposition:
    """Return the only safe outcome for a legacy failure drill.

    A restart/replay may observe a durable fact, but no drill can reactivate a
    legacy order surface.  Missing facts remain disabled and require the
    canonical admission path to create new facts.
    """

    if not isinstance(kind, FailureDrillKind) or not isinstance(durable_fact_exists, bool):
        raise LegacyRetirementError("failure drill inputs must be typed")
    if kind is FailureDrillKind.REPLAY and durable_fact_exists:
        return LegacySurfaceDisposition.ADMISSION_ONLY
    return LegacySurfaceDisposition.DISABLED


__all__ = [
    "LEGACY_RETIREMENT_CONTRACT_VERSION",
    "LegacyRetirementError",
    "LegacySurface",
    "LegacySurfaceDisposition",
    "FailureDrillKind",
    "LegacyRetirementFact",
    "validate_legacy_retirement",
    "failure_drill_disposition",
]
