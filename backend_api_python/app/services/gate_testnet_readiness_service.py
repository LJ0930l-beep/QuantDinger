"""Fail-closed readiness evidence for Gate TestNet read-only research.

This module does not contact Gate.  It validates an injected adapter/profile
before a separately controlled test harness may use public GET transport.
Writes, order reads, and live capability are never inferred or enabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json

from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter
from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, validate_gate_readonly_profile
from app.domain.gate_testnet_readiness_contracts import GateTestnetReadinessStatus


GATE_TESTNET_READINESS_VERSION = "gate-testnet-readiness-v1"


class GateTestnetReadinessError(ValueError):
    """The supplied Gate TestNet profile is not safe for read-only research."""


@dataclass(frozen=True, slots=True)
class GateTestnetReadinessReceipt:
    status: GateTestnetReadinessStatus
    market_type: GateMarketType
    base_url: str
    public_market_data: bool
    writes_enabled: bool
    live_enabled: bool
    reason_codes: tuple[str, ...]
    readiness_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, GateTestnetReadinessStatus):
            raise GateTestnetReadinessError("readiness status must be typed")
        if not isinstance(self.market_type, GateMarketType):
            raise GateTestnetReadinessError("market_type must be typed")
        if not isinstance(self.base_url, str) or not self.base_url:
            raise GateTestnetReadinessError("base_url is required")
        if not isinstance(self.reason_codes, tuple) or any(not isinstance(item, str) or not item for item in self.reason_codes):
            raise GateTestnetReadinessError("reason_codes must be canonical")
        if self.live_enabled or self.writes_enabled:
            raise GateTestnetReadinessError("readiness receipt cannot authorize writes or live mode")
        material = {
            "version": GATE_TESTNET_READINESS_VERSION,
            "status": self.status.value,
            "market_type": self.market_type.value,
            "base_url": self.base_url,
            "public_market_data": self.public_market_data,
            "writes_enabled": self.writes_enabled,
            "live_enabled": self.live_enabled,
            "reason_codes": self.reason_codes,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "readiness_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())


@dataclass(frozen=True, slots=True)
class GateTestnetReadinessService:
    """Assess a caller-owned adapter without making a network request."""

    def assess(self, adapter: GateReadonlyAdapter) -> GateTestnetReadinessReceipt:
        if not isinstance(adapter, GateReadonlyAdapter):
            raise GateTestnetReadinessError("a typed GateReadonlyAdapter is required")
        profile = adapter.profile
        reasons: list[str] = []
        try:
            validate_gate_readonly_profile(profile)
        except Exception as exc:
            raise GateTestnetReadinessError("Gate profile is not read-only TestNet") from exc
        if profile.environment is not GateEnvironment.TESTNET:
            reasons.append("environment_not_testnet")
        if not profile.supports_public_market_data:
            reasons.append("public_market_data_unsupported")
        if profile.writes_enabled:
            reasons.append("writes_enabled")
        if not callable(adapter.transport):
            reasons.append("transport_missing")
        status = GateTestnetReadinessStatus.READY if not reasons else GateTestnetReadinessStatus.BLOCKED
        if not reasons:
            reasons.append("read_only_testnet_profile_validated")
        return GateTestnetReadinessReceipt(
            status,
            profile.market_type,
            profile.base_url,
            bool(profile.supports_public_market_data),
            False,
            False,
            tuple(reasons),
        )


__all__ = [
    "GATE_TESTNET_READINESS_VERSION",
    "GateTestnetReadinessError",
    "GateTestnetReadinessReceipt",
    "GateTestnetReadinessService",
    "GateTestnetReadinessStatus",
]
