"""Controlled Gate TestNet market-read session orchestration.

The session accepts a caller-owned, profile-scoped adapter and stops at an
immutable market evidence bundle.  It never looks up credentials, creates an
HTTP client, enables writes, or places an order.  A real transport may only be
injected by a separately approved testnet harness; fixtures are the default
for local verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json

from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter
from app.services.gate_market_research_service import GateMarketEvidenceBundle, GateMarketResearchService
from app.services.gate_testnet_readiness_service import (
    GateTestnetReadinessReceipt,
    GateTestnetReadinessService,
    GateTestnetReadinessStatus,
)


GATE_TESTNET_MARKET_SESSION_VERSION = "gate-testnet-market-session-v1"


class GateTestnetMarketSessionError(ValueError):
    """The read-only TestNet session cannot produce safe evidence."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateTestnetMarketSessionError("observed_at must use a zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class GateTestnetMarketSessionRequest:
    instrument_id: str
    observed_at: datetime
    snapshot_id: str
    rule_version: str
    interval: str = "1m"
    candle_limit: int = 100
    depth_limit: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id or self.instrument_id.strip() != self.instrument_id or any(ch.isspace() for ch in self.instrument_id) or not self.instrument_id.isascii():
            raise GateTestnetMarketSessionError("instrument_id must be canonical ASCII text")
        for field_name in ("snapshot_id", "rule_version", "interval"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value.strip() != value or any(ch.isspace() for ch in value) or not value.isascii():
                raise GateTestnetMarketSessionError(f"{field_name} must be canonical ASCII text")
        observed = _utc(self.observed_at)
        object.__setattr__(self, "observed_at", observed)
        for field_name in ("candle_limit", "depth_limit"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
                raise GateTestnetMarketSessionError(f"{field_name} must be between 1 and 1000")


@dataclass(frozen=True, slots=True)
class GateTestnetMarketSessionReceipt:
    readiness: GateTestnetReadinessReceipt
    request: GateTestnetMarketSessionRequest
    evidence: GateMarketEvidenceBundle
    session_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.readiness, GateTestnetReadinessReceipt) or self.readiness.status is not GateTestnetReadinessStatus.READY:
            raise GateTestnetMarketSessionError("session requires READY TestNet readiness")
        if not isinstance(self.request, GateTestnetMarketSessionRequest) or not isinstance(self.evidence, GateMarketEvidenceBundle):
            raise GateTestnetMarketSessionError("session request and evidence must be typed")
        if self.evidence.instrument_id != self.request.instrument_id or self.evidence.snapshot_id != self.request.snapshot_id:
            raise GateTestnetMarketSessionError("evidence does not match session request")
        encoded = json.dumps({
            "version": GATE_TESTNET_MARKET_SESSION_VERSION,
            "readiness": self.readiness.readiness_fingerprint,
            "instrument_id": self.request.instrument_id,
            "observed_at": self.request.observed_at.isoformat(),
            "snapshot_id": self.request.snapshot_id,
            "rule_version": self.request.rule_version,
            "interval": self.request.interval,
            "candle_limit": self.request.candle_limit,
            "depth_limit": self.request.depth_limit,
            "evidence": self.evidence.bundle_fingerprint,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "session_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())


@dataclass(frozen=True, slots=True)
class GateTestnetMarketSessionService:
    market_service: GateMarketResearchService
    readiness_service: GateTestnetReadinessService = GateTestnetReadinessService()

    def __post_init__(self) -> None:
        if not isinstance(self.market_service, GateMarketResearchService):
            raise GateTestnetMarketSessionError("market_service must be typed")
        if not isinstance(self.readiness_service, GateTestnetReadinessService):
            raise GateTestnetMarketSessionError("readiness_service must be typed")

    def read(self, request: GateTestnetMarketSessionRequest) -> GateTestnetMarketSessionReceipt:
        if not isinstance(request, GateTestnetMarketSessionRequest):
            raise GateTestnetMarketSessionError("request must be typed")
        adapter = self.market_service.adapter
        if not isinstance(adapter, GateReadonlyAdapter):
            raise GateTestnetMarketSessionError("market adapter must be typed")
        readiness = self.readiness_service.assess(adapter)
        if readiness.status is not GateTestnetReadinessStatus.READY:
            raise GateTestnetMarketSessionError("Gate TestNet read profile is blocked")
        try:
            evidence = self.market_service.read_market_evidence(
                request.instrument_id,
                interval=request.interval,
                candle_limit=request.candle_limit,
                depth_limit=request.depth_limit,
                observed_at=request.observed_at,
                snapshot_id=request.snapshot_id,
                rule_version=request.rule_version,
            )
        except Exception as exc:
            raise GateTestnetMarketSessionError("Gate TestNet market read failed") from exc
        return GateTestnetMarketSessionReceipt(readiness, request, evidence)


__all__ = [
    "GATE_TESTNET_MARKET_SESSION_VERSION",
    "GateTestnetMarketSessionError",
    "GateTestnetMarketSessionReceipt",
    "GateTestnetMarketSessionRequest",
    "GateTestnetMarketSessionService",
]
