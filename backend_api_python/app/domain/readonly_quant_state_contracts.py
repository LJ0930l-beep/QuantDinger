"""Pure, read-only projection API response contracts.

The read surface is deliberately built from the already validated G4-B chain
receipt.  It does not read a database, create a connection, call an exchange,
or expose an order/credential payload.  A future HTTP adapter can serialize
these immutable views without inventing freshness or health semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from app.domain.g4b_readonly_contracts import G4BReadonlyChainReceipt
from app.domain.order_contracts import ReconciliationHealth


READONLY_QUANT_STATE_CONTRACT_VERSION = "readonly-quant-state-v1"


class ReadonlyQuantStateContractError(ValueError):
    """The supplied read-only chain facts are incomplete or inconsistent."""


class ReadonlyViewStatus(str, Enum):
    READY = "READY"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyQuantStateContractError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReadonlyProjectionSummary:
    generation_id: str
    consumer_name: str
    checkpoint_watermark: int
    candidate_count: int
    projection_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.generation_id, str) or not self.generation_id:
            raise ReadonlyQuantStateContractError("generation_id is required")
        if not isinstance(self.consumer_name, str) or not self.consumer_name:
            raise ReadonlyQuantStateContractError("consumer_name is required")
        if isinstance(self.checkpoint_watermark, bool) or not isinstance(self.checkpoint_watermark, int) or self.checkpoint_watermark < 0:
            raise ReadonlyQuantStateContractError("checkpoint_watermark must be non-negative")
        if isinstance(self.candidate_count, bool) or not isinstance(self.candidate_count, int) or self.candidate_count < 0:
            raise ReadonlyQuantStateContractError("candidate_count must be non-negative")
        if not isinstance(self.projection_fingerprint, str) or len(self.projection_fingerprint) != 64:
            raise ReadonlyQuantStateContractError("projection_fingerprint must be SHA-256")


@dataclass(frozen=True, slots=True)
class ReadonlyShadowSummary:
    match_status: str
    difference_count: int
    tolerance_policy_fingerprint: str
    replay_fingerprint: str

    def __post_init__(self) -> None:
        if self.match_status not in {"MATCH", "DIFF"}:
            raise ReadonlyQuantStateContractError("match_status must be MATCH or DIFF")
        if isinstance(self.difference_count, bool) or not isinstance(self.difference_count, int) or self.difference_count < 0:
            raise ReadonlyQuantStateContractError("difference_count must be non-negative")
        for name in ("tolerance_policy_fingerprint", "replay_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                raise ReadonlyQuantStateContractError(f"{name} must be SHA-256")


@dataclass(frozen=True, slots=True)
class ReadonlyReconciliationSummary:
    checkpoint_status: str
    derived_health: str
    discrepancy_count: int
    replay_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_status, str) or not self.checkpoint_status:
            raise ReadonlyQuantStateContractError("checkpoint_status is required")
        if self.derived_health not in {item.value for item in ReconciliationHealth}:
            raise ReadonlyQuantStateContractError("derived_health is not canonical")
        if isinstance(self.discrepancy_count, bool) or not isinstance(self.discrepancy_count, int) or self.discrepancy_count < 0:
            raise ReadonlyQuantStateContractError("discrepancy_count must be non-negative")
        if not isinstance(self.replay_fingerprint, str) or len(self.replay_fingerprint) != 64:
            raise ReadonlyQuantStateContractError("replay_fingerprint must be SHA-256")


@dataclass(frozen=True, slots=True)
class ReadonlyQuantStateView:
    """Safe summary for a projection/shadow/reconciliation read endpoint."""

    status: ReadonlyViewStatus
    account_scope: str | None = None
    venue: str | None = None
    market_type: str | None = None
    instrument_id: str | None = None
    as_of: datetime | None = None
    projection: ReadonlyProjectionSummary | None = None
    shadow: ReadonlyShadowSummary | None = None
    reconciliation: ReadonlyReconciliationSummary | None = None
    chain_fingerprint: str | None = None
    view_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReadonlyViewStatus):
            raise ReadonlyQuantStateContractError("status must use ReadonlyViewStatus")
        if self.status in {ReadonlyViewStatus.READY, ReadonlyViewStatus.STALE}:
            for name in ("account_scope", "venue", "market_type", "instrument_id", "as_of", "projection", "shadow", "reconciliation", "chain_fingerprint"):
                if getattr(self, name) is None:
                    raise ReadonlyQuantStateContractError(f"{name} is required for an available view")
            object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
            if not isinstance(self.projection, ReadonlyProjectionSummary) or not isinstance(self.shadow, ReadonlyShadowSummary) or not isinstance(self.reconciliation, ReadonlyReconciliationSummary):
                raise ReadonlyQuantStateContractError("available view requires typed summaries")
            if not isinstance(self.chain_fingerprint, str) or len(self.chain_fingerprint) != 64:
                raise ReadonlyQuantStateContractError("chain_fingerprint must be SHA-256")
        elif any(value is not None for value in (self.account_scope, self.venue, self.market_type, self.instrument_id, self.as_of, self.projection, self.shadow, self.reconciliation, self.chain_fingerprint)):
            raise ReadonlyQuantStateContractError("unavailable or unauthorized views cannot carry facts")
        object.__setattr__(self, "view_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "version": READONLY_QUANT_STATE_CONTRACT_VERSION,
            "status": self.status.value,
            "account_scope": self.account_scope,
            "venue": self.venue,
            "market_type": self.market_type,
            "instrument_id": self.instrument_id,
            "as_of": None if self.as_of is None else self.as_of.isoformat(),
            "projection": None if self.projection is None else self.projection.__dict__ if hasattr(self.projection, "__dict__") else {
                "generation_id": self.projection.generation_id,
                "consumer_name": self.projection.consumer_name,
                "checkpoint_watermark": self.projection.checkpoint_watermark,
                "candidate_count": self.projection.candidate_count,
                "projection_fingerprint": self.projection.projection_fingerprint,
            },
            "shadow": None if self.shadow is None else {
                "match_status": self.shadow.match_status,
                "difference_count": self.shadow.difference_count,
                "tolerance_policy_fingerprint": self.shadow.tolerance_policy_fingerprint,
                "replay_fingerprint": self.shadow.replay_fingerprint,
            },
            "reconciliation": None if self.reconciliation is None else {
                "checkpoint_status": self.reconciliation.checkpoint_status,
                "derived_health": self.reconciliation.derived_health,
                "discrepancy_count": self.reconciliation.discrepancy_count,
                "replay_fingerprint": self.reconciliation.replay_fingerprint,
            },
            "chain_fingerprint": self.chain_fingerprint,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Return a credential/order-free JSON-ready response."""

        if self.status in {ReadonlyViewStatus.UNAVAILABLE, ReadonlyViewStatus.UNAUTHORIZED}:
            return {"contract_version": READONLY_QUANT_STATE_CONTRACT_VERSION, "status": self.status.value}
        facts = self.canonical_facts()
        facts["contract_version"] = READONLY_QUANT_STATE_CONTRACT_VERSION
        return facts


