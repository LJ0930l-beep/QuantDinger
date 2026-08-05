"""Caller-owned bridge from Gate TestNet receipts to the fill-ledger repository.

This service intentionally does not call the venue client.  A caller that has
already obtained a typed TestNet receipt can compose its fill/evidence facts in
the same transaction as surrounding admission/order facts.  Commit and
rollback remain exclusively owned by that caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.gate_testnet_execution_contracts import GateTestnetExecutionReceipt
from app.domain.gate_testnet_ledger_contracts import GateTestnetLedgerScope, build_gate_testnet_ledger_inputs
from app.domain.immutable_fill_ledger import FillLedgerInput
from app.services.immutable_fill_ledger_repository import (
    FillLedgerCommitResult,
    FillLedgerPersistenceScope,
    ImmutableFillLedgerRepository,
)


class GateTestnetLedgerPersistenceError(RuntimeError):
    """Typed failure at the TestNet-to-ledger composition boundary."""


class _FillLedgerPort(Protocol):
    def persist_fill_bundle_caller_owned(self, connection: object, *, scope: FillLedgerPersistenceScope, fill: FillLedgerInput) -> FillLedgerCommitResult: ...


@dataclass(frozen=True, slots=True)
class GateTestnetLedgerPersistenceResult:
    receipt_fingerprint: str
    fills: tuple[FillLedgerCommitResult, ...]
    live_enabled: bool = False

    @property
    def disposition(self) -> str:
        if not self.fills:
            return "NO_FILL"
        if all(item.disposition.value == "REPLAYED" for item in self.fills):
            return "REPLAYED"
        return "APPLIED"


def persist_gate_testnet_receipt_caller_owned(
    connection: object,
    receipt: GateTestnetExecutionReceipt,
    *,
    ledger_scope: GateTestnetLedgerScope,
    persistence_scope: FillLedgerPersistenceScope,
    repository: _FillLedgerPort | None = None,
) -> GateTestnetLedgerPersistenceResult:
    """Persist every stable fill in one caller-owned transaction."""

    if not isinstance(receipt, GateTestnetExecutionReceipt):
        raise GateTestnetLedgerPersistenceError("typed TestNet receipt is required")
    if not isinstance(ledger_scope, GateTestnetLedgerScope) or not isinstance(persistence_scope, FillLedgerPersistenceScope):
        raise GateTestnetLedgerPersistenceError("typed ledger scopes are required")
    if persistence_scope.economic_order_id != ledger_scope.economic_order_id:
        raise GateTestnetLedgerPersistenceError("ledger and persistence economic order scope mismatch")
    repo = repository or ImmutableFillLedgerRepository()
    try:
        inputs = build_gate_testnet_ledger_inputs(receipt, scope=ledger_scope)
        if inputs and any(item.account_scope != receipt.order.account_scope for item in inputs):
            raise GateTestnetLedgerPersistenceError("persistence and receipt account scope mismatch")
        results = tuple(
            repo.persist_fill_bundle_caller_owned(connection, scope=persistence_scope, fill=fill)
            for fill in inputs
        )
        return GateTestnetLedgerPersistenceResult(receipt.lifecycle_fingerprint, results)
    except GateTestnetLedgerPersistenceError:
        raise
    except Exception as exc:
        raise GateTestnetLedgerPersistenceError("TestNet ledger persistence failed") from exc


__all__ = [
    "GateTestnetLedgerPersistenceError",
    "GateTestnetLedgerPersistenceResult",
    "persist_gate_testnet_receipt_caller_owned",
]
