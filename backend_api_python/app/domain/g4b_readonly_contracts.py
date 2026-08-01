"""Pure G4-B read-only chain validation contracts.

This module validates the deterministic, non-trading chain from an Admission
Outbox event through projection, candidate shadow binding, and reconciliation.
It performs no I/O and exposes no order, reservation, or execution operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json

from app.domain.entry_admission_v2_contracts import AdmissionOutboxEventFactV2, EntryAdmissionError, parse_admission_outbox_event
from app.domain.outbox_projection_contracts import OutboxEvent
from app.domain.projection_consumer_contracts import ProjectionConsumeResult
from app.domain.projection_mapping_contracts import CandidateProjectionFacts, ProjectionMappingError, map_admission_outbox_to_projection
from app.domain.candidate_shadow_contracts import CandidateProjectionGeneration, CandidateShadowBinding
from app.domain.shadow_diff_contracts import ShadowComparisonResult
from app.domain.reconciliation_contracts import ReconciliationResult


G4B_READONLY_CONTRACT_VERSION = "g4b-readonly-chain-v1"


class G4BReadonlyContractError(ValueError):
    """The supplied read-only chain facts are incomplete or inconsistent."""


def _fingerprint(material: object) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class G4BReadonlyChainReceipt:
    """An immutable receipt proving one complete read-only chain."""

    event: OutboxEvent
    admission: AdmissionOutboxEventFactV2
    projection: CandidateProjectionFacts
    consume: ProjectionConsumeResult
    candidate: CandidateProjectionGeneration
    shadow: CandidateShadowBinding
    shadow_result: ShadowComparisonResult
    reconciliation: ReconciliationResult
    chain_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event, OutboxEvent):
            raise G4BReadonlyContractError("event must be an OutboxEvent")
        if not isinstance(self.admission, AdmissionOutboxEventFactV2):
            raise G4BReadonlyContractError("admission must be a typed parsed event")
        if not isinstance(self.projection, CandidateProjectionFacts):
            raise G4BReadonlyContractError("projection must use CandidateProjectionFacts")
        if not isinstance(self.consume, ProjectionConsumeResult):
            raise G4BReadonlyContractError("consume must use ProjectionConsumeResult")
        if not isinstance(self.candidate, CandidateProjectionGeneration):
            raise G4BReadonlyContractError("candidate must use CandidateProjectionGeneration")
        if not isinstance(self.shadow, CandidateShadowBinding):
            raise G4BReadonlyContractError("shadow must use CandidateShadowBinding")
        if not isinstance(self.shadow_result, ShadowComparisonResult):
            raise G4BReadonlyContractError("shadow_result must use ShadowComparisonResult")
        if not isinstance(self.reconciliation, ReconciliationResult):
            raise G4BReadonlyContractError("reconciliation must use ReconciliationResult")

        mapped = map_admission_outbox_to_projection(self.event)
        if self.admission != parse_admission_outbox_event(self.event):
            raise G4BReadonlyContractError("admission parser result is not canonical")
        if self.projection != mapped:
            raise G4BReadonlyContractError("projection does not losslessly map the event")
        request = self.consume.request
        if request.event.event_id != self.event.event_id or request.event.payload_hash != self.event.payload_hash:
            raise G4BReadonlyContractError("consumer request is not bound to the event")
        if request.generation_id != self.candidate.binding.generation_id:
            raise G4BReadonlyContractError("consumer request generation is not the candidate generation")
        if request.consumer.consumer_name != self.candidate.binding.consumer_name:
            raise G4BReadonlyContractError("consumer request is not bound to the candidate consumer")
        if self.consume.resulting_checkpoint_version != self.candidate.binding.checkpoint_watermark:
            raise G4BReadonlyContractError("consumer checkpoint does not prove candidate high watermark")
        if not any(item.event_id == self.projection.event_id and item.fingerprint == self.projection.fingerprint for item in self.candidate.facts):
            raise G4BReadonlyContractError("candidate generation does not contain the mapped event")
        if self.shadow.generation is not self.candidate:
            raise G4BReadonlyContractError("shadow binding must use the supplied candidate generation")
        if self.shadow_result.run != self.shadow.run:
            raise G4BReadonlyContractError("shadow result run is not bound to the shadow request")
        if self.shadow_result.candidate.generation_id != self.candidate.binding.generation_id:
            raise G4BReadonlyContractError("shadow result candidate is not bound to the candidate generation")
        if self.reconciliation.run.correlation_id == "":
            raise G4BReadonlyContractError("reconciliation correlation must be canonical")
        object.__setattr__(self, "chain_fingerprint", _fingerprint({
            "contract_version": G4B_READONLY_CONTRACT_VERSION,
            "event_id": self.event.event_id,
            "payload_hash": self.event.payload_hash,
            "admission": {
                "command_id": self.admission.command_id,
                "action": self.admission.action.value,
                "risk_effect": self.admission.risk_effect.value,
                "economic_fingerprint": self.admission.economic_fingerprint,
                "request_fingerprint": self.admission.request_fingerprint,
            },
            "projection": self.projection.fingerprint,
            "consume": self.consume.fingerprint,
            "candidate": self.candidate.projection_fingerprint,
            "shadow": self.shadow.binding_fingerprint,
            "shadow_result": self.shadow_result.replay_fingerprint,
            "reconciliation": self.reconciliation.replay_fingerprint,
        }))

    @property
    def is_read_only(self) -> bool:
        return True


def validate_g4b_readonly_chain(
    event: OutboxEvent,
    consume: ProjectionConsumeResult,
    candidate: CandidateProjectionGeneration,
    shadow: CandidateShadowBinding,
    shadow_result: ShadowComparisonResult,
    reconciliation: ReconciliationResult,
) -> G4BReadonlyChainReceipt:
    """Parse and validate the full chain without persistence or side effects."""

    try:
        admission = parse_admission_outbox_event(event)
    except (EntryAdmissionError, ProjectionMappingError, TypeError) as exc:
        raise G4BReadonlyContractError("admission event is not a canonical typed event") from exc
    return G4BReadonlyChainReceipt(event, admission, map_admission_outbox_to_projection(event), consume, candidate, shadow, shadow_result, reconciliation)


__all__ = ["G4B_READONLY_CONTRACT_VERSION", "G4BReadonlyChainReceipt", "G4BReadonlyContractError", "validate_g4b_readonly_chain"]