def build_readonly_quant_state_view(receipt: G4BReadonlyChainReceipt, *, authorized: bool = True) -> ReadonlyQuantStateView:
    """Build a read-only view from a validated G4-B receipt.

    ``authorized=False`` intentionally returns no facts.  The function never
    infers authorization from tenant, credential, or request payload values.
    """

    if not isinstance(authorized, bool):
        raise ReadonlyQuantStateContractError("authorized must be boolean")
    if not authorized:
        return ReadonlyQuantStateView(ReadonlyViewStatus.UNAUTHORIZED)
    if not isinstance(receipt, G4BReadonlyChainReceipt):
        raise ReadonlyQuantStateContractError("receipt must be a validated G4-B chain")

    run = receipt.reconciliation.run
    checkpoint = receipt.reconciliation.checkpoint
    shadow = receipt.shadow_result
    status = ReadonlyViewStatus.READY
    if checkpoint.health is not ReconciliationHealth.HEALTHY or shadow.diffs:
        status = ReadonlyViewStatus.STALE
    projection = ReadonlyProjectionSummary(
        generation_id=receipt.candidate.binding.generation_id,
        consumer_name=receipt.candidate.binding.consumer_name,
        checkpoint_watermark=receipt.candidate.binding.checkpoint_watermark,
        candidate_count=len(receipt.candidate.facts),
        projection_fingerprint=receipt.candidate.projection_fingerprint,
    )
    shadow_summary = ReadonlyShadowSummary(
        match_status="MATCH" if not shadow.diffs else "DIFF",
        difference_count=len(shadow.diffs),
        tolerance_policy_fingerprint=shadow.run.policy.tolerance_policy_fingerprint,
        replay_fingerprint=shadow.replay_fingerprint,
    )
    reconciliation = ReadonlyReconciliationSummary(
        checkpoint_status=checkpoint.status.value,
        derived_health=checkpoint.health.value,
        discrepancy_count=checkpoint.discrepancy_count,
        replay_fingerprint=receipt.reconciliation.replay_fingerprint,
    )
    return ReadonlyQuantStateView(
        status=status,
        account_scope=run.account_scope,
        venue=run.venue,
        market_type=run.market_type,
        instrument_id=run.instrument_id,
        as_of=run.as_of,
        projection=projection,
        shadow=shadow_summary,
        reconciliation=reconciliation,
        chain_fingerprint=receipt.chain_fingerprint,
    )


__all__ = [
    "READONLY_QUANT_STATE_CONTRACT_VERSION",
    "ReadonlyQuantStateContractError",
    "ReadonlyViewStatus",
    "ReadonlyProjectionSummary",
    "ReadonlyShadowSummary",
    "ReadonlyReconciliationSummary",
    "ReadonlyQuantStateView",
    "build_readonly_quant_state_view",
]
