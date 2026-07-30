"""Caller-owned composition result for Runtime Entry V1 admission.

The result distinguishes a disabled ingress from a persisted Canonical Entry
admission.  It does not create a transaction or make a trading decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.entry_admission_v2_contracts import EntryAdmissionDisposition, EntryAdmissionResultV2
from app.domain.runtime_entry_authority_persistence_contracts import RuntimeEntryIngressPersistResult


class RuntimeEntryAdmissionDisposition(str, Enum):
    DISABLED = "DISABLED"
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"
    RISK_REJECTED = "RISK_REJECTED"


@dataclass(frozen=True, slots=True)
class RuntimeEntryAdmissionResult:
    disposition: RuntimeEntryAdmissionDisposition
    admission: EntryAdmissionResultV2 | None
    ingress: RuntimeEntryIngressPersistResult | None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, RuntimeEntryAdmissionDisposition):
            raise ValueError("runtime entry admission disposition must be typed")
        if self.disposition is RuntimeEntryAdmissionDisposition.DISABLED:
            if self.admission is not None or self.ingress is not None:
                raise ValueError("disabled runtime entry cannot persist facts")
            return
        if not isinstance(self.admission, EntryAdmissionResultV2) or not isinstance(self.ingress, RuntimeEntryIngressPersistResult):
            raise ValueError("persisted runtime entry requires typed admission and ingress receipts")
        if self.disposition is RuntimeEntryAdmissionDisposition.RISK_REJECTED:
            if self.admission.disposition is not EntryAdmissionDisposition.RISK_REJECTED:
                raise ValueError("risk rejection must preserve admission disposition")
