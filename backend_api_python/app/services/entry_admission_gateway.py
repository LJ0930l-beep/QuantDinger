"""Caller-owned, non-executing admission skeleton for canonical entries.

It deliberately imports no runtime, worker, exchange, executor or route.  The
gateway only coordinates durable ports supplied by its caller; it never owns a
database transaction and never creates a venue order.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from app.domain.canonical_entry_contracts import CanonicalCommandDraft, EntryDisposition, EntryMode
from app.domain.order_contracts import RiskEffect


class EntryAdmissionError(RuntimeError):
    """Base typed admission failure."""


class EntryAdmissionConflict(EntryAdmissionError):
    """A durable idempotency identity names different admission facts."""


class EntryAdmissionRepositoryError(EntryAdmissionError):
    """A database-driver failure was converted at the admission boundary."""


class EntryAdmissionDisposition(str, Enum):
    DISABLED = "DISABLED"
    RISK_REJECTED = "RISK_REJECTED"
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class EntryAdmissionResult:
    disposition: EntryAdmissionDisposition
    mode: EntryMode
    command_id: str | None = None
    intent_id: str | None = None
    economic_order_id: str | None = None
    rejection: str | None = None


class CanonicalCommandMapper(Protocol):
    def map(self, draft: CanonicalCommandDraft) -> Any: ...


class CommandGraphPort(Protocol):
    def persist_command_graph(self, connection: Any, graph: Any) -> Any: ...


class HardRiskPort(Protocol):
    def persist_for_admission(self, connection: Any, draft: CanonicalCommandDraft, graph: Any) -> Any: ...


class OutboxPort(Protocol):
    def persist_admission(self, connection: Any, draft: CanonicalCommandDraft, graph: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class EntryAdmissionPolicy:
    """No LIVE mode exists in this contract."""

    allowed_modes: tuple[EntryMode, ...] = (EntryMode.DISABLED, EntryMode.PAPER, EntryMode.SHADOW)

    def __post_init__(self) -> None:
        if set(self.allowed_modes) != {EntryMode.DISABLED, EntryMode.PAPER, EntryMode.SHADOW}:
            raise EntryAdmissionError("admission policy must define exactly DISABLED, PAPER and SHADOW")


class CanonicalEntryAdmissionGateway:
    """Coordinate durable non-executing admission facts in a caller transaction."""

    def __init__(self, *, mapper: CanonicalCommandMapper, command_graphs: CommandGraphPort,
                 hard_risk: HardRiskPort, outbox: OutboxPort,
                 policy: EntryAdmissionPolicy | None = None) -> None:
        self._mapper = mapper
        self._command_graphs = command_graphs
        self._hard_risk = hard_risk
        self._outbox = outbox
        self._policy = EntryAdmissionPolicy() if policy is None else policy

    def admit(self, connection: Any, draft: CanonicalCommandDraft) -> EntryAdmissionResult:
        if not isinstance(draft, CanonicalCommandDraft):
            raise EntryAdmissionError("gateway accepts CanonicalCommandDraft only")
        request = draft.request
        if draft.disposition is EntryDisposition.REJECTED or request.mode is EntryMode.DISABLED:
            return EntryAdmissionResult(EntryAdmissionDisposition.DISABLED, request.mode, rejection="DISABLED")
        if request.mode not in (EntryMode.PAPER, EntryMode.SHADOW):
            raise EntryAdmissionError("unsupported admission mode")

        graph = self._mapper.map(draft)
        command_result = self._command_graphs.persist_command_graph(connection, graph)
        risk_result = self._hard_risk.persist_for_admission(connection, draft, graph)
        if not bool(getattr(risk_result, "allowed", False)):
            return EntryAdmissionResult(
                EntryAdmissionDisposition.RISK_REJECTED, request.mode,
                str(getattr(command_result, "command_id", "")) or None,
                str(getattr(command_result, "intent_id", "")) or None,
                str(getattr(command_result, "economic_order_id", "")) or None,
                rejection="HARD_RISK_REJECTED",
            )
        if request.risk_effect is RiskEffect.INCREASE_RISK and not bool(getattr(risk_result, "reservation_persisted", False)):
            raise EntryAdmissionConflict("accepted risk-increasing admission requires a durable reservation")
        if request.risk_effect is not RiskEffect.INCREASE_RISK and bool(getattr(risk_result, "reservation_persisted", False)):
            raise EntryAdmissionConflict("risk-reducing admission cannot create a risk-increase reservation")
        outbox_result = self._outbox.persist_admission(connection, draft, graph)
        replayed = all(bool(getattr(value, "replayed", False)) for value in (command_result, risk_result, outbox_result))
        return EntryAdmissionResult(
            EntryAdmissionDisposition.REPLAYED if replayed else EntryAdmissionDisposition.CREATED,
            request.mode, str(getattr(command_result, "command_id", "")) or None,
            str(getattr(command_result, "intent_id", "")) or None,
            str(getattr(command_result, "economic_order_id", "")) or None,
        )
