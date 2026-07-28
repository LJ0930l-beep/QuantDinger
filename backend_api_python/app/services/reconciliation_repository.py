"""Caller-owned transactional persistence for immutable reconciliation facts.

The repository performs no commit or rollback and never contacts a venue.  It
persists only already-normalized domain facts and converts all driver failures
to typed repository failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.reconciliation_contracts import (
    ReconciliationContractError,
    ReconciliationResult,
    ReconciliationRunState,
)


class ReconciliationRepositoryError(RuntimeError):
    """Typed persistence failure; database-driver exceptions never escape."""


class ReconciliationReplayConflict(ReconciliationRepositoryError):
    """A durable reconciliation identity names different immutable facts."""


class ReconciliationRunConflict(ReconciliationRepositoryError):
    """A reconciliation run cannot progress from its durable state."""


class ReconciliationPersistDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class ReconciliationPersistResult:
    run_id: str
    disposition: ReconciliationPersistDisposition
    replay_fingerprint: str
    checkpoint_status: str


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReconciliationRepositoryError("completed_at must use a zero UTC offset")
    return value.astimezone(timezone.utc)


def _value(row: object, index: int) -> object:
    try:
        return row[index]  # type: ignore[index]
    except (TypeError, IndexError) as exc:
        raise ReconciliationRepositoryError("database returned an incomplete reconciliation row") from exc


def _decimal_equal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return False


def _uuid_equal(left: object, right: object) -> bool:
    try:
        return str(UUID(str(left))).lower() == str(UUID(str(right))).lower()
    except (TypeError, ValueError, AttributeError):
        return False


class ReconciliationRepository:
    """Append-only reconciliation run, discrepancy and checkpoint persistence."""

    def persist_result(
        self,
        connection: Any,
        result: ReconciliationResult,
        *,
        completed_at: datetime,
    ) -> ReconciliationPersistResult:
        if not isinstance(result, ReconciliationResult):
            raise ReconciliationRepositoryError("result must use ReconciliationResult")
        if result.run.state is not ReconciliationRunState.BUILDING:
            raise ReconciliationRepositoryError("only a BUILDING reconciliation run may be persisted")
        completed_at = _utc(completed_at)
        try:
            with connection.cursor() as cursor:
                self._lock_generation(cursor, result)
                cursor.execute(
                    """
                    INSERT INTO qd_reconciliation_runs (
                        id, tenant_id, credential_id, account_scope, venue, market_type, instrument_id, asset_scope,
                        reconciliation_contract_version, local_generation_id, local_consumer_name,
                        local_generation_build_fingerprint, local_checkpoint_watermark,
                        external_observation_identity, external_observation_version,
                        external_observation_fingerprint, local_observed_at, external_observed_at, as_of,
                        correlation_id, policy_version, warning_degrades_health, quantity_absolute,
                        monetary_absolute, max_observation_age_seconds, policy_fingerprint, build_fingerprint,
                        replay_fingerprint, discrepancy_count, state, completed_at, failure_reason
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'reconciliation-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,0,'BUILDING',NULL,NULL)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    self._run_insert_values(result),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    row = self._lock_run(cursor, result.run.run_id)
                    self._assert_run_identity(row, result)
                    if _value(row, 27) != ReconciliationRunState.COMPLETE.value:
                        raise ReconciliationRunConflict("reconciliation run is not replayable")
                    if _value(row, 26) != result.replay_fingerprint:
                        raise ReconciliationReplayConflict("completed run names different replay facts")
                    self._assert_discrepancy_replay(cursor, result)
                    return ReconciliationPersistResult(
                        result.run.run_id, ReconciliationPersistDisposition.REPLAYED,
                        result.replay_fingerprint, str(_value(row, 28)),
                    )
                self._insert_discrepancies(cursor, result)
                cursor.execute(
                    """
                    UPDATE qd_reconciliation_runs
                       SET state = 'COMPLETE', replay_fingerprint = %s, discrepancy_count = %s, completed_at = %s
                     WHERE id = %s AND state = 'BUILDING'
                    RETURNING id
                    """,
                    (result.replay_fingerprint, len(result.discrepancies), completed_at, result.run.run_id),
                )
                if cursor.fetchone() is None:
                    raise ReconciliationRunConflict("reconciliation completion was not applied")
                checkpoint_id = str(uuid5(NAMESPACE_URL, f"reconciliation-v1|{result.run.run_id}|{result.replay_fingerprint}"))
                cursor.execute(
                    """
                    INSERT INTO qd_reconciliation_checkpoints (
                        id, tenant_id, credential_id, exchange, market_type, account_scope, instrument_id, status,
                        reconciliation_run_id, reconciliation_checkpoint_version, result_fingerprint,
                        reconciliation_discrepancy_count, policy_fingerprint, version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
                    ON CONFLICT (credential_id, exchange, market_type, account_scope, instrument_id)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        reconciliation_run_id = EXCLUDED.reconciliation_run_id,
                        reconciliation_checkpoint_version = CASE
                            WHEN qd_reconciliation_checkpoints.reconciliation_run_id IS NULL THEN 0
                            ELSE qd_reconciliation_checkpoints.reconciliation_checkpoint_version + 1
                        END,
                        result_fingerprint = EXCLUDED.result_fingerprint,
                        reconciliation_discrepancy_count = EXCLUDED.reconciliation_discrepancy_count,
                        policy_fingerprint = EXCLUDED.policy_fingerprint,
                        version = qd_reconciliation_checkpoints.version + 1,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (checkpoint_id, result.run.tenant_id, result.run.credential_id, result.run.venue,
                     result.run.market_type, result.run.account_scope, result.run.instrument_id or "",
                     result.checkpoint.status.value, result.run.run_id, result.checkpoint.checkpoint_version,
                     result.replay_fingerprint, len(result.discrepancies), result.run.policy.policy_fingerprint),
                )
                if cursor.fetchone() is None:
                    raise ReconciliationRunConflict("reconciliation checkpoint was not applied")
                return ReconciliationPersistResult(
                    result.run.run_id, ReconciliationPersistDisposition.CREATED,
                    result.replay_fingerprint, result.checkpoint.status.value,
                )
        except (ReconciliationRepositoryError, ReconciliationReplayConflict, ReconciliationRunConflict):
            raise
        except (ReconciliationContractError, ValueError, TypeError) as exc:
            raise ReconciliationRepositoryError("reconciliation persistence input is invalid") from exc
        except Exception as exc:
            raise ReconciliationRepositoryError("reconciliation persistence database operation failed") from exc

    def _run_insert_values(self, result: ReconciliationResult) -> tuple[object, ...]:
        run, policy = result.run, result.run.policy
        return (
            run.run_id, run.tenant_id, run.credential_id, run.account_scope, run.venue, run.market_type,
            run.instrument_id, run.asset_scope, run.local_generation_id, run.local_consumer_name,
            run.local_generation_build_fingerprint, run.local_checkpoint_watermark,
            run.external_observation_identity, run.external_observation_version,
            run.external_observation_fingerprint, run.local_observed_at, run.external_observed_at,
            run.as_of, run.correlation_id, policy.policy_version, policy.warning_degrades_health,
            policy.quantity_absolute, policy.monetary_absolute, policy.max_observation_age_seconds,
            policy.policy_fingerprint, run.build_fingerprint,
        )

    def _lock_generation(self, cursor: Any, result: ReconciliationResult) -> None:
        run = result.run
        cursor.execute(
            """
            SELECT consumer_name, build_fingerprint, state, source_high_watermark,
                   processed_high_watermark, completed_at
              FROM qd_projection_generations
             WHERE id = %s FOR UPDATE
            """,
            (run.local_generation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ReconciliationRunConflict("local projection generation is not visible")
        consumer, fingerprint, state, source_watermark, processed_watermark, completed = (_value(row, index) for index in range(6))
        if (consumer != run.local_consumer_name or fingerprint != run.local_generation_build_fingerprint
                or state != "READY" or completed is None
                or source_watermark != run.local_checkpoint_watermark
                or processed_watermark != run.local_checkpoint_watermark):
            raise ReconciliationRunConflict("local projection generation facts are not ready or exact")

    def _lock_run(self, cursor: Any, run_id: str) -> object:
        cursor.execute(
            """
            SELECT tenant_id, credential_id, account_scope, venue, market_type, instrument_id, asset_scope,
                   local_generation_id, local_consumer_name, local_generation_build_fingerprint,
                   local_checkpoint_watermark, external_observation_identity, external_observation_version,
                   external_observation_fingerprint, local_observed_at, external_observed_at, as_of,
                   correlation_id, policy_version, warning_degrades_health, quantity_absolute,
                   monetary_absolute, max_observation_age_seconds, policy_fingerprint, build_fingerprint,
                   discrepancy_count, replay_fingerprint, state,
                   (SELECT status FROM qd_reconciliation_checkpoints c WHERE c.run_id = qd_reconciliation_runs.id ORDER BY checkpoint_version DESC LIMIT 1)
              FROM qd_reconciliation_runs WHERE id = %s FOR UPDATE
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ReconciliationReplayConflict("reconciliation uniqueness conflict names another run")
        return row

    def _assert_run_identity(self, row: object, result: ReconciliationResult) -> None:
        run, policy = result.run, result.run.policy
        expected = (
            run.tenant_id, run.credential_id, run.account_scope, run.venue, run.market_type, run.instrument_id, run.asset_scope,
            run.local_generation_id, run.local_consumer_name, run.local_generation_build_fingerprint,
            run.local_checkpoint_watermark, run.external_observation_identity, run.external_observation_version,
            run.external_observation_fingerprint, run.local_observed_at, run.external_observed_at, run.as_of,
            run.correlation_id, policy.policy_version, policy.warning_degrades_health, policy.quantity_absolute,
            policy.monetary_absolute, policy.max_observation_age_seconds, policy.policy_fingerprint, run.build_fingerprint,
        )
        actual = tuple(_value(row, index) for index in range(25))
        decimals = {20, 21}
        uuids = {7}
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected, strict=True)):
            if index in decimals:
                equal = _decimal_equal(actual_value, expected_value)
            elif index in uuids:
                equal = _uuid_equal(actual_value, expected_value)
            else:
                equal = actual_value == expected_value
            if not equal:
                raise ReconciliationReplayConflict("reconciliation run identity names different immutable facts")

    def _insert_discrepancies(self, cursor: Any, result: ReconciliationResult) -> None:
        for discrepancy in result.discrepancies:
            identifier = str(uuid5(NAMESPACE_URL, f"reconciliation-v1|{result.run.run_id}|{discrepancy.discrepancy_fingerprint}"))
            local, external = discrepancy.local, discrepancy.external
            cursor.execute(
                """
                INSERT INTO qd_reconciliation_discrepancies (
                    id, run_id, fact_name, discrepancy_kind, severity,
                    local_value, local_value_kind, local_asset,
                    external_value, external_value_kind, external_asset, detail, discrepancy_fingerprint
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING RETURNING id
                """,
                (identifier, result.run.run_id, discrepancy.fact_name, discrepancy.kind.value, discrepancy.severity.value,
                 None if local is None else local.value, None if local is None else local.kind.value,
                 None if local is None else local.asset, None if external is None else external.value,
                 None if external is None else external.kind.value, None if external is None else external.asset,
                 discrepancy.detail, discrepancy.discrepancy_fingerprint),
            )
            if cursor.fetchone() is None:
                raise ReconciliationReplayConflict("reconciliation discrepancy identity names existing facts")

    def _assert_discrepancy_replay(self, cursor: Any, result: ReconciliationResult) -> None:
        for discrepancy in result.discrepancies:
            cursor.execute(
                "SELECT 1 FROM qd_reconciliation_discrepancies WHERE run_id = %s AND discrepancy_fingerprint = %s",
                (result.run.run_id, discrepancy.discrepancy_fingerprint),
            )
            if cursor.fetchone() is None:
                raise ReconciliationReplayConflict("persisted reconciliation discrepancies are incomplete")
