"""Pure, deterministic comparison contracts for legacy-versus-candidate shadow state.

This module has no database, worker, exchange, order-decision, or runtime
dependency.  It compares supplied facts only and fails closed when their scope,
valuation, freshness, or numeric representation is not trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from app.domain.decimal_values import DecimalInput, DecimalInputTypeError, canonical_decimal_string, validate_numeric_38_18


SHADOW_DIFF_CONTRACT_VERSION = "shadow-diff-v1"


class ShadowDiffContractError(ValueError):
    """Raised for non-canonical or unsafe shadow-comparison input."""


class ShadowDiffSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class ShadowDiffKind(str, Enum):
    MISSING_LEGACY = "MISSING_LEGACY"
    MISSING_CANDIDATE = "MISSING_CANDIDATE"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    STALE_SOURCE = "STALE_SOURCE"
    UNSUPPORTED_FACT = "UNSUPPORTED_FACT"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    VALUATION_REQUIRED = "VALUATION_REQUIRED"


class ShadowRunState(str, Enum):
    BUILDING = "BUILDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class ShadowSourceStatus(str, Enum):
    READY = "READY"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ShadowValueKind(str, Enum):
    QUANTITY = "QUANTITY"
    MONETARY = "MONETARY"
    RATIO = "RATIO"


def _text(value: object, field_name: str, *, uppercase: bool = False, lowercase: bool = False, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii() or len(value) > max_length:
        raise ShadowDiffContractError(f"{field_name} must be canonical ASCII text")
    if uppercase and value != value.upper():
        raise ShadowDiffContractError(f"{field_name} must be uppercase")
    if lowercase and value != value.lower():
        raise ShadowDiffContractError(f"{field_name} must be lowercase")
    return value


def _uuid(value: UUID | str, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(value)).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ShadowDiffContractError(f"{field_name} must be a UUID") from exc


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ShadowDiffContractError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


def _decimal(value: DecimalInput, field_name: str, *, non_negative: bool = True) -> Decimal:
    try:
        parsed = validate_numeric_38_18(value)
    except (ValueError, TypeError, DecimalInputTypeError) as exc:
        raise ShadowDiffContractError(f"{field_name} must satisfy NUMERIC(38,18)") from exc
    if non_negative and parsed < 0:
        raise ShadowDiffContractError(f"{field_name} cannot be negative")
    return parsed


def _fingerprint(facts: object) -> str:
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class ShadowTolerancePolicy:
    policy_version: str
    quantity_absolute: DecimalInput = Decimal(0)
    quantity_relative: DecimalInput = Decimal(0)
    monetary_absolute: DecimalInput = Decimal(0)
    monetary_relative: DecimalInput = Decimal(0)
    ratio_absolute: DecimalInput = Decimal(0)
    tolerance_policy_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version", max_length=64))
        for name in ("quantity_absolute", "quantity_relative", "monetary_absolute", "monetary_relative", "ratio_absolute"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        object.__setattr__(self, "tolerance_policy_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, str]:
        return {
            "policy_version": self.policy_version,
            "quantity_absolute": canonical_decimal_string(self.quantity_absolute),
            "quantity_relative": canonical_decimal_string(self.quantity_relative),
            "monetary_absolute": canonical_decimal_string(self.monetary_absolute),
            "monetary_relative": canonical_decimal_string(self.monetary_relative),
            "ratio_absolute": canonical_decimal_string(self.ratio_absolute),
        }


@dataclass(frozen=True, slots=True)
class ShadowFactValue:
    value: DecimalInput
    kind: ShadowValueKind
    asset: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _decimal(self.value, "value", non_negative=False))
        if not isinstance(self.kind, ShadowValueKind):
            raise ShadowDiffContractError("kind must use ShadowValueKind")
        if self.kind is ShadowValueKind.RATIO:
            if self.asset is not None:
                raise ShadowDiffContractError("ratio facts cannot carry an asset")
        else:
            object.__setattr__(self, "asset", _text(self.asset, "asset", uppercase=True, max_length=24))

    def canonical_facts(self) -> dict[str, str | None]:
        return {"value": canonical_decimal_string(self.value), "kind": self.kind.value, "asset": self.asset}


@dataclass(frozen=True, slots=True)
class ShadowSourceSnapshot:
    source_name: str
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    source_version: str
    observed_at: datetime
    status: ShadowSourceStatus
    facts: Mapping[str, ShadowFactValue]
    generation_id: UUID | str | None = None
    checkpoint_watermark: int | None = None
    source_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_name", _text(self.source_name, "source_name", lowercase=True, max_length=32))
        for name in ("tenant_id", "credential_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ShadowDiffContractError(f"{name} must be positive")
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", uppercase=True, max_length=100))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lowercase=True, max_length=20))
        object.__setattr__(self, "source_version", _text(self.source_version, "source_version", max_length=64))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if not isinstance(self.status, ShadowSourceStatus):
            raise ShadowDiffContractError("status must use ShadowSourceStatus")
        if (self.generation_id is None) != (self.checkpoint_watermark is None):
            raise ShadowDiffContractError("generation_id and checkpoint_watermark must be supplied together")
        if self.generation_id is not None:
            object.__setattr__(self, "generation_id", _uuid(self.generation_id, "generation_id"))
            if isinstance(self.checkpoint_watermark, bool) or not isinstance(self.checkpoint_watermark, int) or self.checkpoint_watermark < 0:
                raise ShadowDiffContractError("checkpoint_watermark must be non-negative")
        canonical: dict[str, ShadowFactValue] = {}
        for key, value in self.facts.items():
            canonical[_text(key, "fact_name", max_length=100)] = value
            if not isinstance(value, ShadowFactValue):
                raise ShadowDiffContractError("facts must use ShadowFactValue")
        object.__setattr__(self, "facts", MappingProxyType(dict(sorted(canonical.items()))))
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_scope(self) -> tuple[int, int, str, str, str]:
        return (self.tenant_id, self.credential_id, self.account_scope, self.instrument_id, self.market_type)

    def canonical_facts(self) -> dict[str, object]:
        return {
            "version": SHADOW_DIFF_CONTRACT_VERSION,
            "source_name": self.source_name,
            "scope": self.canonical_scope(),
            "source_version": self.source_version,
            "observed_at": self.observed_at.isoformat(),
            "status": self.status.value,
            "generation_id": self.generation_id,
            "checkpoint_watermark": self.checkpoint_watermark,
            "facts": {name: value.canonical_facts() for name, value in self.facts.items()},
        }


@dataclass(frozen=True, slots=True)
class ShadowComparisonRun:
    run_id: UUID | str
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    legacy_source_identity: str
    legacy_source_version: str
    legacy_source_fingerprint: str
    candidate_generation_id: UUID | str
    candidate_consumer_name: str
    candidate_generation_build_fingerprint: str
    candidate_checkpoint_watermark: int
    as_of: datetime
    correlation_id: str
    policy: ShadowTolerancePolicy
    state: ShadowRunState = ShadowRunState.BUILDING
    build_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        for name in ("tenant_id", "credential_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ShadowDiffContractError(f"{name} must be positive")
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", uppercase=True, max_length=100))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lowercase=True, max_length=20))
        object.__setattr__(self, "legacy_source_identity", _text(self.legacy_source_identity, "legacy_source_identity", lowercase=True, max_length=32))
        object.__setattr__(self, "legacy_source_version", _text(self.legacy_source_version, "legacy_source_version", max_length=64))
        if not isinstance(self.legacy_source_fingerprint, str) or len(self.legacy_source_fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in self.legacy_source_fingerprint):
            raise ShadowDiffContractError("legacy_source_fingerprint must be SHA-256")
        object.__setattr__(self, "candidate_generation_id", _uuid(self.candidate_generation_id, "candidate_generation_id"))
        object.__setattr__(self, "candidate_consumer_name", _text(self.candidate_consumer_name, "candidate_consumer_name", lowercase=True, max_length=160))
        if not isinstance(self.candidate_generation_build_fingerprint, str) or len(self.candidate_generation_build_fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in self.candidate_generation_build_fingerprint):
            raise ShadowDiffContractError("candidate_generation_build_fingerprint must be SHA-256")
        if isinstance(self.candidate_checkpoint_watermark, bool) or not isinstance(self.candidate_checkpoint_watermark, int) or self.candidate_checkpoint_watermark < 0:
            raise ShadowDiffContractError("candidate_checkpoint_watermark must be non-negative")
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        object.__setattr__(self, "correlation_id", _text(self.correlation_id, "correlation_id"))
        if not isinstance(self.policy, ShadowTolerancePolicy) or not isinstance(self.state, ShadowRunState):
            raise ShadowDiffContractError("policy and state must use canonical contracts")
        object.__setattr__(self, "build_fingerprint", _fingerprint(self.canonical_build_facts()))

    def canonical_scope(self) -> tuple[int, int, str, str, str]:
        return (self.tenant_id, self.credential_id, self.account_scope, self.instrument_id, self.market_type)

    def canonical_build_facts(self) -> dict[str, object]:
        return {
            "version": SHADOW_DIFF_CONTRACT_VERSION,
            "scope": self.canonical_scope(),
            "legacy_source_identity": self.legacy_source_identity,
            "legacy_source_version": self.legacy_source_version,
            "legacy_source_fingerprint": self.legacy_source_fingerprint,
            "candidate_generation_id": self.candidate_generation_id,
            "candidate_consumer_name": self.candidate_consumer_name,
            "candidate_generation_build_fingerprint": self.candidate_generation_build_fingerprint,
            "candidate_checkpoint_watermark": self.candidate_checkpoint_watermark,
            "as_of": self.as_of.isoformat(),
            "policy": self.policy.canonical_facts(),
            "tolerance_policy_fingerprint": self.policy.tolerance_policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ShadowDiffFact:
    fact_name: str
    kind: ShadowDiffKind
    severity: ShadowDiffSeverity
    legacy: ShadowFactValue | None = None
    candidate: ShadowFactValue | None = None
    detail: str = ""
    diff_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_name", _text(self.fact_name, "fact_name", max_length=100))
        if not isinstance(self.kind, ShadowDiffKind) or not isinstance(self.severity, ShadowDiffSeverity):
            raise ShadowDiffContractError("kind and severity must use canonical enums")
        if self.legacy is not None and not isinstance(self.legacy, ShadowFactValue):
            raise ShadowDiffContractError("legacy must be a ShadowFactValue")
        if self.candidate is not None and not isinstance(self.candidate, ShadowFactValue):
            raise ShadowDiffContractError("candidate must be a ShadowFactValue")
        object.__setattr__(self, "detail", _text(self.detail or "none", "detail", max_length=160))
        object.__setattr__(self, "diff_fingerprint", _fingerprint({
            "version": SHADOW_DIFF_CONTRACT_VERSION, "fact_name": self.fact_name,
            "kind": self.kind.value, "severity": self.severity.value,
            "legacy": None if self.legacy is None else self.legacy.canonical_facts(),
            "candidate": None if self.candidate is None else self.candidate.canonical_facts(),
            "detail": self.detail,
        }))


@dataclass(frozen=True, slots=True)
class ShadowComparisonResult:
    run: ShadowComparisonRun
    legacy: ShadowSourceSnapshot
    candidate: ShadowSourceSnapshot
    exact_matches: tuple[str, ...]
    tolerated_matches: tuple[str, ...]
    diffs: tuple[ShadowDiffFact, ...]
    replay_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not all(isinstance(value, expected) for value, expected in ((self.run, ShadowComparisonRun), (self.legacy, ShadowSourceSnapshot), (self.candidate, ShadowSourceSnapshot))):
            raise ShadowDiffContractError("result requires canonical run and source snapshots")
        object.__setattr__(self, "exact_matches", tuple(sorted(self.exact_matches)))
        object.__setattr__(self, "tolerated_matches", tuple(sorted(self.tolerated_matches)))
        object.__setattr__(self, "diffs", tuple(sorted(self.diffs, key=lambda fact: fact.diff_fingerprint)))
        object.__setattr__(self, "replay_fingerprint", _fingerprint({
            "version": SHADOW_DIFF_CONTRACT_VERSION, "run": self.run.run_id,
            "legacy_identity": self.run.legacy_source_identity,
            "legacy_version": self.run.legacy_source_version,
            "legacy_source_fingerprint": self.run.legacy_source_fingerprint,
            "candidate_generation_id": self.run.candidate_generation_id,
            "candidate_consumer_name": self.run.candidate_consumer_name,
            "candidate_generation_build_fingerprint": self.run.candidate_generation_build_fingerprint,
            "candidate_checkpoint_watermark": self.run.candidate_checkpoint_watermark,
            "as_of": self.run.as_of.isoformat(),
            "tolerance_policy": self.run.policy.canonical_facts(),
            "tolerance_policy_fingerprint": self.run.policy.tolerance_policy_fingerprint,
            "build_fingerprint": self.run.build_fingerprint,
            "legacy": self.legacy.source_fingerprint, "candidate": self.candidate.source_fingerprint,
            "exact": self.exact_matches, "tolerated": self.tolerated_matches,
            "diffs": [item.diff_fingerprint for item in self.diffs],
        }))


def _within_tolerance(left: ShadowFactValue, right: ShadowFactValue, policy: ShadowTolerancePolicy) -> bool:
    if left.kind is not right.kind:
        return False
    if left.kind is not ShadowValueKind.RATIO and left.asset != right.asset:
        return False
    difference = abs(left.value - right.value)
    if left.kind is ShadowValueKind.QUANTITY:
        absolute, relative = policy.quantity_absolute, policy.quantity_relative
    elif left.kind is ShadowValueKind.MONETARY:
        absolute, relative = policy.monetary_absolute, policy.monetary_relative
    else:
        return difference <= policy.ratio_absolute
    with localcontext() as context:
        context.prec = 80
        scale = max(abs(left.value), abs(right.value))
        return difference <= absolute or (scale != 0 and difference / scale <= relative)


def compare_shadow_state(run: ShadowComparisonRun, legacy: ShadowSourceSnapshot, candidate: ShadowSourceSnapshot) -> ShadowComparisonResult:
    """Compare exactly the supplied snapshots; unknown or stale facts never match."""

    diffs: list[ShadowDiffFact] = []
    exact: list[str] = []
    tolerated: list[str] = []
    if legacy.canonical_scope() != run.canonical_scope() or candidate.canonical_scope() != run.canonical_scope():
        diffs.append(ShadowDiffFact("scope", ShadowDiffKind.SCOPE_MISMATCH, ShadowDiffSeverity.BLOCKING, detail="run_scope"))
        return ShadowComparisonResult(run, legacy, candidate, (), (), tuple(diffs))
    if (
        legacy.source_name != run.legacy_source_identity
        or legacy.source_version != run.legacy_source_version
        or legacy.source_fingerprint != run.legacy_source_fingerprint
        or candidate.generation_id != run.candidate_generation_id
        or candidate.checkpoint_watermark != run.candidate_checkpoint_watermark
        or legacy.observed_at > run.as_of
        or candidate.observed_at > run.as_of
    ):
        diffs.append(ShadowDiffFact("source_binding", ShadowDiffKind.VERSION_MISMATCH, ShadowDiffSeverity.BLOCKING, detail="run_binding"))
        return ShadowComparisonResult(run, legacy, candidate, (), (), tuple(diffs))
    if legacy.status is not ShadowSourceStatus.READY or candidate.status is not ShadowSourceStatus.READY:
        diffs.append(ShadowDiffFact("source_status", ShadowDiffKind.STALE_SOURCE, ShadowDiffSeverity.BLOCKING, detail="not_ready"))
        return ShadowComparisonResult(run, legacy, candidate, (), (), tuple(diffs))
    for name in sorted(set(legacy.facts) | set(candidate.facts)):
        left, right = legacy.facts.get(name), candidate.facts.get(name)
        if left is None:
            diffs.append(ShadowDiffFact(name, ShadowDiffKind.MISSING_LEGACY, ShadowDiffSeverity.BLOCKING, candidate=right, detail="legacy_missing"))
        elif right is None:
            diffs.append(ShadowDiffFact(name, ShadowDiffKind.MISSING_CANDIDATE, ShadowDiffSeverity.BLOCKING, legacy=left, detail="candidate_missing"))
        elif left.kind is not right.kind:
            diffs.append(ShadowDiffFact(name, ShadowDiffKind.UNSUPPORTED_FACT, ShadowDiffSeverity.BLOCKING, left, right, "kind_mismatch"))
        elif left.kind is not ShadowValueKind.RATIO and left.asset != right.asset:
            diffs.append(ShadowDiffFact(name, ShadowDiffKind.VALUATION_REQUIRED, ShadowDiffSeverity.BLOCKING, left, right, "asset_mismatch"))
        elif left.value == right.value:
            exact.append(name)
        elif _within_tolerance(left, right, run.policy):
            tolerated.append(name)
        else:
            diffs.append(ShadowDiffFact(name, ShadowDiffKind.VALUE_MISMATCH, ShadowDiffSeverity.WARNING, left, right, "outside_tolerance"))
    return ShadowComparisonResult(run, legacy, candidate, tuple(exact), tuple(tolerated), tuple(diffs))
