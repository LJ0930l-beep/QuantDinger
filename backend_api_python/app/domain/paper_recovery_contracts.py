"""Deterministic, read-only Paper restart/recovery evidence.

The recovery check replays the existing Paper order facts through the same
position projection used by the account reader.  It never writes a checkpoint,
opens an exchange client, or treats a missing expected fingerprint as proof of
recovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from app.domain.readonly_paper_account_contracts import (
    ReadonlyPaperAccountSnapshot,
    ReadonlyPaperAccountError,
    project_paper_positions,
)


PAPER_RECOVERY_CONTRACT_VERSION = "paper-recovery-v1"


class PaperRecoveryError(ValueError):
    """Invalid or unverifiable Paper recovery evidence."""


class PaperRecoveryStatus(str, Enum):
    VERIFIED = "VERIFIED"
    CHECKPOINT_REQUIRED = "CHECKPOINT_REQUIRED"
    MISMATCH = "MISMATCH"


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise PaperRecoveryError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise PaperRecoveryError(f"{field_name} must be a lowercase SHA-256 hex string")
    return value


def _projection_material(snapshot: ReadonlyPaperAccountSnapshot) -> dict[str, Any]:
    return {
        "version": PAPER_RECOVERY_CONTRACT_VERSION,
        "account_snapshot": snapshot.snapshot_fingerprint,
        "positions": [
            {
                "market": item.market,
                "symbol": item.symbol,
                "signed_quantity": format(item.signed_quantity.normalize(), "f"),
                "average_entry_price": None if item.average_entry_price is None else format(item.average_entry_price.normalize(), "f"),
                "realized_pnl": format(item.realized_pnl.normalize(), "f"),
            }
            for item in snapshot.positions
        ],
        "observed_at": snapshot.observed_at.isoformat(),
        "live_enabled": False,
    }


@dataclass(frozen=True, slots=True)
class PaperRecoveryEvidence:
    status: PaperRecoveryStatus
    user_id: int
    snapshot_fingerprint: str
    replay_fingerprint: str
    order_count: int
    position_count: int
    observed_at: datetime
    expected_snapshot_fingerprint: str | None = None
    recovery_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, PaperRecoveryStatus):
            raise PaperRecoveryError("status must be typed")
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int) or self.user_id <= 0:
            raise PaperRecoveryError("user_id must be a positive integer")
        _hash(self.snapshot_fingerprint, "snapshot_fingerprint")
        _hash(self.replay_fingerprint, "replay_fingerprint")
        if isinstance(self.order_count, bool) or not isinstance(self.order_count, int) or self.order_count < 0:
            raise PaperRecoveryError("order_count must be non-negative")
        if isinstance(self.position_count, bool) or not isinstance(self.position_count, int) or self.position_count < 0:
            raise PaperRecoveryError("position_count must be non-negative")
        observed = _utc(self.observed_at, "observed_at")
        expected = None if self.expected_snapshot_fingerprint is None else _hash(
            self.expected_snapshot_fingerprint, "expected_snapshot_fingerprint"
        )
        if self.status is PaperRecoveryStatus.VERIFIED and expected is None:
            raise PaperRecoveryError("VERIFIED recovery requires an expected snapshot fingerprint")
        if self.status is PaperRecoveryStatus.MISMATCH and expected is None:
            raise PaperRecoveryError("MISMATCH recovery requires an expected snapshot fingerprint")
        material = {
            "version": PAPER_RECOVERY_CONTRACT_VERSION,
            "status": self.status.value,
            "user_id": self.user_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "replay_fingerprint": self.replay_fingerprint,
            "expected_snapshot_fingerprint": expected,
            "order_count": self.order_count,
            "position_count": self.position_count,
            "observed_at": observed.isoformat(),
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expected_snapshot_fingerprint", expected)
        object.__setattr__(self, "recovery_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": PAPER_RECOVERY_CONTRACT_VERSION,
            "status": self.status.value,
            "user_id": self.user_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "replay_fingerprint": self.replay_fingerprint,
            "expected_snapshot_fingerprint": self.expected_snapshot_fingerprint,
            "order_count": self.order_count,
            "position_count": self.position_count,
            "observed_at": self.observed_at.isoformat(),
            "recovery_fingerprint": self.recovery_fingerprint,
            "live_enabled": False,
        }


def verify_paper_snapshot_recovery(
    snapshot: ReadonlyPaperAccountSnapshot,
    *,
    expected_snapshot_fingerprint: str | None = None,
) -> PaperRecoveryEvidence:
    """Replay Paper facts and compare an optional caller-supplied checkpoint."""

    if not isinstance(snapshot, ReadonlyPaperAccountSnapshot):
        raise PaperRecoveryError("recovery requires a typed Paper account snapshot")
    expected = None if expected_snapshot_fingerprint is None else _hash(
        expected_snapshot_fingerprint, "expected_snapshot_fingerprint"
    )
    try:
        replayed_positions = project_paper_positions(snapshot.orders)
    except (ReadonlyPaperAccountError, ValueError, TypeError) as exc:
        raise PaperRecoveryError("Paper facts cannot be replayed deterministically") from exc
    if replayed_positions != snapshot.positions:
        raise PaperRecoveryError("Paper position projection is not replay-stable")
    projection_material = _projection_material(snapshot)
    replay_fingerprint = hashlib.sha256(
        json.dumps(projection_material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    status = PaperRecoveryStatus.CHECKPOINT_REQUIRED
    if expected is not None:
        status = PaperRecoveryStatus.VERIFIED if expected == snapshot.snapshot_fingerprint else PaperRecoveryStatus.MISMATCH
    return PaperRecoveryEvidence(
        status=status,
        user_id=snapshot.user_id,
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        replay_fingerprint=replay_fingerprint,
        order_count=len(snapshot.orders),
        position_count=len(snapshot.positions),
        observed_at=snapshot.observed_at,
        expected_snapshot_fingerprint=expected,
    )


__all__ = [
    "PAPER_RECOVERY_CONTRACT_VERSION",
    "PaperRecoveryError",
    "PaperRecoveryEvidence",
    "PaperRecoveryStatus",
    "verify_paper_snapshot_recovery",
]
