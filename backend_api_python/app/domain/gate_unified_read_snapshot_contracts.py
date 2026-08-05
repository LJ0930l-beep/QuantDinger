"""Typed aggregate for a Gate Spot + Perpetual read-only snapshot.

The aggregate is intentionally read-only.  It keeps the two market books
separate so balances, positions and fills are never silently added across
different Gate account products.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .gate_read_snapshot_contracts import GateReadSnapshot
from .gate_vertical_read_contracts import GatePermission
from .multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment


GATE_UNIFIED_READ_SNAPSHOT_CONTRACT_VERSION = "gate-unified-read-snapshot-v1"


class GateUnifiedReadSnapshotError(ValueError):
    """Malformed or cross-scope Gate market snapshots."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateUnifiedReadSnapshotError("observed_at must be zero-offset UTC")
    return value.astimezone(timezone.utc)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GateUnifiedReadSnapshot:
    """An immutable, same-credential aggregate of the two Gate books."""

    snapshots: tuple[GateReadSnapshot, ...]
    observed_at: datetime
    snapshot_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshots, tuple) or not self.snapshots:
            raise GateUnifiedReadSnapshotError("at least one market snapshot is required")
        if any(not isinstance(item, GateReadSnapshot) for item in self.snapshots):
            raise GateUnifiedReadSnapshotError("snapshots must be typed GateReadSnapshot values")
        markets = [item.auth.market_type for item in self.snapshots]
        if len(set(markets)) != len(markets):
            raise GateUnifiedReadSnapshotError("market_type must be unique")
        first = self.snapshots[0].auth
        for snapshot in self.snapshots:
            auth = snapshot.auth
            if auth.venue_id != first.venue_id or auth.account_scope != first.account_scope:
                raise GateUnifiedReadSnapshotError("Gate snapshot scope mismatch")
            if auth.environment is not first.environment or auth.credential_ref != first.credential_ref:
                raise GateUnifiedReadSnapshotError("Gate credential or environment mismatch")
        observed = _utc(self.observed_at)
        if any(item.observed_at > observed for item in self.snapshots):
            raise GateUnifiedReadSnapshotError("snapshot observed_at cannot exceed aggregate observed_at")
        expected = build_gate_unified_read_snapshot_fingerprint(self.snapshots, observed)
        if self.snapshot_fingerprint != expected:
            raise GateUnifiedReadSnapshotError("snapshot_fingerprint does not match immutable facts")
        object.__setattr__(self, "observed_at", observed)

    @property
    def venue_id(self) -> str:
        return self.snapshots[0].auth.venue_id

    @property
    def account_scope(self) -> str:
        return self.snapshots[0].auth.account_scope

    @property
    def environment(self) -> CapabilityEnvironment:
        return self.snapshots[0].auth.environment

    @property
    def account_facts_complete(self) -> bool:
        """Whether every market snapshot contains authoritative account facts.

        A unified *account* response must not claim readiness when a provider
        returned only an authenticated shell (for example, no balance rows).
        Keeping this check derived from typed facts prevents callers from
        manufacturing a READY/zero-balance response after a partial read.
        """

        return all(
            bool(snapshot.balances)
            and GatePermission.READ_ACCOUNT in snapshot.auth.permissions
            for snapshot in self.snapshots
        )

    def to_public_dict(self) -> dict[str, Any]:
        """Expose sanitized market-separated snapshots; never credential facts."""

        # This is a read-health receipt, not a claim that reconciliation or
        # live trading is healthy.  Those facts have separate authoritative
        # providers and must not be inferred from a private account response.
        account_facts_verified = self.account_facts_complete
        read_health = {
            "status": "READY" if account_facts_verified else "INCOMPLETE",
            "scope_verified": True,
            "account_facts_verified": account_facts_verified,
            "reconciliation_health": "UNKNOWN",
            "market_data_health": "UNKNOWN",
            "live_enabled": False,
        }
        return {
            "contract_version": GATE_UNIFIED_READ_SNAPSHOT_CONTRACT_VERSION,
            "status": "READY",
            "venue_id": self.venue_id,
            "account_scope": self.account_scope,
            "environment": self.environment.value,
            "observed_at": self.observed_at.isoformat(),
            "market_types": [item.auth.market_type.value for item in self.snapshots],
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "read_health": read_health,
            "markets": {
                item.auth.market_type.value: item.to_public_dict()
                for item in self.snapshots
            },
            "live_enabled": False,
        }


def build_gate_unified_read_snapshot_fingerprint(
    snapshots: tuple[GateReadSnapshot, ...], observed_at: datetime
) -> str:
    if not isinstance(snapshots, tuple) or any(not isinstance(item, GateReadSnapshot) for item in snapshots):
        raise GateUnifiedReadSnapshotError("snapshots must be typed")
    observed = _utc(observed_at)
    material = {
        "version": GATE_UNIFIED_READ_SNAPSHOT_CONTRACT_VERSION,
        "observed_at": observed.isoformat(),
        "markets": sorted(
            (
                item.auth.market_type.value,
                item.snapshot_fingerprint,
            )
            for item in snapshots
        ),
    }
    return _fingerprint(material)


def build_gate_unified_read_snapshot(
    snapshots: tuple[GateReadSnapshot, ...], *, observed_at: datetime
) -> GateUnifiedReadSnapshot:
    if not isinstance(snapshots, tuple):
        raise GateUnifiedReadSnapshotError("snapshots must be a tuple")
    observed = _utc(observed_at)
    return GateUnifiedReadSnapshot(
        snapshots=snapshots,
        observed_at=observed,
        snapshot_fingerprint=build_gate_unified_read_snapshot_fingerprint(snapshots, observed),
    )


__all__ = [
    "GATE_UNIFIED_READ_SNAPSHOT_CONTRACT_VERSION",
    "GateUnifiedReadSnapshot",
    "GateUnifiedReadSnapshotError",
    "build_gate_unified_read_snapshot",
    "build_gate_unified_read_snapshot_fingerprint",
]
