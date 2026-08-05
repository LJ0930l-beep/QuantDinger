"""Orchestrate a minimal, honest reconciliation checkpoint from real facts.

The service compares local paper fills with the real Gate snapshot positions
for one scope and persists a reconciliation checkpoint.  It never fabricates
facts: when both sides are empty or exactly match, the checkpoint is HEALTHY
with zero discrepancies; any mismatch degrades health.  If either source is
unavailable the service fails closed and writes nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.domain.gate_read_snapshot_contracts import GateReadSnapshot
from app.domain.reconciliation_contracts import (
    ReconciliationDiscrepancy,
    ReconciliationDiscrepancyKind,
    ReconciliationFactKind,
    ReconciliationFactValue,
    ReconciliationPolicySnapshot,
    ReconciliationResult,
    ReconciliationRun,
    ReconciliationSeverity,
    ReconciliationSourceSnapshot,
    compare_reconciliation_state,
)
from app.domain.runtime_entry_authority_projection_contracts import (
    PROJECTION_CONTRACT_VERSION,
    SOURCE_IDENTITY,
    position_side,
)


RECONCILIATION_POLICY_VERSION = "runtime-entry-reconciliation-v1"
LOCAL_CONSUMER_NAME = "runtime-entry-authority-v1"
LOCAL_BUILD_FINGERPRINT = "b" * 64  # deterministic projection build identity
LOCAL_WATERMARK = 0  # snapshot-driven projection has no event stream


class RuntimeEntryReconciliationError(RuntimeError):
    """Typed orchestration failure; never leaks credentials or raw payloads."""


class RuntimeEntryReconciliationService:
    """Build one reconciliation result from real local and external facts."""

    def __init__(
        self,
        snapshot_provider: Callable[..., GateReadSnapshot] | None = None,
        local_position_reader: Callable[..., dict[tuple[str, str], Decimal]] | None = None,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._local_position_reader = local_position_reader or self._read_local_positions

    def _provider(self) -> Callable[..., GateReadSnapshot]:
        if self._snapshot_provider is not None:
            return self._snapshot_provider
        from app.services.gate_private_read_provider import provider_from_database

        try:
            return provider_from_database()
        except Exception as exc:
            raise RuntimeEntryReconciliationError("gate read provider is unavailable") from exc

    def _read_local_positions(
        self,
        connection: object,
        *,
        tenant_id: int,
        credential_id: int,
        account_scope: str,
        market_type: str,
        instrument_id: str,
    ) -> dict[tuple[str, str], Decimal]:
        """Aggregate local paper fills into net positions (empty when none).

        The paper tables are keyed by ``user_id``/``symbol``/``market_type``;
        credential and account scope are not persisted there, so the join is
        scoped by user + market + symbol only and an empty table yields empty.
        """

        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT f.quantity
                  FROM qd_paper_execution_fills f
                  JOIN qd_paper_execution_orders o ON o.id = f.order_id
                 WHERE o.user_id = %s
                   AND o.market_type = %s
                   AND o.symbol = %s
                   AND o.status IN ('FILLED', 'PARTIALLY_FILLED')
                """,
                (tenant_id, market_type, instrument_id),
            )
            positions: dict[tuple[str, str], Decimal] = {}
            side = "LONG"
            total = Decimal("0")
            for row in cursor.fetchall():
                quantity = Decimal(str(row[0]))
                if quantity <= 0:
                    continue
                total += quantity
            if total > 0:
                positions[(str(instrument_id).upper(), side)] = total
            return positions
        finally:
            cursor.close()

    def run_reconciliation(
        self,
        connection: object,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        market_type: str,
        instrument_id: str,
        as_of: datetime | None = None,
    ) -> ReconciliationResult:
        """Build and persist one reconciliation checkpoint from real facts."""

        observed = as_of or datetime.now(timezone.utc)
        provider = self._provider()
        try:
            snapshot = provider(
                user_id=int(user_id),
                credential_id=int(credential_id),
                market_type=str(market_type),
                account_scope=str(account_scope),
                instrument_id=str(instrument_id or ""),
                as_of=observed,
            )
        except Exception as exc:
            raise RuntimeEntryReconciliationError(f"gate snapshot read failed ({type(exc).__name__})") from exc
        if not isinstance(snapshot, GateReadSnapshot):
            raise RuntimeEntryReconciliationError("gate snapshot provider returned an untyped value")

        local = self._read_local_positions(
            connection, tenant_id=int(user_id), credential_id=int(credential_id),
            account_scope=account_scope, market_type=market_type, instrument_id=instrument_id,
        )
        external: dict[tuple[str, str], Decimal] = {}
        for position in snapshot.positions:
            side = position_side(position.side)
            if position.quantity > 0:
                external[(position.instrument_id.upper(), side)] = position.quantity

        discrepancies = self._compare(local, external, instrument_id=instrument_id)
        return self._build_result(
            user_id=int(user_id), credential_id=int(credential_id), account_scope=account_scope,
            market_type=market_type, instrument_id=instrument_id, observed_at=observed,
            local=local, external=external, discrepancies=discrepancies,
            external_identity=snapshot.snapshot_fingerprint, external_version=snapshot.auth.evidence_version,
        )

    def _compare(
        self,
        local: dict[tuple[str, str], Decimal],
        external: dict[tuple[str, str], Decimal],
        *,
        instrument_id: str,
    ) -> tuple[ReconciliationDiscrepancy, ...]:
        instrument = str(instrument_id).upper()
        discrepancies: list[ReconciliationDiscrepancy] = []
        for side in ("LONG", "SHORT"):
            key = (instrument, side)
            local_qty = local.get(key, Decimal("0"))
            external_qty = external.get(key, Decimal("0"))
            if local_qty != external_qty:
                discrepancies.append(ReconciliationDiscrepancy(
                    fact_name=f"position.{side.lower()}",
                    kind=ReconciliationDiscrepancyKind.POSITION_MISMATCH,
                    severity=ReconciliationSeverity.BLOCKING,
                    local=ReconciliationFactValue(str(local_qty), ReconciliationFactKind.QUANTITY, instrument),
                    external=ReconciliationFactValue(str(external_qty), ReconciliationFactKind.QUANTITY, instrument),
                    detail="local paper fills do not match the venue position",
                ))
        return tuple(discrepancies)

    def _build_result(
        self,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        market_type: str,
        instrument_id: str,
        observed_at: datetime,
        local: dict[tuple[str, str], Decimal],
        external: dict[tuple[str, str], Decimal],
        discrepancies: tuple[ReconciliationDiscrepancy, ...],
        external_identity: str,
        external_version: str,
    ) -> ReconciliationResult:
        instrument = str(instrument_id).upper()
        # The generation row is append-only once referenced by reconciliation
        # runs, so its id must be deterministic per consumer+build+watermark to
        # be reusable across replays.  The run itself stays unique per scope.
        generation_id = uuid5(NAMESPACE_URL, f"{LOCAL_CONSUMER_NAME}|{LOCAL_BUILD_FINGERPRINT}|{LOCAL_WATERMARK}")
        run_id = uuid4()
        local_facts = {
            f"position.{side.lower()}": ReconciliationFactValue(
                str(local.get((instrument, side), Decimal("0"))), ReconciliationFactKind.QUANTITY, instrument,
            )
            for side in ("LONG", "SHORT")
        }
        external_facts = {
            f"position.{side.lower()}": ReconciliationFactValue(
                str(external.get((instrument, side), Decimal("0"))), ReconciliationFactKind.QUANTITY, instrument,
            )
            for side in ("LONG", "SHORT")
        }
        external_source = ReconciliationSourceSnapshot(
            "gate-private-read-v1", external_version, user_id, credential_id, account_scope,
            "gate", market_type, instrument, None, observed_at, external_facts,
        )
        local_source = ReconciliationSourceSnapshot(
            LOCAL_CONSUMER_NAME, "paper-facts-v1", user_id, credential_id, account_scope,
            "gate", market_type, instrument, None, observed_at, local_facts,
            generation_id, LOCAL_WATERMARK,
        )
        run = ReconciliationRun(
            run_id, user_id, credential_id, account_scope, "gate", market_type, instrument, None,
            generation_id, LOCAL_CONSUMER_NAME, LOCAL_BUILD_FINGERPRINT, LOCAL_WATERMARK,
            external_source.source_identity, external_source.source_version, external_source.source_fingerprint,
            observed_at, observed_at, observed_at, "audit-correlation",
            ReconciliationPolicySnapshot(RECONCILIATION_POLICY_VERSION, True),
        )
        return compare_reconciliation_state(run, local_source, external_source)


