"""Shared typed status for the Gate TestNet read-only readiness gate."""

from enum import Enum


class GateTestnetReadinessStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


__all__ = ["GateTestnetReadinessStatus"]
