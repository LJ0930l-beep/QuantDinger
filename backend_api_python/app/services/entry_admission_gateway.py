"""Typed, caller-owned, non-executing canonical entry admission boundary."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from app.domain.canonical_entry_contracts import CanonicalCommandDraft, EntryDisposition, EntryMode, EntryRejection
from app.domain.command_intent_contracts import CommandGraph, CommandGraphDisposition, CommandGraphResult
from app.domain.order_contracts import RiskEffect

class EntryAdmissionError(RuntimeError): pass
class EntryAdmissionConflict(EntryAdmissionError): pass

class EntryAdmissionDisposition(str, Enum):
    DISABLED="DISABLED"; REJECTED="REJECTED"; RISK_REJECTED="RISK_REJECTED"; CREATED="CREATED"; REPLAYED="REPLAYED"
class ReservationDisposition(str, Enum): CREATED="CREATED"; REPLAYED="REPLAYED"
class HardRiskDisposition(str, Enum): CREATED="CREATED"; REPLAYED="REPLAYED"
class OutboxDisposition(str, Enum): CREATED="CREATED"; REPLAYED="REPLAYED"

@dataclass(frozen=True, slots=True)
class ReservationPersistResult:
    reservation_id: str
    disposition: ReservationDisposition
@dataclass(frozen=True, slots=True)
class HardRiskPersistResult:
    allowed: bool
    reservation: ReservationPersistResult | None
    disposition: HardRiskDisposition
@dataclass(frozen=True, slots=True)
class OutboxPersistResult:
    event_id: str
    disposition: OutboxDisposition
@dataclass(frozen=True, slots=True)
class EntryAdmissionResult:
    disposition: EntryAdmissionDisposition; mode: EntryMode
    command_id: str | None=None; intent_id: str | None=None; economic_order_id: str | None=None
    rejection: EntryRejection | None=None

class CanonicalCommandMapper(Protocol):
    def map(self, draft: CanonicalCommandDraft) -> CommandGraph: ...
class CommandGraphPort(Protocol):
    def persist_command_graph(self, connection: object, graph: CommandGraph) -> CommandGraphResult: ...
class HardRiskPort(Protocol):
    def persist_for_admission(self, connection: object, draft: CanonicalCommandDraft, graph: CommandGraph) -> HardRiskPersistResult: ...
class OutboxPort(Protocol):
    def persist_admission(self, connection: object, draft: CanonicalCommandDraft, graph: CommandGraph) -> OutboxPersistResult: ...

class CanonicalEntryAdmissionGateway:
    def __init__(self, *, mapper: CanonicalCommandMapper, command_graphs: CommandGraphPort, hard_risk: HardRiskPort, outbox: OutboxPort) -> None:
        self._mapper, self._command_graphs, self._hard_risk, self._outbox = mapper, command_graphs, hard_risk, outbox
    def admit(self, connection: object, draft: CanonicalCommandDraft) -> EntryAdmissionResult:
        if not isinstance(draft, CanonicalCommandDraft): raise EntryAdmissionError("gateway accepts CanonicalCommandDraft only")
        if draft.disposition is EntryDisposition.REJECTED:
            return EntryAdmissionResult(EntryAdmissionDisposition.REJECTED, draft.request.mode, rejection=draft.rejection)
        if draft.request.mode is EntryMode.DISABLED:
            return EntryAdmissionResult(EntryAdmissionDisposition.DISABLED, draft.request.mode)
        graph=self._mapper.map(draft); self._validate_graph(draft, graph)
        command=self._command_graphs.persist_command_graph(connection, graph)
        risk=self._hard_risk.persist_for_admission(connection,draft,graph)
        if not isinstance(risk, HardRiskPersistResult):
            raise EntryAdmissionConflict("hard risk port returned an untyped result")
        if not risk.allowed:
            if risk.reservation is not None: raise EntryAdmissionConflict("denied risk result cannot reserve")
            return EntryAdmissionResult(EntryAdmissionDisposition.RISK_REJECTED,draft.request.mode,command.command_id,command.intent_id,command.economic_order_id)
        increase=draft.request.risk_effect is RiskEffect.INCREASE_RISK
        if increase and risk.reservation is None: raise EntryAdmissionConflict("allowed increase requires authoritative reservation")
        if not increase and risk.reservation is not None: raise EntryAdmissionConflict("non-increase cannot reserve")
        outbox=self._outbox.persist_admission(connection,draft,graph)
        if not isinstance(outbox, OutboxPersistResult):
            raise EntryAdmissionConflict("outbox port returned an untyped result")
        replayed=(command.disposition is CommandGraphDisposition.REPLAYED and risk.disposition is HardRiskDisposition.REPLAYED and outbox.disposition is OutboxDisposition.REPLAYED)
        return EntryAdmissionResult(EntryAdmissionDisposition.REPLAYED if replayed else EntryAdmissionDisposition.CREATED,draft.request.mode,command.command_id,command.intent_id,command.economic_order_id)
    def _validate_graph(self,draft:CanonicalCommandDraft,graph:CommandGraph)->None:
        if not isinstance(graph, CommandGraph):
            raise EntryAdmissionConflict("mapper must return CommandGraph")
        r=draft.request; c,i=graph.command,graph.intent
        if (c.tenant_id,c.credential_id,c.account_scope,c.action,c.actor_type,c.actor_id,c.source,c.idempotency_key,c.correlation_id,i.instrument_id,i.market_type)!=(r.tenant_id,r.credential_id,r.account_scope,r.action,r.actor.actor_type,r.actor.actor_id,r.actor.entry_source.value.lower(),r.idempotency_key,r.correlation_id,r.instrument_id,r.market_type):
            raise EntryAdmissionConflict("mapper graph does not match canonical draft")
        if (c.request_payload.get("canonical_request_fingerprint")!=r.request_fingerprint
            or c.request_payload.get("economic_fingerprint")!=r.economic_fingerprint
            or c.request_payload.get("intent_payload_hash")!=i.payload_hash
            or c.request_payload.get("risk_effect")!=r.risk_effect.value):
            raise EntryAdmissionConflict("mapper fingerprint does not match canonical draft")
