"""Transactional persistence boundary for immutable Shadow Diff runs.

Callers own the surrounding database transaction: this repository never commits
or rolls back.  It does not import a worker, exchange, executor, or runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.shadow_diff_contracts import (
    ShadowComparisonResult,
    ShadowDiffContractError,
    ShadowDiffFact,
    ShadowRunState,
)


class ShadowRepositoryError(RuntimeError):
    """Typed repository failure; no database-driver failure may escape."""


class ShadowReplayConflict(ShadowRepositoryError):
    """A durable replay identity names different immutable comparison facts."""


class ShadowRunConflict(ShadowRepositoryError):
    """A comparison run cannot progress from its durable state."""


class ShadowPersistDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class ShadowPersistResult:
    run_id: str
    disposition: ShadowPersistDisposition
    replay_fingerprint: str


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ShadowRepositoryError("completed_at must use a zero UTC offset")
    return value.astimezone(timezone.utc)


def _row_value(row: object, position: int) -> object:
    try:
        return row[position]  # type: ignore[index]
    except (TypeError, IndexError) as exc:
        raise ShadowRepositoryError("database returned an incomplete shadow row") from exc


def _decimal_equal(left: object, right: object) -> bool:
    try:
        return Decimal(left) == Decimal(right)
    except Exception:
        return False


def _uuid_equal(left: object, right: object) -> bool:
    try:
        return str(UUID(str(left))).lower() == str(UUID(str(right))).lower()
    except (TypeError, ValueError, AttributeError):
        return False


def _driver_error(exc: Exception) -> ShadowRepositoryError:
    return ShadowRepositoryError("shadow persistence database operation failed")


class ShadowDiffRepository:
    """Append-only run and diff persistence with replay-safe facts."""

    def persist_comparison(
        self,
        connection: Any,
        result: ShadowComparisonResult,
        *,
        completed_at: datetime,
    ) -> ShadowPersistResult:
        if not isinstance(result, ShadowComparisonResult):
            raise ShadowRepositoryError("result must use ShadowComparisonResult")
        if result.run.state is not ShadowRunState.BUILDING:
            raise ShadowRepositoryError("only a BUILDING shadow run may be persisted")
        completed_at = _utc(completed_at)
        run = result.run
        try:
            with connection.cursor() as cursor:
                self._lock_candidate_generation(cursor, run)
                cursor.execute(
                    """
                    INSERT INTO qd_shadow_comparison_runs (
                        id, tenant_id, credential_id, account_scope, instrument_id, market_type,
                        comparison_contract_version, legacy_source_identity, legacy_source_version,
                        legacy_source_fingerprint, candidate_source_fingerprint, candidate_generation_id,
                        candidate_consumer_name, candidate_generation_build_fingerprint, candidate_checkpoint_watermark,
                        as_of, correlation_id, tolerance_policy_version, quantity_absolute, quantity_relative,
                        monetary_absolute, monetary_relative, ratio_absolute, tolerance_policy_fingerprint,
                        build_fingerprint, replay_fingerprint, state, completed_at, failure_reason
                    ) VALUES (%s,%s,%s,%s,%s,%s,'shadow-diff-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'BUILDING',NULL,NULL)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (
                        run.run_id, run.tenant_id, run.credential_id, run.account_scope,
                        run.instrument_id, run.market_type, run.legacy_source_identity, run.legacy_source_version,
                        result.legacy.source_fingerprint, result.candidate.source_fingerprint, run.candidate_generation_id,
                        run.candidate_consumer_name, run.candidate_generation_build_fingerprint,
                        run.candidate_checkpoint_watermark, run.as_of, run.correlation_id, run.policy.policy_version,
                        run.policy.quantity_absolute, run.policy.quantity_relative, run.policy.monetary_absolute,
                        run.policy.monetary_relative, run.policy.ratio_absolute,
                        run.policy.tolerance_policy_fingerprint, run.build_fingerprint,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    persisted = self._lock_run(cursor, run.run_id)
                    self._assert_run_identity(persisted, result)
                    state = _row_value(persisted, 24)
                    persisted_replay = _row_value(persisted, 23)
                    if state == ShadowRunState.COMPLETE.value:
                        if persisted_replay != result.replay_fingerprint:
                            raise ShadowReplayConflict("completed run names different replay facts")
                        self._assert_persisted_diffs(cursor, result)
                        return ShadowPersistResult(run.run_id, ShadowPersistDisposition.REPLAYED, result.replay_fingerprint)
                    if state != ShadowRunState.BUILDING.value:
                        raise ShadowRunConflict("shadow run is not replayable")
                self._persist_diffs(cursor, result)
                cursor.execute(
                    """
                    UPDATE qd_shadow_comparison_runs
                       SET state = 'COMPLETE', replay_fingerprint = %s, completed_at = %s
                     WHERE id = %s AND state = 'BUILDING'
                    RETURNING id
                    """,
                    (result.replay_fingerprint, completed_at, run.run_id),
                )
                if cursor.fetchone() is None:
                    raise ShadowRunConflict("shadow run completion was not applied")
                return ShadowPersistResult(run.run_id, ShadowPersistDisposition.CREATED, result.replay_fingerprint)
        except (ShadowRepositoryError, ShadowReplayConflict, ShadowRunConflict):
            raise
        except Exception as exc:
            raise _driver_error(exc) from exc

    def _lock_candidate_generation(self, cursor: Any, run: object) -> None:
        cursor.execute(
            """
            SELECT consumer_name, build_fingerprint, state, source_high_watermark,
                   processed_high_watermark, completed_at
              FROM qd_projection_generations
             WHERE id = %s
             FOR UPDATE
            """,
            (run.candidate_generation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ShadowRunConflict("candidate projection generation is not visible")
        consumer, build, state, source_watermark, processed_watermark, completed = (
            _row_value(row, index) for index in range(6)
        )
        if (
            state != "READY"
            or completed is None
            or consumer != run.candidate_consumer_name
            or build != run.candidate_generation_build_fingerprint
            or source_watermark != run.candidate_checkpoint_watermark
            or processed_watermark != run.candidate_checkpoint_watermark
        ):
            raise ShadowRunConflict("candidate projection generation facts are not ready or exact")

    def _lock_run(self, cursor: Any, run_id: str) -> object:
        cursor.execute(
            """
            SELECT tenant_id, credential_id, account_scope, instrument_id, market_type,
                   legacy_source_fingerprint, candidate_source_fingerprint,
                   legacy_source_identity, legacy_source_version, candidate_generation_id,
                   candidate_consumer_name, candidate_generation_build_fingerprint,
                   candidate_checkpoint_watermark, as_of, correlation_id, tolerance_policy_version,
                   quantity_absolute, quantity_relative, monetary_absolute, monetary_relative,
                   ratio_absolute, tolerance_policy_fingerprint, build_fingerprint, replay_fingerprint, state
              FROM qd_shadow_comparison_runs
             WHERE id = %s
             FOR UPDATE
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ShadowRunConflict("shadow comparison uniqueness conflict is not visible")
        return row

    def _assert_run_identity(self, row: object, result: ShadowComparisonResult) -> None:
        run = result.run
        expected = (
            run.tenant_id, run.credential_id, run.account_scope, run.instrument_id,
            run.market_type, result.legacy.source_fingerprint, result.candidate.source_fingerprint,
            run.legacy_source_identity, run.legacy_source_version, run.candidate_generation_id,
            run.candidate_consumer_name, run.candidate_generation_build_fingerprint,
            run.candidate_checkpoint_watermark, run.as_of, run.correlation_id, run.policy.policy_version,
            run.policy.quantity_absolute, run.policy.quantity_relative, run.policy.monetary_absolute,
            run.policy.monetary_relative, run.policy.ratio_absolute,
            run.policy.tolerance_policy_fingerprint, run.build_fingerprint,
        )
        actual = tuple(_row_value(row, index) for index in range(23))
        if actual[:9] != expected[:9] or not _uuid_equal(actual[9], expected[9]) or actual[10:16] != expected[10:16] or not all(_decimal_equal(actual[index], expected[index]) for index in range(16, 21)) or actual[21:] != expected[21:]:
            raise ShadowReplayConflict("shadow run identity names different immutable facts")

    def _persist_diffs(self, cursor: Any, result: ShadowComparisonResult) -> None:
        for fact in result.diffs:
            fact_id = str(uuid5(NAMESPACE_URL, f"shadow-diff-v1|{result.run.run_id}|{fact.diff_fingerprint}"))
            legacy = fact.legacy
            candidate = fact.candidate
            cursor.execute(
                """
                INSERT INTO qd_shadow_diff_facts (
                    id, run_id, fact_name, diff_kind, severity,
                    legacy_value, legacy_value_kind, legacy_asset,
                    candidate_value, candidate_value_kind, candidate_asset,
                    detail, diff_fingerprint
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    fact_id, result.run.run_id, fact.fact_name, fact.kind.value, fact.severity.value,
                    None if legacy is None else legacy.value,
                    None if legacy is None else legacy.kind.value,
                    None if legacy is None else legacy.asset,
                    None if candidate is None else candidate.value,
                    None if candidate is None else candidate.kind.value,
                    None if candidate is None else candidate.asset,
                    fact.detail, fact.diff_fingerprint,
                ),
            )
            if cursor.fetchone() is None:
                self._assert_diff_replay(cursor, result.run.run_id, fact)

    def _assert_persisted_diffs(self, cursor: Any, result: ShadowComparisonResult) -> None:
        for fact in result.diffs:
            self._assert_diff_replay(cursor, result.run.run_id, fact)

    def _assert_diff_replay(self, cursor: Any, run_id: str, fact: ShadowDiffFact) -> None:
        cursor.execute(
            """
            SELECT fact_name, diff_kind, severity, legacy_value, legacy_value_kind, legacy_asset,
                   candidate_value, candidate_value_kind, candidate_asset, detail
              FROM qd_shadow_diff_facts
             WHERE run_id = %s AND diff_fingerprint = %s
            """,
            (run_id, fact.diff_fingerprint),
        )
        row = cursor.fetchone()
        if row is None:
            raise ShadowReplayConflict("shadow diff replay conflict is not visible")
        legacy, candidate = fact.legacy, fact.candidate
        expected = (
            fact.fact_name, fact.kind.value, fact.severity.value,
            None if legacy is None else legacy.value,
            None if legacy is None else legacy.kind.value,
            None if legacy is None else legacy.asset,
            None if candidate is None else candidate.value,
            None if candidate is None else candidate.kind.value,
            None if candidate is None else candidate.asset,
            fact.detail,
        )
        for index, (actual, wanted) in enumerate(zip(row, expected, strict=True)):
            if index in (3, 6) and wanted is not None:
                if not _decimal_equal(actual, wanted):
                    raise ShadowReplayConflict("shadow diff replay names different numeric facts")
            elif actual != wanted:
                raise ShadowReplayConflict("shadow diff replay names different immutable facts")