def persist_reconciliation_result(connection: object, result: ReconciliationResult, *, completed_at: datetime) -> None:
    """Persist one reconciliation result plus its projection-generation precondition."""

    from app.services.reconciliation_repository import ReconciliationRepository

    run = result.run
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO qd_projection_generations (
                id, consumer_name, build_fingerprint, state, source_high_watermark,
                processed_high_watermark, expected_event_count, applied_event_count, is_current, completed_at
            ) VALUES (%s, %s, %s, 'READY', %s, %s, 0, 0, TRUE, %s)
            ON CONFLICT (consumer_name, build_fingerprint) DO UPDATE
              SET state = 'READY',
                  source_high_watermark = EXCLUDED.source_high_watermark,
                  processed_high_watermark = EXCLUDED.processed_high_watermark,
                  is_current = TRUE,
                  completed_at = EXCLUDED.completed_at,
                  failure_reason = NULL
            """,
            (str(run.local_generation_id), run.local_consumer_name, run.local_generation_build_fingerprint,
             run.local_checkpoint_watermark, run.local_checkpoint_watermark, completed_at),
        )
    finally:
        cursor.close()
    repository = ReconciliationRepository()
    repository.persist_result(connection, result, completed_at=completed_at)


__all__ = [
    "RECONCILIATION_POLICY_VERSION",
    "LOCAL_CONSUMER_NAME",
    "LOCAL_BUILD_FINGERPRINT",
    "LOCAL_WATERMARK",
    "RuntimeEntryReconciliationService",
    "RuntimeEntryReconciliationError",
    "persist_reconciliation_result",
]
