"""Offline assembly of Gate account/instrument evidence.

The service accepts only caller-supplied, already retrieved payloads.  It is a
testnet/readiness boundary, not a private API client: no credential values,
network calls, persistence, order authority, or live mode exist here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json

from app.domain.gate_read_formatters import (
    GateReadPayloadError,
    normalize_gate_balances,
    normalize_gate_instruments,
    normalize_gate_positions,
)
from app.domain.gate_vertical_read_contracts import (
    GateAuthFacts,
    GateBalanceFact,
    GateInstrumentRuleSnapshot,
    GatePositionFact,
    gate_read_fingerprint,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType


GATE_VERTICAL_RESEARCH_SERVICE_VERSION = "gate-vertical-research-v1"


class GateVerticalResearchServiceError(ValueError):
    """Supplied Gate vertical facts cannot form a safe evidence bundle."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateVerticalResearchServiceError("observed_at must use a zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class GateVerticalEvidenceBundle:
    auth: GateAuthFacts
    balances: tuple[GateBalanceFact, ...]
    instruments: tuple[GateInstrumentRuleSnapshot, ...]
    positions: tuple[GatePositionFact, ...]
    observed_at: datetime
    bundle_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.auth, GateAuthFacts):
            raise GateVerticalResearchServiceError("auth facts must be typed")
        if not isinstance(self.balances, tuple) or any(not isinstance(item, GateBalanceFact) for item in self.balances):
            raise GateVerticalResearchServiceError("balances must be typed facts")
        if not isinstance(self.instruments, tuple) or any(not isinstance(item, GateInstrumentRuleSnapshot) for item in self.instruments):
            raise GateVerticalResearchServiceError("instruments must be typed facts")
        if not isinstance(self.positions, tuple) or any(not isinstance(item, GatePositionFact) for item in self.positions):
            raise GateVerticalResearchServiceError("positions must be typed facts")
        observed = _utc(self.observed_at)
        scope = self.auth.account_scope
        market_type = self.auth.market_type
        for item in (*self.balances, *self.instruments, *self.positions):
            if item.market_type is not market_type:
                raise GateVerticalResearchServiceError("vertical fact market scope mismatch")
            if hasattr(item, "account_scope") and item.account_scope != scope:
                raise GateVerticalResearchServiceError("vertical fact account scope mismatch")
            if item.observed_at > observed:
                raise GateVerticalResearchServiceError("observed_at precedes a supplied fact")
        object.__setattr__(self, "observed_at", observed)
        material = {
            "version": GATE_VERTICAL_RESEARCH_SERVICE_VERSION,
            "auth": gate_read_fingerprint(self.auth),
            "balances": [gate_read_fingerprint(item) for item in self.balances],
            "instruments": [gate_read_fingerprint(item) for item in self.instruments],
            "positions": [gate_read_fingerprint(item) for item in self.positions],
            "observed_at": observed.isoformat(),
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "bundle_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())


@dataclass(frozen=True, slots=True)
class GateVerticalResearchService:
    """Normalize Gate balances, instruments, and perpetual positions offline."""

    source_event_prefix: str
    evidence_hash_prefix: str

    def __post_init__(self) -> None:
        for field_name in ("source_event_prefix", "evidence_hash_prefix"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value.strip() != value or not value.isascii():
                raise GateVerticalResearchServiceError(f"{field_name} must be canonical ASCII text")

    def assemble(
        self,
        auth: GateAuthFacts,
        *,
        balances_payload: object,
        instruments_payload: object,
        positions_payload: object | None = None,
        valuation_ccy: str = "USDT",
        rule_version: str,
        observed_at: datetime,
    ) -> GateVerticalEvidenceBundle:
        if not isinstance(auth, GateAuthFacts):
            raise GateVerticalResearchServiceError("auth facts must be typed")
        observed = _utc(observed_at)
        market_type = auth.market_type
        try:
            balances = normalize_gate_balances(
                balances_payload,
                market_type=market_type,
                account_scope=auth.account_scope,
                valuation_ccy=valuation_ccy,
                observed_at=observed,
                source_event_prefix=self.source_event_prefix,
                evidence_hash_prefix=self.evidence_hash_prefix,
            )
            instruments = normalize_gate_instruments(
                instruments_payload,
                market_type=market_type,
                observed_at=observed,
                rule_version=rule_version,
            )
            if market_type is AssetMarketType.PERPETUAL:
                if positions_payload is None:
                    raise GateVerticalResearchServiceError("perpetual evidence requires positions payload")
                positions = normalize_gate_positions(
                    positions_payload,
                    market_type=market_type,
                    account_scope=auth.account_scope,
                    observed_at=observed,
                    source_event_prefix=self.source_event_prefix,
                )
            else:
                if positions_payload not in (None, [], (), {"data": []}):
                    raise GateVerticalResearchServiceError("spot profile cannot carry perpetual positions")
                positions = ()
            return GateVerticalEvidenceBundle(auth, balances, instruments, positions, observed)
        except GateVerticalResearchServiceError:
            raise
        except GateReadPayloadError as exc:
            raise GateVerticalResearchServiceError("Gate vertical payload is invalid") from exc
        except Exception as exc:
            raise GateVerticalResearchServiceError("Gate vertical evidence could not be assembled") from exc


__all__ = [
    "GATE_VERTICAL_RESEARCH_SERVICE_VERSION",
    "GateVerticalEvidenceBundle",
    "GateVerticalResearchService",
    "GateVerticalResearchServiceError",
]
