"""Orchestrate Gate snapshot -> Runtime Entry authority facts projection.

The service composes the read-only Gate provider and the caller-owned facts
repository on one caller-provided connection.  It never commits, never opens a
venue client itself, never fabricates facts, and exposes typed failures so an
HTTP boundary can return a fail-closed response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.domain.runtime_entry_authority_projection_contracts import (
    PROJECTION_CONTRACT_VERSION,
    build_instrument_authority_facts,
    build_instrument_rule_snapshot_facts,
    build_position_projection_facts,
    build_scope_binding_facts,
    instrument_authority_id,
    position_projection_id,
    position_subject_id,
    position_side,
)
from app.domain.gate_read_snapshot_contracts import GateReadSnapshot
from app.services.runtime_entry_authority_facts_repository import (
    RuntimeEntryAuthorityFactsDisposition,
    RuntimeEntryAuthorityFactsRepository,
    RuntimeEntryAuthorityFactsResult,
)


class RuntimeEntryAuthorityProjectionError(RuntimeError):
    """Typed orchestration failure; never leaks credentials or raw payloads."""


@dataclass(frozen=True)
class RuntimeEntryAuthorityProjectionResult:
    scope: RuntimeEntryAuthorityFactsResult
    rules: tuple[RuntimeEntryAuthorityFactsResult, ...] = ()
    authority: RuntimeEntryAuthorityFactsResult | None = None
    positions: tuple[RuntimeEntryAuthorityFactsResult, ...] = ()
    snapshot_fingerprint: str = ""
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dispositions: dict[str, Any] = field(default_factory=dict)


class RuntimeEntryAuthorityProjectionService:
    """Project one authenticated Gate snapshot into persisted authority facts."""

    def __init__(
        self,
        repository: RuntimeEntryAuthorityFactsRepository | None = None,
        snapshot_provider: Callable[..., GateReadSnapshot] | None = None,
    ) -> None:
        self._repository = repository or RuntimeEntryAuthorityFactsRepository()
        self._snapshot_provider = snapshot_provider
        self._last_snapshot: GateReadSnapshot | None = None

    def _provider(self) -> Callable[..., GateReadSnapshot]:
        if self._snapshot_provider is not None:
            return self._snapshot_provider
        from app.services.gate_private_read_provider import GatePrivateReadProviderError, provider_from_database

        try:
            return provider_from_database()
        except Exception as exc:
            raise RuntimeEntryAuthorityProjectionError("gate read provider is unavailable") from exc

    def project_authority_facts(
        self,
        connection: object,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        market_type: str,
        instrument_id: str = "",
        as_of: datetime | None = None,
    ) -> RuntimeEntryAuthorityProjectionResult:
        """Project scope binding + instrument rules + instrument authority."""

        observed = as_of or datetime.now(timezone.utc)
        if not isinstance(observed, datetime) or observed.tzinfo is None:
            raise RuntimeEntryAuthorityProjectionError("as_of must be a UTC datetime")
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
            raise RuntimeEntryAuthorityProjectionError(f"gate snapshot read failed ({type(exc).__name__})") from exc
        if not isinstance(snapshot, GateReadSnapshot):
            raise RuntimeEntryAuthorityProjectionError("gate snapshot provider returned an untyped value")
        self._last_snapshot = snapshot

        scope_facts = build_scope_binding_facts(snapshot, tenant_id=int(user_id), credential_id=int(credential_id))
        rule_facts = build_instrument_rule_snapshot_facts(snapshot)
        authority_facts = build_instrument_authority_facts(
            snapshot, rule_facts, tenant_id=int(user_id), credential_id=int(credential_id), account_scope=account_scope,
        )

        scope = self._repository.persist_scope_binding(connection, scope_facts)
        rules = tuple(self._repository.persist_instrument_rule_snapshot(connection, row) for row in rule_facts)
        authorities = tuple(self._repository.persist_instrument_authority(connection, row) for row in authority_facts)
        authority = authorities[0] if len(authorities) == 1 else None

        return RuntimeEntryAuthorityProjectionResult(
            scope=scope,
            rules=rules,
            authority=authority,
            snapshot_fingerprint=snapshot.snapshot_fingerprint,
            observed_at=snapshot.observed_at,
            dispositions={
                "scope": scope.disposition.value,
                "rules": [r.disposition.value for r in rules],
                "authorities": [r.disposition.value for r in authorities],
            },
        )

    def project_position_projection(
        self,
        connection: object,
        snapshot: GateReadSnapshot,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
    ) -> tuple[RuntimeEntryAuthorityFactsResult, ...]:
        """Persist position projections for open (quantity>0) positions."""

        rows = build_position_projection_facts(
            snapshot, tenant_id=int(user_id), credential_id=int(credential_id), account_scope=account_scope,
        )
        return tuple(self._repository.persist_position_projection(connection, row) for row in rows)

    def project_position_subjects(
        self,
        connection: object,
        snapshot: GateReadSnapshot,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        market_type: str,
        checkpoint_id: str,
    ) -> tuple[RuntimeEntryAuthorityFactsResult, ...]:
        """Persist position subjects for open positions under a HEALTHY checkpoint.

        The DB trigger re-validates checkpoint HEALTHY and projection
        quantity>0; a non-HEALTHY checkpoint therefore makes this fail closed.
        """

        results: list[RuntimeEntryAuthorityFactsResult] = []
        # Reuse the same scope/authority facts persisted by the authority step
        # so every FK and trigger predicate lines up.
        scope_facts = build_scope_binding_facts(snapshot, tenant_id=int(user_id), credential_id=int(credential_id))
        rule_facts = build_instrument_rule_snapshot_facts(snapshot)
        authority_facts = build_instrument_authority_facts(
            snapshot, rule_facts, tenant_id=int(user_id), credential_id=int(credential_id), account_scope=account_scope,
        )
        projection_facts = build_position_projection_facts(
            snapshot, tenant_id=int(user_id), credential_id=int(credential_id), account_scope=account_scope,
        )
        by_instrument = {row["instrument_id"]: row for row in projection_facts}
        for position in snapshot.positions:
            if position.quantity <= 0:
                continue
            instrument = str(position.instrument_id).upper()
            side = position_side(position.side)
            market = str(market_type).lower()
            scope = self._repository.persist_scope_binding(connection, scope_facts)
            authority_row = next((row for row in authority_facts if row["instrument_id"] == instrument and row["market_type"] == market), None)
            if authority_row is None:
                raise RuntimeEntryAuthorityProjectionError(f"instrument authority missing for {instrument}")
            authority = self._repository.persist_instrument_authority(connection, authority_row)
            projection_row = by_instrument.get(instrument)
            if projection_row is None or projection_row["side"] != side:
                raise RuntimeEntryAuthorityProjectionError(f"position projection missing for {instrument} {side}")
            projection = self._repository.persist_position_projection(connection, projection_row)
            subject = self._repository.persist_position_subject(
                connection,
                {
                    "id": position_subject_id(authority.id, side, projection.id, checkpoint_id),
                    "contract_version": PROJECTION_CONTRACT_VERSION,
                    "scope_binding_id": scope.id,
                    "instrument_authority_id": authority.id,
                    "reconciliation_checkpoint_id": checkpoint_id,
                    "position_projection_id": projection.id,
                    "tenant_id": int(user_id),
                    "credential_id": int(credential_id),
                    "account_scope": str(account_scope),
                    "exchange_id": "gate",
                    "instrument_id": instrument,
                    "market_type": market,
                    "position_side": side,
                    "source_fingerprint": snapshot.snapshot_fingerprint,
                    "observed_at": snapshot.observed_at,
                },
            )
            results.append(subject)
        return tuple(results)

    def run_pipeline(
        self,
        connection: object,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        market_type: str,
        instrument_id: str = "",
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Project authority + reconcile + position subjects in one transaction.

        Returns dispositions only; the caller owns commit/rollback.
        """

        from app.services.runtime_entry_reconciliation_service import (
            RuntimeEntryReconciliationService,
            persist_reconciliation_result,
        )

        projection = self.project_authority_facts(
            connection, user_id=user_id, credential_id=credential_id,
            account_scope=account_scope, market_type=market_type, instrument_id=instrument_id, as_of=as_of,
        )
        observed = projection.observed_at
        reconciliation = RuntimeEntryReconciliationService(snapshot_provider=self._snapshot_provider).run_reconciliation(
            connection, user_id=user_id, credential_id=credential_id,
            account_scope=account_scope, market_type=market_type, instrument_id=instrument_id, as_of=observed,
        )
        persist_reconciliation_result(connection, reconciliation, completed_at=observed)
        from uuid import NAMESPACE_URL, uuid5

        checkpoint_id = str(uuid5(
            NAMESPACE_URL,
            f"reconciliation-v1|{reconciliation.run.run_id}|{reconciliation.replay_fingerprint}",
        ))
        # The authoritative risk provider requires a fresh checkpoint with a
        # max-age bound; the repository leaves it NULL, so stamp it here.
        cursor = connection.cursor()
        try:
            cursor.execute(
                "UPDATE qd_reconciliation_checkpoints "
                "SET risk_max_age_seconds = %s, evidence_hash = %s, version = version + 1, "
                "reconciliation_checkpoint_version = reconciliation_checkpoint_version + 1 "
                "WHERE id = %s",
                (60, reconciliation.replay_fingerprint, checkpoint_id),
            )
        finally:
            cursor.close()
        last_snapshot = self._last_snapshot
        subjects = self.project_position_subjects(
            connection, last_snapshot,
            user_id=user_id, credential_id=credential_id, account_scope=account_scope,
            market_type=market_type, checkpoint_id=checkpoint_id,
        ) if last_snapshot is not None else ()
        return {
            "status": "PIPELINED",
            "authority": projection.dispositions,
            "checkpoint": {
                "run_id": checkpoint_id,
                "status": reconciliation.checkpoint.status.value,
                "discrepancy_count": reconciliation.checkpoint.discrepancy_count,
            },
            "position_subjects": [r.disposition.value for r in subjects],
            "live_enabled": False,
        }


__all__ = [
    "PROJECTION_CONTRACT_VERSION",
    "RuntimeEntryAuthorityProjectionService",
    "RuntimeEntryAuthorityProjectionResult",
    "RuntimeEntryAuthorityProjectionError",
]
