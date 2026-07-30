"""Typed persistence boundary for Runtime Entry authority facts.

This contract records which immutable server-side scope, instrument, and
position facts authorized a Canonical Entry V2 graph.  It has no route,
runtime, exchange, or transaction ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.domain.runtime_entry_resolution_contracts import ResolvedRuntimeEntryFacts


RUNTIME_ENTRY_AUTHORITY_CONTRACT_VERSION = "runtime-entry-authority-v1"
RUNTIME_ENTRY_INGRESS_TABLE = "qd_runtime_entry_ingresses"


class RuntimeEntryAuthorityRepositoryError(RuntimeError):
    """Typed failure at the persisted Runtime Entry authority boundary."""


class RuntimeEntryAuthorityConflict(RuntimeEntryAuthorityRepositoryError):
    """An ingress identity names immutable facts that do not match."""


class RuntimeEntryAuthorityUnavailable(RuntimeEntryAuthorityRepositoryError):
    """Required persisted authority facts do not exist or are not healthy."""


def _uuid(value: UUID | str, field_name: str) -> str:
    try:
        return str(UUID(str(value))).lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeEntryAuthorityRepositoryError(f"{field_name} must be a UUID") from exc


@dataclass(frozen=True, slots=True)
class RuntimeEntryAuthorityReferences:
    """Immutable database locators selected by the authority repository."""

    scope_binding_id: UUID | str
    instrument_authority_id: UUID | str
    position_subject_id: UUID | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_binding_id", _uuid(self.scope_binding_id, "scope_binding_id"))
        object.__setattr__(self, "instrument_authority_id", _uuid(self.instrument_authority_id, "instrument_authority_id"))
        if self.position_subject_id is not None:
            object.__setattr__(self, "position_subject_id", _uuid(self.position_subject_id, "position_subject_id"))


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeEntryAuthority:
    """Resolved typed facts together with their immutable source records."""

    facts: ResolvedRuntimeEntryFacts
    references: RuntimeEntryAuthorityReferences

    def __post_init__(self) -> None:
        if not isinstance(self.facts, ResolvedRuntimeEntryFacts):
            raise RuntimeEntryAuthorityRepositoryError("facts must use ResolvedRuntimeEntryFacts")
        if not isinstance(self.references, RuntimeEntryAuthorityReferences):
            raise RuntimeEntryAuthorityRepositoryError("references must use RuntimeEntryAuthorityReferences")
        if (self.facts.position is None) != (self.references.position_subject_id is None):
            raise RuntimeEntryAuthorityRepositoryError("position authority facts and references must agree")


class RuntimeEntryIngressPersistDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class RuntimeEntryIngressPersistResult:
    command_id: str
    disposition: RuntimeEntryIngressPersistDisposition
    authority: ResolvedRuntimeEntryAuthority
