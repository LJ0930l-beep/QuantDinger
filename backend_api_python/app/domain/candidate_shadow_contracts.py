"""Pure Candidate Projection generation and Shadow binding contracts.

The projection repository remains the authority for persisted generation rows.
This module only validates a repository-returned generation view, immutable
candidate facts, and a read-only binding to the existing Shadow Diff contracts.
It does not create a generation, persist a projection, or make a trade decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Protocol, Tuple
from uuid import UUID

from app.domain.projection_consumer_contracts import RegisteredProjectionConsumer
from app.domain.projection_mapping_contracts import CandidateProjectionFacts
from app.domain.shadow_diff_contracts import (
    ShadowComparisonResult,
    ShadowComparisonRun,
    ShadowDiffContractError,
    ShadowSourceSnapshot,
    ShadowRunState,
    compare_shadow_state,
)


CANDIDATE_GENERATION_CONTRACT_VERSION = "candidate-generation-v1"


class CandidateShadowContractError(ValueError):
    """Base typed failure for candidate generation/shadow binding facts."""


class CandidateGenerationConflict(CandidateShadowContractError):
    """Generation, consumer, candidate facts, or snapshot do not match."""


class CandidateShadowReadOnlyError(CandidateShadowContractError):
    """A caller attempted to use the shadow contract as a trading decision."""


class ProjectionGenerationView(Protocol):
    """Structural view returned by the existing projection repository."""

    generation_id: str
    consumer_name: str
    build_fingerprint: str
    source_high_watermark: int
    processed_high_watermark: int
    state: str


def _uuid(value: object, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value))).lower()
    except (TypeError, ValueError, AttributeError) as exc:
        raise CandidateGenerationConflict(f"{field_name} must be a UUID") from exc


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise CandidateGenerationConflict(f"{field_name} must be lowercase SHA-256")
    return value


def _fingerprint(material: object) -> str:
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateGenerationBinding:
    """Exact immutable binding to one READY persisted projection generation."""

    generation_id: str
    consumer_name: str
    consumer: RegisteredProjectionConsumer
    build_fingerprint: str
    checkpoint_watermark: int
    state: str = "READY"
    binding_fingerprint: str = field(init=False)

    @classmethod
    def from_generation(
        cls,
        generation: ProjectionGenerationView,
        consumer: RegisteredProjectionConsumer,
    ) -> "CandidateGenerationBinding":
        if generation is None or not isinstance(consumer, RegisteredProjectionConsumer):
            raise CandidateGenerationConflict("generation and consumer must be typed")
        try:
            values = {
                "generation_id": generation.generation_id,
                "consumer_name": generation.consumer_name,
                "consumer": consumer,
                "build_fingerprint": generation.build_fingerprint,
                "checkpoint_watermark": generation.source_high_watermark,
                "state": generation.state,
            }
            processed = generation.processed_high_watermark
        except AttributeError as exc:
            raise CandidateGenerationConflict("generation view is incomplete") from exc
        if processed != values["checkpoint_watermark"]:
            raise CandidateGenerationConflict("generation watermark is not complete")
        return cls(**values)

    def __post_init__(self) -> None:
        object.__setattr__(self, "generation_id", _uuid(self.generation_id, "generation_id"))
        if not isinstance(self.consumer_name, str) or not self.consumer_name or self.consumer_name != self.consumer_name.strip() or not self.consumer_name.isascii() or self.consumer_name != self.consumer_name.lower():
            raise CandidateGenerationConflict("consumer_name must be lowercase ASCII text")
        if not isinstance(self.consumer, RegisteredProjectionConsumer):
            raise CandidateGenerationConflict("consumer must be registered")
        if self.consumer.consumer_name != self.consumer_name:
            raise CandidateGenerationConflict("generation consumer does not match registered consumer")
        object.__setattr__(self, "build_fingerprint", _sha(self.build_fingerprint, "build_fingerprint"))
        if not self.consumer.build_fingerprint or self.consumer.build_fingerprint != self.build_fingerprint:
            raise CandidateGenerationConflict("generation build fingerprint does not match consumer")
        if isinstance(self.checkpoint_watermark, bool) or not isinstance(self.checkpoint_watermark, int) or self.checkpoint_watermark < 0:
            raise CandidateGenerationConflict("checkpoint_watermark must be non-negative")
        if self.state != "READY":
            raise CandidateGenerationConflict("candidate generation must be READY")
        object.__setattr__(self, "binding_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, object]:
        return {
            "contract_version": CANDIDATE_GENERATION_CONTRACT_VERSION,
            "generation_id": self.generation_id,
            "consumer_name": self.consumer_name,
            "consumer_fingerprint": self.consumer.fingerprint,
            "build_fingerprint": self.build_fingerprint,
            "checkpoint_watermark": self.checkpoint_watermark,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class CandidateProjectionGeneration:
    """Immutable candidate facts associated with one READY generation."""

    binding: CandidateGenerationBinding
    facts: Tuple[CandidateProjectionFacts, ...]
    source_snapshot: ShadowSourceSnapshot
    projection_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.binding, CandidateGenerationBinding):
            raise CandidateGenerationConflict("candidate generation requires a typed binding")
        if not isinstance(self.source_snapshot, ShadowSourceSnapshot):
            raise CandidateGenerationConflict("candidate generation requires a shadow source snapshot")
        if self.source_snapshot.status.value != "READY":
            raise CandidateGenerationConflict("candidate source snapshot must be READY")
        if self.source_snapshot.generation_id != self.binding.generation_id or self.source_snapshot.checkpoint_watermark != self.binding.checkpoint_watermark:
            raise CandidateGenerationConflict("candidate snapshot generation binding is not exact")
        values = tuple(self.facts)
        if any(not isinstance(item, CandidateProjectionFacts) for item in values):
            raise CandidateGenerationConflict("candidate facts must use CandidateProjectionFacts")
        event_ids = tuple(item.event_id for item in values)
        if len(set(event_ids)) != len(event_ids):
            raise CandidateGenerationConflict("candidate event identities must be unique")
        scope = self.source_snapshot.canonical_scope()
        for item in values:
            if (item.tenant_id, item.credential_id, item.account_scope, item.instrument_id, item.market_type) != scope:
                raise CandidateGenerationConflict("candidate facts do not match snapshot scope")
        canonical = tuple(sorted(values, key=lambda item: (item.event_id, item.fingerprint)))
        object.__setattr__(self, "facts", canonical)
        object.__setattr__(self, "projection_fingerprint", _fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, object]:
        return {
            "contract_version": CANDIDATE_GENERATION_CONTRACT_VERSION,
            "binding": self.binding.binding_fingerprint,
            "source_snapshot": self.source_snapshot.source_fingerprint,
            "facts": [item.fingerprint for item in self.facts],
        }


@dataclass(frozen=True, slots=True)
class CandidateShadowBinding:
    """Read-only exact binding between a candidate generation and Shadow run."""

    generation: CandidateProjectionGeneration
    run: ShadowComparisonRun
    binding_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.generation, CandidateProjectionGeneration) or not isinstance(self.run, ShadowComparisonRun):
            raise CandidateGenerationConflict("shadow binding requires typed generation and run")
        binding = self.generation.binding
        run = self.run
        if run.state is not ShadowRunState.BUILDING:
            raise CandidateGenerationConflict("only BUILDING shadow runs may be bound")
        if (
            run.candidate_generation_id != binding.generation_id
            or run.candidate_consumer_name != binding.consumer_name
            or run.candidate_generation_build_fingerprint != binding.build_fingerprint
            or run.candidate_checkpoint_watermark != binding.checkpoint_watermark
        ):
            raise CandidateGenerationConflict("shadow run does not exactly bind candidate generation")
        if self.generation.source_snapshot.canonical_scope() != run.canonical_scope():
            raise CandidateGenerationConflict("candidate generation scope does not match shadow run")
        object.__setattr__(self, "binding_fingerprint", _fingerprint({
            "contract_version": CANDIDATE_GENERATION_CONTRACT_VERSION,
            "generation": self.generation.projection_fingerprint,
            "run_build": run.build_fingerprint,
            "candidate_source": self.generation.source_snapshot.source_fingerprint,
        }))


def bind_candidate_shadow(run: ShadowComparisonRun, generation: CandidateProjectionGeneration) -> CandidateShadowBinding:
    """Validate and return a read-only candidate-to-shadow binding."""

    return CandidateShadowBinding(generation=generation, run=run)


def compare_bound_candidate_shadow(
    binding: CandidateShadowBinding,
    legacy: ShadowSourceSnapshot,
) -> ShadowComparisonResult:
    """Compare supplied snapshots only; this function cannot admit or trade."""

    if not isinstance(binding, CandidateShadowBinding) or not isinstance(legacy, ShadowSourceSnapshot):
        raise CandidateShadowContractError("typed candidate shadow binding and legacy snapshot are required")
    try:
        return compare_shadow_state(binding.run, legacy, binding.generation.source_snapshot)
    except ShadowDiffContractError as exc:
        raise CandidateShadowContractError("shadow comparison facts are invalid") from exc


__all__ = [
    "CANDIDATE_GENERATION_CONTRACT_VERSION",
    "CandidateGenerationBinding",
    "CandidateGenerationConflict",
    "CandidateProjectionGeneration",
    "CandidateShadowBinding",
    "CandidateShadowContractError",
    "CandidateShadowReadOnlyError",
    "ProjectionGenerationView",
    "bind_candidate_shadow",
    "compare_bound_candidate_shadow",
]
