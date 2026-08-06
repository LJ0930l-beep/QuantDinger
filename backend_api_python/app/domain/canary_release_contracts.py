"""Deterministic canary evidence and rollback decision contracts.

Canary evidence is a gate, not an activation mechanism.  The contract never
enables LIVE and requires an explicit operator-owned rollback proof before a
release can become a promotion candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json


CANARY_CONTRACT_VERSION = "canary-release-v1"


class CanaryContractError(ValueError):
    pass


class CanaryDecision(str, Enum):
    BLOCKED = "BLOCKED"
    PROMOTION_CANDIDATE = "PROMOTION_CANDIDATE"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise CanaryContractError(f"{field_name} must be canonical ASCII text")
    return value


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise CanaryContractError("observed_at must use zero UTC offset")
    return value.astimezone(timezone.utc)


def _ratio(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise CanaryContractError(f"{field_name} must be Decimal-compatible")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise CanaryContractError(f"{field_name} must be Decimal-compatible") from exc
    if not result.is_finite() or result < 0 or result > 1:
        raise CanaryContractError(f"{field_name} must be between 0 and 1")
    return result


@dataclass(frozen=True, slots=True)
class CanaryReleaseEvidence:
    release_id: str
    artifact_digest: str
    sample_count: int
    error_count: int
    shadow_match_rate: Decimal
    reconciliation_healthy: bool
    kill_switch_clear: bool
    rollback_verified: bool
    observed_at: datetime
    live_enabled: bool = False
    evidence_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_id", _text(self.release_id, "release_id"))
        object.__setattr__(self, "artifact_digest", _text(self.artifact_digest, "artifact_digest"))
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise CanaryContractError("sample_count must be a non-negative integer")
        if isinstance(self.error_count, bool) or not isinstance(self.error_count, int) or self.error_count < 0 or self.error_count > self.sample_count:
            raise CanaryContractError("error_count must be within sample_count")
        object.__setattr__(self, "shadow_match_rate", _ratio(self.shadow_match_rate, "shadow_match_rate"))
        for name in ("reconciliation_healthy", "kill_switch_clear", "rollback_verified", "live_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise CanaryContractError(f"{name} must be boolean")
        if self.live_enabled:
            raise CanaryContractError("canary evidence cannot enable LIVE")
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        payload = {
            "version": CANARY_CONTRACT_VERSION,
            "release_id": self.release_id,
            "artifact_digest": self.artifact_digest,
            "sample_count": self.sample_count,
            "error_count": self.error_count,
            "shadow_match_rate": format(self.shadow_match_rate.normalize(), "f"),
            "reconciliation_healthy": self.reconciliation_healthy,
            "kill_switch_clear": self.kill_switch_clear,
            "rollback_verified": self.rollback_verified,
            "observed_at": self.observed_at.isoformat(),
            "live_enabled": False,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "evidence_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    @property
    def error_rate(self) -> Decimal:
        if self.sample_count == 0:
            return Decimal("1")
        return Decimal(self.error_count) / Decimal(self.sample_count)


@dataclass(frozen=True, slots=True)
class CanaryPromotionResult:
    decision: CanaryDecision
    reasons: tuple[str, ...]
    evidence_fingerprint: str
    live_enabled: bool = False


def evaluate_canary_promotion(
    evidence: CanaryReleaseEvidence,
    *,
    minimum_samples: int = 100,
    maximum_error_rate: Decimal = Decimal("0.01"),
    minimum_shadow_match_rate: Decimal = Decimal("0.995"),
) -> CanaryPromotionResult:
    if not isinstance(evidence, CanaryReleaseEvidence):
        raise CanaryContractError("typed canary evidence is required")
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples < 1:
        raise CanaryContractError("minimum_samples must be positive")
    max_errors = _ratio(maximum_error_rate, "maximum_error_rate")
    min_match = _ratio(minimum_shadow_match_rate, "minimum_shadow_match_rate")
    reasons: list[str] = []
    if evidence.sample_count < minimum_samples:
        reasons.append("insufficient_samples")
    if evidence.error_rate > max_errors:
        reasons.append("error_rate_above_limit")
    if evidence.shadow_match_rate < min_match:
        reasons.append("shadow_match_below_limit")
    if not evidence.reconciliation_healthy:
        reasons.append("reconciliation_unhealthy")
    if not evidence.kill_switch_clear:
        reasons.append("kill_switch_not_clear")
    if not evidence.rollback_verified:
        reasons.append("rollback_not_verified")
    decision = CanaryDecision.BLOCKED if reasons else CanaryDecision.PROMOTION_CANDIDATE
    return CanaryPromotionResult(decision, tuple(reasons), evidence.evidence_fingerprint, False)


__all__ = [
    "CANARY_CONTRACT_VERSION",
    "CanaryContractError",
    "CanaryDecision",
    "CanaryPromotionResult",
    "CanaryReleaseEvidence",
    "evaluate_canary_promotion",
]
