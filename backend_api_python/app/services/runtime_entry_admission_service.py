"""Caller-owned Runtime Entry V1 composition into Canonical Entry admission.

This service contains no HTTP parsing, connection creation, commit, rollback,
executor, exchange client, or order-submission call.  Its caller supplies one
connection and is responsible for committing or rolling back all persistence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.canonical_entry_contracts import EntryMode, EntrySource
from app.domain.entry_admission_v2_contracts import EntryAdmissionDisposition, EntryAdmissionResultV2
from app.domain.entrypoint_v2_binding_contracts import bind_entrypoint_v2
from app.domain.runtime_entry_admission_contracts import (
    RuntimeEntryAdmissionDisposition,
    RuntimeEntryAdmissionResult,
)
from app.domain.runtime_entry_authority_persistence_contracts import RuntimeEntryIngressPersistDisposition
from app.domain.runtime_entry_ingress_contracts import (
    AuthoritativeIngressScope,
    RuntimeEntryIngressV1,
    RuntimeEntryIngressError,
    RuntimeIngressPrincipal,
    build_runtime_entry_request,
    derive_durable_entry_identity,
)
from app.domain.runtime_entry_resolution_contracts import RuntimeEntryResolutionError
from app.services.runtime_entry_authority_repository import RuntimeEntryAuthorityRepository


class RuntimeEntryAdmissionError(RuntimeError):
    """Typed Runtime Entry composition failure; transaction ownership stays outside."""


class RuntimeAuthorityPort(Protocol):
    def resolve(self, connection: object, ingress: RuntimeEntryIngressV1, principal: RuntimeIngressPrincipal): ...
    def persist_ingress(self, connection: object, graph: object, authority: object): ...


class AdmissionPort(Protocol):
    def admit(self, connection: object, graph: object) -> EntryAdmissionResultV2: ...


class RuntimeEntryAdmissionService:
    """Convert authorized V1 ingress to one V2 admission on a supplied transaction."""

    def __init__(
        self,
        *,
        authorities: RuntimeAuthorityPort | None = None,
        admissions: AdmissionPort,
    ) -> None:
        self._authorities = authorities or RuntimeEntryAuthorityRepository()
        self._admissions = admissions

    def admit(
        self,
        connection: object,
        ingress: RuntimeEntryIngressV1,
        principal: RuntimeIngressPrincipal,
        *,
        correlation_id: str,
        occurred_at: datetime,
        mode: EntryMode,
    ) -> RuntimeEntryAdmissionResult:
        if not isinstance(ingress, RuntimeEntryIngressV1) or not isinstance(principal, RuntimeIngressPrincipal):
            raise RuntimeEntryAdmissionError("runtime admission requires typed ingress and principal")
        if not isinstance(mode, EntryMode):
            raise RuntimeEntryAdmissionError("runtime admission mode must be typed")
        if mode is EntryMode.DISABLED:
            return RuntimeEntryAdmissionResult(RuntimeEntryAdmissionDisposition.DISABLED, None, None)
        try:
            authority = self._authorities.resolve(connection, ingress, principal)
            request = build_runtime_entry_request(
                ingress,
                principal=principal,
                scope=authority.facts.scope,
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                mode=mode,
            )
            graph = self._bind(request, ingress, principal, authority.facts.scope)
            admission = self._admissions.admit(connection, graph)
            if not isinstance(admission, EntryAdmissionResultV2):
                raise RuntimeEntryAdmissionError("admission port returned an untyped receipt")
            persisted = self._authorities.persist_ingress(connection, graph, authority)
        except (RuntimeEntryIngressError, RuntimeEntryResolutionError, RuntimeEntryAdmissionError):
            raise
        except Exception as exc:
            raise RuntimeEntryAdmissionError("runtime entry admission failed") from exc
        return RuntimeEntryAdmissionResult(self._disposition(admission, persisted), admission, persisted)

    @staticmethod
    def _bind(request, ingress: RuntimeEntryIngressV1, principal: RuntimeIngressPrincipal, scope: AuthoritativeIngressScope):
        return bind_entrypoint_v2(
            principal.source,
            request,
            derive_durable_entry_identity(ingress, principal=principal, scope=scope),
        )

    @staticmethod
    def _disposition(admission: EntryAdmissionResultV2, persisted: object) -> RuntimeEntryAdmissionDisposition:
        if admission.disposition is EntryAdmissionDisposition.RISK_REJECTED:
            return RuntimeEntryAdmissionDisposition.RISK_REJECTED
        if admission.disposition is EntryAdmissionDisposition.REPLAYED and persisted.disposition is RuntimeEntryIngressPersistDisposition.REPLAYED:
            return RuntimeEntryAdmissionDisposition.REPLAYED
        return RuntimeEntryAdmissionDisposition.CREATED
