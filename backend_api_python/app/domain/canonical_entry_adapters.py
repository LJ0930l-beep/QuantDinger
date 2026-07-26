"""Pure adapters from named entry surfaces into canonical entry requests.

Adapters only bind a source and actor contract.  They do not persist, dispatch,
authorize, submit, or otherwise reach a trading runtime.
"""

from __future__ import annotations

from typing import Any

from app.domain.canonical_entry_contracts import (
    CanonicalCommandDraft,
    EntryActorContext,
    EntryContractError,
    EntryMode,
    EntrySource,
    normalize_entry,
)
from app.domain.order_contracts import Actor


class EntryAdapterError(EntryContractError):
    """Raised when a source adapter receives facts outside its fixed boundary."""


_SOURCE_ACTORS = {
    EntrySource.REST: Actor.HUMAN,
    EntrySource.MANUAL: Actor.HUMAN,
    EntrySource.STRATEGY: Actor.STRATEGY,
    EntrySource.AGENT: Actor.AGENT,
    EntrySource.MCP: Actor.MCP,
    EntrySource.GRID: Actor.GRID,
    EntrySource.PROTECTION: Actor.PROTECTION,
}


def _adapt(source: EntrySource, actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    supplied_actor = facts.pop("actor", None)
    supplied_source = facts.pop("entry_source", None)
    if supplied_actor is not None or supplied_source is not None:
        raise EntryAdapterError("source adapters own actor and entry_source facts")
    return normalize_entry(
        actor=EntryActorContext(_SOURCE_ACTORS[source], actor_id, source),
        **facts,
    )


def adapt_rest(actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    return _adapt(EntrySource.REST, actor_id, **facts)


def adapt_manual(actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    return _adapt(EntrySource.MANUAL, actor_id, **facts)


def adapt_strategy(actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    return _adapt(EntrySource.STRATEGY, actor_id, **facts)


def adapt_agent(actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    return _adapt(EntrySource.AGENT, actor_id, **facts)


def adapt_mcp(actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    return _adapt(EntrySource.MCP, actor_id, **facts)


def adapt_grid(actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    return _adapt(EntrySource.GRID, actor_id, **facts)


def adapt_protection(actor_id: str, **facts: Any) -> CanonicalCommandDraft:
    return _adapt(EntrySource.PROTECTION, actor_id, **facts)


__all__ = [
    "EntryAdapterError",
    "adapt_agent",
    "adapt_grid",
    "adapt_manual",
    "adapt_mcp",
    "adapt_protection",
    "adapt_rest",
    "adapt_strategy",
]
