"""Pure, lossless bindings from a named entry surface to Durable Entry V2.

This module intentionally accepts an already normalized
``CanonicalEntryRequestV2``.  Concrete REST, strategy, protection, and agent
parsers remain responsible for supplying only typed facts; this boundary then
proves that the declared source and caller-supplied durable identities cannot
be changed, invented, or silently downgraded before admission.

It does not import a route, runtime, worker, executor, exchange client,
repository, or gateway.  It owns no transaction and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.canonical_entry_contracts import EntrySource
from app.domain.canonical_entry_v2_contracts import (
    CancelTargetSubject,
    CanonicalEntryRequestV2,
    CanonicalEntryV2Error,
    DurableEntryGraphV2,
    EconomicOrderSubject,
)
from app.domain.order_contracts import OrderAction


ENTRYPOINT_V2_BINDING_CONTRACT_VERSION = "entrypoint-v2-binding-v1"


class EntryPointBindingError(CanonicalEntryV2Error):
    """A source binding cannot produce a lossless durable-entry graph."""


@dataclass(frozen=True, slots=True)
class DurableEntryIdentityV2:
    """Caller-supplied immutable locators; this contract never generates IDs."""

    command_id: UUID | str
    economic_order_id: UUID | str | None = None


def bind_entrypoint_v2(
    source: EntrySource,
    request: CanonicalEntryRequestV2,
    identity: DurableEntryIdentityV2,
) -> DurableEntryGraphV2:
    """Bind one normalized source request to its precise durable subject.

    ``CANCEL`` carries its typed target from the canonical economic intent and
    never creates an economic-order subject.  Every other action requires the
    caller to provide its immutable economic-order identity explicitly.
    """

    if not isinstance(source, EntrySource):
        raise EntryPointBindingError("source must use EntrySource")
    if not isinstance(request, CanonicalEntryRequestV2):
        raise EntryPointBindingError("request must use CanonicalEntryRequestV2")
    if not isinstance(identity, DurableEntryIdentityV2):
        raise EntryPointBindingError("identity must use DurableEntryIdentityV2")
    if request.actor.entry_source is not source:
        raise EntryPointBindingError("request source does not match binding source")

    if request.action is OrderAction.CANCEL:
        if identity.economic_order_id is not None:
            raise EntryPointBindingError("CANCEL cannot carry an economic-order identity")
        intent = request.economic_intent
        if intent.cancel_target_kind is None or intent.cancel_target_id is None:
            raise EntryPointBindingError("CANCEL requires a typed cancel target")
        subject = CancelTargetSubject(intent.cancel_target_kind, intent.cancel_target_id)
    else:
        if identity.economic_order_id is None:
            raise EntryPointBindingError("non-CANCEL requires an economic-order identity")
        subject = EconomicOrderSubject(identity.economic_order_id)

    try:
        return DurableEntryGraphV2(identity.command_id, request, subject)
    except CanonicalEntryV2Error as exc:
        raise EntryPointBindingError("durable entry identity is invalid") from exc


def bind_rest_v2(request: CanonicalEntryRequestV2, identity: DurableEntryIdentityV2) -> DurableEntryGraphV2:
    return bind_entrypoint_v2(EntrySource.REST, request, identity)


def bind_manual_v2(request: CanonicalEntryRequestV2, identity: DurableEntryIdentityV2) -> DurableEntryGraphV2:
    return bind_entrypoint_v2(EntrySource.MANUAL, request, identity)


def bind_strategy_v2(request: CanonicalEntryRequestV2, identity: DurableEntryIdentityV2) -> DurableEntryGraphV2:
    return bind_entrypoint_v2(EntrySource.STRATEGY, request, identity)


def bind_protection_v2(request: CanonicalEntryRequestV2, identity: DurableEntryIdentityV2) -> DurableEntryGraphV2:
    return bind_entrypoint_v2(EntrySource.PROTECTION, request, identity)


def bind_agent_v2(request: CanonicalEntryRequestV2, identity: DurableEntryIdentityV2) -> DurableEntryGraphV2:
    return bind_entrypoint_v2(EntrySource.AGENT, request, identity)


def bind_mcp_v2(request: CanonicalEntryRequestV2, identity: DurableEntryIdentityV2) -> DurableEntryGraphV2:
    return bind_entrypoint_v2(EntrySource.MCP, request, identity)


def bind_grid_v2(request: CanonicalEntryRequestV2, identity: DurableEntryIdentityV2) -> DurableEntryGraphV2:
    return bind_entrypoint_v2(EntrySource.GRID, request, identity)


__all__ = [
    "DurableEntryIdentityV2",
    "ENTRYPOINT_V2_BINDING_CONTRACT_VERSION",
    "EntryPointBindingError",
    "bind_agent_v2",
    "bind_entrypoint_v2",
    "bind_grid_v2",
    "bind_manual_v2",
    "bind_mcp_v2",
    "bind_protection_v2",
    "bind_rest_v2",
    "bind_strategy_v2",
]
