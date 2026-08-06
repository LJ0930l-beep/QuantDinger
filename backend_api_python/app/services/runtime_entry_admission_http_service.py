"""Authenticated HTTP composition for the Runtime Entry admission chain.

This is deliberately an admission-only boundary.  It accepts explicit,
typed entry facts, resolves server-owned authority, and composes the durable
entry, hard-risk, reservation, and outbox repositories on one transaction.
It never creates a venue client, submits an order, starts a worker, or enables
LIVE mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.canonical_entry_contracts import (
    EntryMode,
    EntrySource,
    ExecutionKind,
    OrderSide,
    PositionSide,
)
from app.domain.canonical_entry_v2_contracts import (
    CancelTargetKind,
    QuantitySemantics,
    TriggerDirection,
    TriggerPriceType,
)
from app.domain.entry_admission_v2_contracts import EntryAdmissionError
from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2
from app.domain.order_contracts import OrderAction
from app.domain.runtime_entry_admission_contracts import (
    RuntimeEntryAdmissionDisposition,
    RuntimeEntryAdmissionResult,
)
from app.domain.runtime_entry_ingress_contracts import (
    RuntimeEntryIngressError,
    RuntimeEntryIngressV1,
    RuntimeIngressPrincipal,
)
from app.services.authoritative_risk_facts_provider import AuthoritativeRiskFactsProvider
from app.services.durable_entry_repository import DurableEntryRepository
from app.services.entry_admission_gateway import CanonicalEntryAdmissionGateway
from app.services.entry_admission_v2_adapters import (
    AdmissionOutboxAdapter,
    DurableRiskAdmissionAdapter,
)
from app.services.outbox_projection_repository import OutboxProjectionRepository
from app.services.runtime_entry_admission_service import RuntimeEntryAdmissionError, RuntimeEntryAdmissionService
from app.services.runtime_entry_authority_repository import RuntimeEntryAuthorityRepository
from app.services.durable_risk_enforcement_v2_repository import DurableRiskEnforcementRepositoryV2
from app.utils.db import get_db_connection


class RuntimeEntryAdmissionApiError(RuntimeEntryIngressError):
    """Payload or admission failure safe to expose as a typed API error."""


def _object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeEntryAdmissionApiError("request body must be a JSON object")
    return payload


def _enum(enum_type: type, value: Any, field: str, *, required: bool = False):
    if value is None:
        if required:
            raise RuntimeEntryAdmissionApiError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise RuntimeEntryAdmissionApiError(f"{field} must be a typed enum value")
    try:
        return enum_type(value.upper())
    except ValueError as exc:
        raise RuntimeEntryAdmissionApiError(f"{field} is not supported") from exc


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise RuntimeEntryAdmissionApiError(f"{field} must be canonical ASCII text")
    return value


def _occurred_at(payload: dict[str, Any]) -> datetime:
    value = payload.get("occurred_at")
    if not isinstance(value, str):
        raise RuntimeEntryAdmissionApiError("occurred_at is required as an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeEntryAdmissionApiError("occurred_at must be an ISO-8601 UTC string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RuntimeEntryAdmissionApiError("occurred_at must use a zero UTC offset")
    return parsed.astimezone(timezone.utc)


def build_runtime_ingress(payload: Any, *, principal: RuntimeIngressPrincipal) -> tuple[RuntimeEntryIngressV1, EntryMode, str, datetime]:
    """Parse one explicit HTTP body into immutable Runtime Entry facts.

    Source and actor identity are server-owned.  This endpoint intentionally
    permits only human REST/MANUAL sources; restricted Agent/MCP/Grid sources
    remain DISABLED until their own reviewed adapters exist.
    """

    body = _object(payload)
    if not isinstance(principal, RuntimeIngressPrincipal):
        raise RuntimeEntryAdmissionApiError("authenticated principal is invalid")
    source = _enum(EntrySource, body.get("source", EntrySource.REST.value), "source", required=True)
    if source is not EntrySource.REST or principal.source is not EntrySource.REST:
        raise RuntimeEntryAdmissionApiError("this admission endpoint accepts only REST source")
    mode = _enum(EntryMode, body.get("mode", EntryMode.PAPER.value), "mode", required=True)
    if mode is EntryMode.DISABLED:
        # DISABLED is a legitimate explicit safety response, but it must not
        # be used to smuggle an unsupported source through this endpoint.
        mode = EntryMode.DISABLED
    action = _enum(OrderAction, body.get("action"), "action", required=True)
    ingress = RuntimeEntryIngressV1(
        credential_id=body.get("credential_id"),
        instrument_id=_required_text(body, "instrument_id"),
        market_type=_required_text(body, "market_type").lower(),
        action=action,
        side=_enum(OrderSide, body.get("side"), "side"),
        quantity=body.get("quantity"),
        quantity_semantics=_enum(QuantitySemantics, body.get("quantity_semantics"), "quantity_semantics"),
        execution_kind=_enum(ExecutionKind, body.get("execution_kind"), "execution_kind"),
        limit_price=body.get("limit_price"),
        trigger_price=body.get("trigger_price"),
        trigger_direction=_enum(TriggerDirection, body.get("trigger_direction"), "trigger_direction"),
        trigger_price_type=_enum(TriggerPriceType, body.get("trigger_price_type"), "trigger_price_type"),
        reduce_only=body.get("reduce_only", False),
        position_side=_enum(PositionSide, body.get("position_side", PositionSide.NET.value), "position_side", required=True),
        cancel_target_kind=_enum(CancelTargetKind, body.get("cancel_target_kind"), "cancel_target_kind"),
        cancel_target_id=body.get("cancel_target_id"),
        target_position_id=body.get("target_position_id"),
        close_quantity=body.get("close_quantity"),
        close_all=body.get("close_all", False),
        idempotency_key=_required_text(body, "idempotency_key"),
    )
    correlation_id = body.get("correlation_id")
    if correlation_id is None:
        raise RuntimeEntryAdmissionApiError("correlation_id is required")
    if not isinstance(correlation_id, str) or not correlation_id or correlation_id != correlation_id.strip() or not correlation_id.isascii():
        raise RuntimeEntryAdmissionApiError("correlation_id must be canonical ASCII text")
    return ingress, mode, correlation_id, _occurred_at(body)


def _gateway() -> CanonicalEntryAdmissionGateway:
    return CanonicalEntryAdmissionGateway(
        durable_entries=DurableEntryRepository(),
        durable_risk=DurableRiskAdmissionAdapter(
            provider=AuthoritativeRiskFactsProvider(),
            repository=DurableRiskEnforcementRepositoryV2(),
        ),
        outbox=AdmissionOutboxAdapter(repository=OutboxProjectionRepository()),
    )


def admit_runtime_entry_payload(payload: Any, *, tenant_id: int, actor_id: str) -> RuntimeEntryAdmissionResult:
    """Admit one authenticated payload and own only the outer DB boundary."""

    try:
        principal = RuntimeIngressPrincipal(tenant_id=tenant_id, actor_id=actor_id, source=EntrySource.REST)
        _ingress, mode, _correlation_id, _occurred_at = build_runtime_ingress(payload, principal=principal)
        if mode is EntryMode.DISABLED:
            # Preserve the zero-call disabled contract: do not acquire a DB
            # connection merely to discover that the entry is disabled.
            return RuntimeEntryAdmissionResult(RuntimeEntryAdmissionDisposition.DISABLED, None, None)
        with get_db_connection() as connection:
            result, _graph = admit_runtime_entry_payload_caller_owned(
                connection,
                payload,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            if result.disposition is not RuntimeEntryAdmissionDisposition.DISABLED:
                connection.commit()
            return result
    except (RuntimeEntryAdmissionApiError, RuntimeEntryIngressError, RuntimeEntryAdmissionError):
        raise
    except Exception as exc:
        raise RuntimeEntryAdmissionApiError("runtime entry admission is unavailable") from exc


def admit_runtime_entry_payload_caller_owned(
    connection: object,
    payload: Any,
    *,
    tenant_id: int,
    actor_id: str,
) -> tuple[RuntimeEntryAdmissionResult, DurableEntryGraphV2 | None]:
    """Compose admission on a caller-owned connection without transaction control.

    This is the bridge used by the guarded TestNet/Paper execution service.
    It deliberately performs no commit or rollback; a caller can therefore
    keep admission, execution facts, fill persistence, and its final commit
    in one explicit transaction boundary.
    """

    principal = RuntimeIngressPrincipal(tenant_id=tenant_id, actor_id=actor_id, source=EntrySource.REST)
    ingress, mode, correlation_id, occurred_at = build_runtime_ingress(payload, principal=principal)
    if mode is EntryMode.DISABLED:
        # Restricted/disabled mode must not even acquire a database
        # connection; this is the zero-call safety contract.
        return RuntimeEntryAdmissionResult(RuntimeEntryAdmissionDisposition.DISABLED, None, None), None
    service = RuntimeEntryAdmissionService(
        authorities=RuntimeEntryAuthorityRepository(),
        admissions=_gateway(),
    )
    try:
        return service.admit_with_graph(
            connection,
            ingress,
            principal,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            mode=mode,
        )
    except (RuntimeEntryAdmissionApiError, RuntimeEntryIngressError, RuntimeEntryAdmissionError):
        raise
    except Exception as exc:
        raise RuntimeEntryAdmissionApiError("runtime entry admission is unavailable") from exc


def result_to_public_dict(result: RuntimeEntryAdmissionResult) -> dict[str, Any]:
    if not isinstance(result, RuntimeEntryAdmissionResult):
        raise RuntimeEntryAdmissionApiError("runtime entry result is untyped")
    body: dict[str, Any] = {
        "status": result.disposition.value,
        "mode": "DISABLED" if result.admission is None else result.admission.mode.value,
        "live_enabled": False,
        "network_access": False,
        "writes_enabled": result.admission is not None,
    }
    if result.admission is None:
        return body
    admission = result.admission
    body.update({
        "command_id": admission.command_id,
        "action": admission.action.value,
        "economic_order_id": admission.economic_order_id,
        "economic_fingerprint": admission.economic_fingerprint,
        "request_fingerprint": admission.request_fingerprint,
        "risk_decision_id": admission.risk_decision_id,
        "risk_decision_status": admission.risk_decision_status,
        "reservation_id": admission.reservation_id,
        "outbox_event_id": admission.outbox_event_id,
        "outbox_payload_hash": admission.outbox_payload_hash,
    })
    return body


__all__ = [
    "RuntimeEntryAdmissionApiError",
    "build_runtime_ingress",
    "admit_runtime_entry_payload",
    "admit_runtime_entry_payload_caller_owned",
    "result_to_public_dict",
]
