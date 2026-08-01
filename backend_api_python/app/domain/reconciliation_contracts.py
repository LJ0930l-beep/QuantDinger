"""Pure, deterministic reconciliation and derived-health contracts.

This module accepts only normalized caller supplied facts.  It has no database,
exchange, worker, scheduler, order-decision, or runtime dependency.  Persisted
``ReconciliationCheckpoint`` facts are the sole authority from which health is
derived; callers cannot supply a health value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from app.domain.decimal_values import DecimalInput, DecimalInputTypeError, canonical_decimal_string, validate_numeric_38_18
from app.domain.order_contracts import ReconciliationCheckpointStatus, ReconciliationHealth, derive_reconciliation_health


RECONCILIATION_CONTRACT_VERSION = "reconciliation-v1"


class ReconciliationContractError(ValueError):
    """Raised when caller-provided reconciliation facts are unsafe or incomplete."""


class ReconciliationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class ReconciliationDiscrepancyKind(str, Enum):
    MISSING_LOCAL = "MISSING_LOCAL"
    MISSING_EXTERNAL = "MISSING_EXTERNAL"
    ORDER_STATE_MISMATCH = "ORDER_STATE_MISMATCH"
    UNKNOWN_SUBMISSION = "UNKNOWN_SUBMISSION"
    FILL_MISSING = "FILL_MISSING"
    FILL_UNEXPECTED = "FILL_UNEXPECTED"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    BALANCE_MISMATCH = "BALANCE_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"
    STALE_LOCAL = "STALE_LOCAL"
    STALE_EXTERNAL = "STALE_EXTERNAL"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    UNSUPPORTED_FACT = "UNSUPPORTED_FACT"


class ReconciliationRunState(str, Enum):
    BUILDING = "BUILDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class ReconciliationFactKind(str, Enum):
    QUANTITY = "QUANTITY"
    MONETARY = "MONETARY"
    RATIO = "RATIO"


def _text(value: object, field_name: str, *, uppercase: bool = False, lowercase: bool = False, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii() or len(value) > max_length:
        raise ReconciliationContractError(f"{field_name} must be canonical ASCII text")
    if uppercase and value != value.upper():
        raise ReconciliationContractError(f"{field_name} must be uppercase")
    if lowercase and value != value.lower():
        raise ReconciliationContractError(f"{field_name} must be lowercase")
    return value


def _uuid(value: UUID | str, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(value)).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReconciliationContractError(f"{field_name} must be a UUID") from exc


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReconciliationContractError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


def _decimal(value: DecimalInput, field_name: str, *, non_negative: bool = True) -> Decimal:
    try:
        parsed = validate_numeric_38_18(value)
    except (ValueError, TypeError, DecimalInputTypeError) as exc:
        raise ReconciliationContractError(f"{field_name} must satisfy NUMERIC(38,18)") from exc
    if non_negative and parsed < 0:
        raise ReconciliationContractError(f"{field_name} cannot be negative")
    return parsed


def _fingerprint(facts: object) -> str:
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReconciliationPolicySnapshot:
    policy_version: str
    warning_degrades_health: bool
    quantity_absolute: DecimalInput = Decimal(0)
    monetary_absolute: DecimalInput = Decimal(0)
    max_observation_age_seconds: int = 0
    policy_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version", max_length=64))
        if not isinstance(self.warning_degrades_health, bool):
            raise ReconciliationContractError("warning_degrades_health must be boolean")
        object.__setattr__(self, "quantity_absolute", _decimal(self.quantity_absolute, "quantity_absolute"))
        object.__setattr__(self, "monetary_absolute", _decimal(self.monetary_absolute, "monetary_absolute"))
        if isinstance(self.max_observation_age_seconds, bool) or not isinstance(self.max_observation_age_seconds, int) or self.max_observation_age_seconds < 0:
            raise ReconciliationContractError("max_observation_age_seconds must be non-negative")
        object.__setattr__(self, "policy_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, str | bool]:
        return {
            "version": RECONCILIATION_CONTRACT_VERSION,
            "policy_version": self.policy_version,
            "warning_degrades_health": self.warning_degrades_health,
            "quantity_absolute": canonical_decimal_string(self.quantity_absolute),
            "monetary_absolute": canonical_decimal_string(self.monetary_absolute),
            "max_observation_age_seconds": self.max_observation_age_seconds,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationFactValue:
    value: DecimalInput
    kind: ReconciliationFactKind
    asset: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _decimal(self.value, "value", non_negative=False))
        if not isinstance(self.kind, ReconciliationFactKind):
            raise ReconciliationContractError("kind must use ReconciliationFactKind")
        if self.kind is ReconciliationFactKind.RATIO:
            if self.asset is not None:
                raise ReconciliationContractError("ratio facts cannot carry an asset")
        else:
            object.__setattr__(self, "asset", _text(self.asset, "asset", uppercase=True, max_length=24))

    def canonical_facts(self) -> dict[str, str | None]:
        return {"value": canonical_decimal_string(self.value), "kind": self.kind.value, "asset": self.asset}


@dataclass(frozen=True, slots=True)
class ReconciliationSourceSnapshot:
    source_identity: str
    source_version: str
    tenant_id: int
    credential_id: int
    account_scope: str
    venue: str
    market_type: str
    instrument_id: str | None
    asset_scope: str | None
    observed_at: datetime
    facts: Mapping[str, ReconciliationFactValue]
    generation_id: UUID | str | None = None
    checkpoint_watermark: int | None = None
    source_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identity", _text(self.source_identity, "source_identity", lowercase=True, max_length=64))
        object.__setattr__(self, "source_version", _text(self.source_version, "source_version", max_length=64))
        for name in ("tenant_id", "credential_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ReconciliationContractError(f"{name} must be positive")
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "venue", _text(self.venue, "venue", lowercase=True, max_length=64))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lowercase=True, max_length=20))
        if self.instrument_id is None and self.asset_scope is None:
            raise ReconciliationContractError("instrument_id or asset_scope is required")
        if self.instrument_id is not None:
            object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", uppercase=True, max_length=100))
        if self.asset_scope is not None:
            object.__setattr__(self, "asset_scope", _text(self.asset_scope, "asset_scope", uppercase=True, max_length=24))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if (self.generation_id is None) != (self.checkpoint_watermark is None):
            raise ReconciliationContractError("generation_id and checkpoint_watermark must be supplied together")
        if self.generation_id is not None:
            object.__setattr__(self, "generation_id", _uuid(self.generation_id, "generation_id"))
            if isinstance(self.checkpoint_watermark, bool) or not isinstance(self.checkpoint_watermark, int) or self.checkpoint_watermark < 0:
                raise ReconciliationContractError("checkpoint_watermark must be non-negative")
        canonical: dict[str, ReconciliationFactValue] = {}
        for key, value in self.facts.items():
            if not isinstance(value, ReconciliationFactValue):
                raise ReconciliationContractError("facts must use ReconciliationFactValue")
            canonical[_text(key, "fact_name", max_length=100)] = value
        object.__setattr__(self, "facts", MappingProxyType(dict(sorted(canonical.items()))))
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_scope(self) -> tuple[int, int, str, str, str, str | None, str | None]:
        return (self.tenant_id, self.credential_id, self.account_scope, self.venue, self.market_type, self.instrument_id, self.asset_scope)

    def canonical_facts(self) -> dict[str, object]:
        return {
            "version": RECONCILIATION_CONTRACT_VERSION,
            "source_identity": self.source_identity,
            "source_version": self.source_version,
            "scope": self.canonical_scope(),
            "observed_at": self.observed_at.isoformat(),
            "generation_id": self.generation_id,
            "checkpoint_watermark": self.checkpoint_watermark,
            "facts": {name: value.canonical_facts() for name, value in self.facts.items()},
        }


@dataclass(frozen=True, slots=True)
class ReconciliationRun:
    run_id: UUID | str
    tenant_id: int
    credential_id: int
    account_scope: str
    venue: str
    market_type: str
    instrument_id: str | None
    asset_scope: str | None
    local_generation_id: UUID | str
    local_consumer_name: str
    local_generation_build_fingerprint: str
    local_checkpoint_watermark: int
    external_observation_identity: str
    external_observation_version: str
    external_observation_fingerprint: str
    local_observed_at: datetime
    external_observed_at: datetime
    as_of: datetime
    correlation_id: str
    policy: ReconciliationPolicySnapshot
    state: ReconciliationRunState = ReconciliationRunState.BUILDING
    build_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        for name in ("tenant_id", "credential_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ReconciliationContractError(f"{name} must be positive")
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "venue", _text(self.venue, "venue", lowercase=True, max_length=64))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", lowercase=True, max_length=20))
        if self.instrument_id is None and self.asset_scope is None:
            raise ReconciliationContractError("instrument_id or asset_scope is required")
        if self.instrument_id is not None:
            object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", uppercase=True, max_length=100))
        if self.asset_scope is not None:
            object.__setattr__(self, "asset_scope", _text(self.asset_scope, "asset_scope", uppercase=True, max_length=24))
        object.__setattr__(self, "local_generation_id", _uuid(self.local_generation_id, "local_generation_id"))
        object.__setattr__(self, "local_consumer_name", _text(self.local_consumer_name, "local_consumer_name", lowercase=True, max_length=160))
        for name in ("local_generation_build_fingerprint", "external_observation_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ReconciliationContractError(f"{name} must be SHA-256")
        object.__setattr__(self, "external_observation_identity", _text(self.external_observation_identity, "external_observation_identity", lowercase=True, max_length=64))
        object.__setattr__(self, "external_observation_version", _text(self.external_observation_version, "external_observation_version", max_length=64))
        if isinstance(self.local_checkpoint_watermark, bool) or not isinstance(self.local_checkpoint_watermark, int) or self.local_checkpoint_watermark < 0:
            raise ReconciliationContractError("local_checkpoint_watermark must be non-negative")
        for name in ("local_observed_at", "external_observed_at", "as_of"):
            object.__setattr__(self, name, _utc(getattr(self, name), name))
        if self.local_observed_at > self.as_of or self.external_observed_at > self.as_of:
            raise ReconciliationContractError("observation timestamps cannot be after as_of")
        object.__setattr__(self, "correlation_id", _text(self.correlation_id, "correlation_id"))
        if not isinstance(self.policy, ReconciliationPolicySnapshot) or not isinstance(self.state, ReconciliationRunState):
            raise ReconciliationContractError("policy and state must use canonical contracts")
        object.__setattr__(self, "build_fingerprint", _fingerprint(self.canonical_build_facts()))

    def canonical_scope(self) -> tuple[int, int, str, str, str, str | None, str | None]:
        return (self.tenant_id, self.credential_id, self.account_scope, self.venue, self.market_type, self.instrument_id, self.asset_scope)

    def canonical_build_facts(self) -> dict[str, object]:
        """Authoritative identity; correlation remains deliberately audit-only."""
        return {
            "version": RECONCILIATION_CONTRACT_VERSION,
            "scope": self.canonical_scope(),
            "local_generation_id": self.local_generation_id,
            "local_consumer_name": self.local_consumer_name,
            "local_generation_build_fingerprint": self.local_generation_build_fingerprint,
            "local_checkpoint_watermark": self.local_checkpoint_watermark,
            "external_observation_identity": self.external_observation_identity,
            "external_observation_version": self.external_observation_version,
            "external_observation_fingerprint": self.external_observation_fingerprint,
            "local_observed_at": self.local_observed_at.isoformat(),
            "external_observed_at": self.external_observed_at.isoformat(),
            "as_of": self.as_of.isoformat(),
            "policy": self.policy.canonical_facts(),
            "policy_fingerprint": self.policy.policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationDiscrepancy:
    fact_name: str
    kind: ReconciliationDiscrepancyKind
    severity: ReconciliationSeverity
    local: ReconciliationFactValue | None = None
    external: ReconciliationFactValue | None = None
    detail: str = "none"
    discrepancy_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_name", _text(self.fact_name, "fact_name", max_length=100))
        if not isinstance(self.kind, ReconciliationDiscrepancyKind) or not isinstance(self.severity, ReconciliationSeverity):
            raise ReconciliationContractError("kind and severity must use canonical enums")
        if self.local is not None and not isinstance(self.local, ReconciliationFactValue):
            raise ReconciliationContractError("local must use ReconciliationFactValue")
        if self.external is not None and not isinstance(self.external, ReconciliationFactValue):
            raise ReconciliationContractError("external must use ReconciliationFactValue")
        object.__setattr__(self, "detail", _text(self.detail, "detail", max_length=160))
        object.__setattr__(self, "discrepancy_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, object]:
        return {
            "version": RECONCILIATION_CONTRACT_VERSION,
            "fact_name": self.fact_name,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "local": None if self.local is None else self.local.canonical_facts(),
            "external": None if self.external is None else self.external.canonical_facts(),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationCheckpoint:
    run_id: UUID | str
    status: ReconciliationCheckpointStatus
    result_fingerprint: str
    discrepancy_count: int
    policy_fingerprint: str
    checkpoint_version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        if not isinstance(self.status, ReconciliationCheckpointStatus):
            raise ReconciliationContractError("status must use ReconciliationCheckpointStatus")
        for name in ("result_fingerprint", "policy_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ReconciliationContractError(f"{name} must be SHA-256")
        for name in ("discrepancy_count", "checkpoint_version"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReconciliationContractError(f"{name} must be non-negative")

    @property
    def health(self) -> ReconciliationHealth:
        return derive_reconciliation_health(self.status)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    run: ReconciliationRun
    local: ReconciliationSourceSnapshot
    external: ReconciliationSourceSnapshot
    discrepancies: tuple[ReconciliationDiscrepancy, ...]
    replay_fingerprint: str = field(init=False)
    checkpoint: ReconciliationCheckpoint = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, ReconciliationRun) or not isinstance(self.local, ReconciliationSourceSnapshot) or not isinstance(self.external, ReconciliationSourceSnapshot):
            raise ReconciliationContractError("result requires canonical run and source snapshots")
        discrepancies = tuple(sorted(self.discrepancies, key=lambda item: item.discrepancy_fingerprint))
        if any(not isinstance(item, ReconciliationDiscrepancy) for item in discrepancies):
            raise ReconciliationContractError("discrepancies must use ReconciliationDiscrepancy")
        if len({item.discrepancy_fingerprint for item in discrepancies}) != len(discrepancies):
            raise ReconciliationContractError("discrepancy facts must be unique and append-only")
        object.__setattr__(self, "discrepancies", discrepancies)
        self._validate_bindings()
        replay = _fingerprint({
            "version": RECONCILIATION_CONTRACT_VERSION,
            "run": self.run.run_id,
            "build_fingerprint": self.run.build_fingerprint,
            "local": self.local.source_fingerprint,
            "external": self.external.source_fingerprint,
            "discrepancies": [item.discrepancy_fingerprint for item in discrepancies],
        })
        object.__setattr__(self, "replay_fingerprint", replay)
        object.__setattr__(self, "checkpoint", ReconciliationCheckpoint(
            run_id=self.run.run_id,
            status=self._derive_status(),
            result_fingerprint=replay,
            discrepancy_count=len(discrepancies),
            policy_fingerprint=self.run.policy.policy_fingerprint,
        ))

    def _validate_bindings(self) -> None:
        if self.local.canonical_scope() != self.run.canonical_scope() or self.external.canonical_scope() != self.run.canonical_scope():
            raise ReconciliationContractError("source scope must exactly match reconciliation run scope")
        if (
            self.local.generation_id != self.run.local_generation_id
            or self.local.checkpoint_watermark != self.run.local_checkpoint_watermark
        ):
            raise ReconciliationContractError("local source must bind the run generation and checkpoint watermark")
        if self.external.generation_id is not None or self.external.checkpoint_watermark is not None:
            raise ReconciliationContractError("external source cannot claim a local generation checkpoint")
        if self.local.observed_at != self.run.local_observed_at or self.external.observed_at != self.run.external_observed_at:
            raise ReconciliationContractError("source observed_at must exactly match run facts")
        if (self.external.source_identity != self.run.external_observation_identity
                or self.external.source_version != self.run.external_observation_version
                or self.external.source_fingerprint != self.run.external_observation_fingerprint):
            raise ReconciliationContractError("external source facts must exactly match run facts")

    def _derive_status(self) -> ReconciliationCheckpointStatus:
        kinds = {item.kind for item in self.discrepancies}
        if ReconciliationDiscrepancyKind.UNKNOWN_SUBMISSION in kinds or any(item.severity is ReconciliationSeverity.BLOCKING for item in self.discrepancies):
            return ReconciliationCheckpointStatus.CONFLICT
        if ReconciliationDiscrepancyKind.STALE_LOCAL in kinds or ReconciliationDiscrepancyKind.STALE_EXTERNAL in kinds:
            return ReconciliationCheckpointStatus.STALE
        if any(item.severity is ReconciliationSeverity.WARNING for item in self.discrepancies) and self.run.policy.warning_degrades_health:
            return ReconciliationCheckpointStatus.STALE
        return ReconciliationCheckpointStatus.HEALTHY


def compare_reconciliation_state(
    run: ReconciliationRun,
    local: ReconciliationSourceSnapshot,
    external: ReconciliationSourceSnapshot,
) -> ReconciliationResult:
    """Compare supplied facts only; mismatched assets, unknown and stale facts fail closed."""

    discrepancies: list[ReconciliationDiscrepancy] = []
    if local.canonical_scope() != run.canonical_scope() or external.canonical_scope() != run.canonical_scope():
        discrepancies.append(ReconciliationDiscrepancy("scope", ReconciliationDiscrepancyKind.SCOPE_MISMATCH, ReconciliationSeverity.BLOCKING, detail="run_scope"))
        return ReconciliationResult(run, local, external, tuple(discrepancies))
    maximum_age = run.policy.max_observation_age_seconds
    if maximum_age and (run.as_of - local.observed_at).total_seconds() > maximum_age:
        discrepancies.append(ReconciliationDiscrepancy("local", ReconciliationDiscrepancyKind.STALE_LOCAL, ReconciliationSeverity.BLOCKING, detail="stale_local"))
    if maximum_age and (run.as_of - external.observed_at).total_seconds() > maximum_age:
        discrepancies.append(ReconciliationDiscrepancy("external", ReconciliationDiscrepancyKind.STALE_EXTERNAL, ReconciliationSeverity.BLOCKING, detail="stale_external"))
    if not local.facts and not external.facts:
        discrepancies.append(ReconciliationDiscrepancy(
            "facts", ReconciliationDiscrepancyKind.UNSUPPORTED_FACT,
            ReconciliationSeverity.BLOCKING, detail="missing_facts",
        ))
    for name in sorted(set(local.facts) | set(external.facts)):
        left, right = local.facts.get(name), external.facts.get(name)
        if left is None:
            discrepancies.append(ReconciliationDiscrepancy(name, ReconciliationDiscrepancyKind.MISSING_LOCAL, ReconciliationSeverity.BLOCKING, external=right, detail="local_missing"))
        elif right is None:
            discrepancies.append(ReconciliationDiscrepancy(name, ReconciliationDiscrepancyKind.MISSING_EXTERNAL, ReconciliationSeverity.BLOCKING, local=left, detail="external_missing"))
        elif left.kind is not right.kind or (left.kind is not ReconciliationFactKind.RATIO and left.asset != right.asset):
            discrepancies.append(ReconciliationDiscrepancy(name, ReconciliationDiscrepancyKind.SCOPE_MISMATCH, ReconciliationSeverity.BLOCKING, left, right, "asset_or_kind"))
        elif left.value != right.value:
            kind = ReconciliationDiscrepancyKind.BALANCE_MISMATCH if left.kind is ReconciliationFactKind.MONETARY else ReconciliationDiscrepancyKind.POSITION_MISMATCH
            discrepancies.append(ReconciliationDiscrepancy(name, kind, ReconciliationSeverity.WARNING, left, right, "value_mismatch"))
    return ReconciliationResult(run, local, external, tuple(discrepancies))
