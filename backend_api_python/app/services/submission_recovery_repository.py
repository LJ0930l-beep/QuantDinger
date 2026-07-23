"""One-transaction persistence of a pure submission-recovery decision.

This boundary writes only already-redacted observations and reducer-authorized
events.  It contains no venue client, retry, submit, or cancel behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from app.domain.order_state_machine import StateEventConflict
from app.domain.submission_recovery_contracts import RecoveryDecision, SubmissionRecoveryContractError
from app.services.order_state_repository import (
    Connection,
    Cursor,
    OrderStateRepository,
    StateEventResult,
    _row_value,
)


class RecoveryDisposition(str, Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"
    OBSERVATION_ONLY = "OBSERVATION_ONLY"


@dataclass(frozen=True, slots=True)
class RecoveryPersistenceResult:
    observation_id: str
    order_event: StateEventResult | None
    attempt_event: StateEventResult | None
    disposition: RecoveryDisposition


class SubmissionRecoveryRepository:
    """Persist a decision in fixed ``order -> attempt`` lock order."""

    def __init__(self, state_repository: OrderStateRepository | None = None) -> None:
        self._states = state_repository or OrderStateRepository()

    def apply(self, connection: Connection, decision: RecoveryDecision) -> RecoveryPersistenceResult:
        cursor = connection.cursor()
        try:
            self._lock_and_verify_order(cursor, decision)
            self._lock_and_verify_attempt(cursor, decision)
            self._lock_and_verify_snapshots(cursor, decision)
            observation_id, replayed_observation = self._append_observation(cursor, decision)
            if decision.order_transition is None and decision.attempt_transition is None:
                connection.commit()
                return RecoveryPersistenceResult(
                    observation_id, None, None,
                    RecoveryDisposition.REPLAYED if replayed_observation else RecoveryDisposition.OBSERVATION_ONLY,
                )
            # State helpers take no commit.  They retain the established lock
            # order and participate in this exact observation transaction.
            order_result = None
            attempt_result = None
            if decision.order_transition is not None:
                order_result = self._states._apply_order_locked(cursor, decision.order_transition)
            if decision.attempt_transition is not None:
                attempt_result = self._states._apply_attempt_locked(cursor, decision.attempt_transition)
            connection.commit()
            return RecoveryPersistenceResult(observation_id, order_result, attempt_result, RecoveryDisposition.APPLIED)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    @staticmethod
    def _lock_and_verify_order(cursor: Cursor, decision: RecoveryDecision) -> None:
        cursor.execute(
            """
            SELECT id, tenant_id, credential_id, account_scope, instrument_id,
                   market_type, state, version, last_event_seq
              FROM qd_economic_orders
             WHERE id = %s FOR UPDATE
            """,
            (decision.order.id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise SubmissionRecoveryContractError("persisted economic order is missing")
        expected = decision.order
        observed = (
            str(_row_value(row, 0, "id")), int(_row_value(row, 1, "tenant_id")),
            int(_row_value(row, 2, "credential_id")), str(_row_value(row, 3, "account_scope")),
            str(_row_value(row, 4, "instrument_id")).upper(), str(_row_value(row, 5, "market_type")).lower(),
            str(_row_value(row, 6, "state")), int(_row_value(row, 7, "version")), int(_row_value(row, 8, "last_event_seq")),
        )
        wanted = (expected.id, expected.tenant_id, expected.credential_id, expected.account_scope,
                  expected.instrument_id, expected.market_type, expected.state.value, expected.version,
                  expected.last_event_seq)
        if observed != wanted:
            raise SubmissionRecoveryContractError("persisted economic order scope, state, or version mismatch")

    @staticmethod
    def _lock_and_verify_attempt(cursor: Cursor, decision: RecoveryDecision) -> None:
        cursor.execute(
            """
            SELECT id, economic_order_id, tenant_id, credential_id, account_scope,
                   instrument_id, exchange, market_type, state, version, last_event_seq,
                   venue_capability_snapshot_id, recovery_policy_snapshot_id,
                   canonical_client_order_id, client_id_algorithm_version,
                   broker_prefix_normalization_version, broker_prefix
              FROM qd_submission_attempts
             WHERE id = %s FOR UPDATE
            """,
            (decision.attempt.id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise SubmissionRecoveryContractError("persisted submission attempt is missing")
        expected = decision.attempt
        observed = (
            str(_row_value(row, 0, "id")), str(_row_value(row, 1, "economic_order_id")),
            int(_row_value(row, 2, "tenant_id")), int(_row_value(row, 3, "credential_id")),
            str(_row_value(row, 4, "account_scope")), str(_row_value(row, 5, "instrument_id")).upper(),
            str(_row_value(row, 6, "exchange")).lower(), str(_row_value(row, 7, "market_type")).lower(),
            str(_row_value(row, 8, "state")), int(_row_value(row, 9, "version")), int(_row_value(row, 10, "last_event_seq")),
            str(_row_value(row, 11, "venue_capability_snapshot_id")), str(_row_value(row, 12, "recovery_policy_snapshot_id")),
            str(_row_value(row, 13, "canonical_client_order_id")), str(_row_value(row, 14, "client_id_algorithm_version")),
            str(_row_value(row, 15, "broker_prefix_normalization_version")), str(_row_value(row, 16, "broker_prefix")),
        )
        wanted = (
            expected.id, expected.economic_order_id, expected.tenant_id, expected.credential_id,
            expected.account_scope, expected.instrument_id, expected.exchange, expected.market_type,
            expected.state.value, expected.version, expected.last_event_seq,
            expected.venue_capability_snapshot_id, expected.recovery_policy_snapshot_id,
            expected.canonical_client_order_id, expected.client_id_algorithm_version,
            expected.broker_prefix_normalization_version, expected.broker_prefix,
        )
        if observed != wanted:
            raise SubmissionRecoveryContractError("persisted attempt scope, snapshot, identity, state, or version mismatch")

    @staticmethod
    def _lock_and_verify_snapshots(cursor: Cursor, decision: RecoveryDecision) -> None:
        """Verify the exact persisted policy/capability pair, including scope.

        The schema's composite foreign keys are the final database guard; this
        read is still required because NOT VALID legacy constraints may exist
        during the expand-only migration window.
        """
        cursor.execute(
            """
            SELECT p.id AS policy_id, p.capability_snapshot_id, p.exchange AS policy_exchange,
                   p.market_type AS policy_market_type, c.id AS capability_id,
                   c.exchange AS capability_exchange, c.market_type AS capability_market_type
              FROM qd_submission_recovery_policy_snapshots AS p
              JOIN qd_venue_capability_snapshots AS c ON c.id = p.capability_snapshot_id
             WHERE p.id = %s AND c.id = %s
             FOR KEY SHARE OF p, c
            """,
            (decision.policy.id, decision.policy.capability_snapshot_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise SubmissionRecoveryContractError("persisted recovery policy/capability pair is missing")
        observed = (
            str(_row_value(row, 0, "policy_id")), str(_row_value(row, 1, "capability_snapshot_id")),
            str(_row_value(row, 2, "policy_exchange")).lower(), str(_row_value(row, 3, "policy_market_type")).lower(),
            str(_row_value(row, 4, "capability_id")), str(_row_value(row, 5, "capability_exchange")).lower(),
            str(_row_value(row, 6, "capability_market_type")).lower(),
        )
        expected = (
            decision.policy.id, decision.policy.capability_snapshot_id, decision.policy.exchange,
            decision.policy.market_type, decision.policy.capability_snapshot_id, decision.attempt.exchange,
            decision.attempt.market_type,
        )
        if observed != expected:
            raise SubmissionRecoveryContractError("persisted recovery policy/capability scope mismatch")

    @staticmethod
    def _append_observation(cursor: Cursor, decision: RecoveryDecision) -> tuple[str, bool]:
        observation = decision.observation
        payload_json = observation.canonical_payload_json
        cursor.execute(
            """
            INSERT INTO qd_exchange_order_observations (
                id, attempt_id, observation_source, payload_hash, payload_json, observed_at
            ) VALUES (%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (str(uuid4()), observation.attempt_id, observation.observation_source,
             observation.payload_hash, payload_json, observation.observed_at),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return str(_row_value(inserted, 0, "id")), False
        cursor.execute(
            """
            SELECT id, payload_json
              FROM qd_exchange_order_observations
             WHERE attempt_id = %s AND observation_source = %s AND payload_hash = %s
             FOR UPDATE
            """,
            (observation.attempt_id, observation.observation_source, observation.payload_hash),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise StateEventConflict("observation uniqueness conflict is not visible")
        existing_payload = _row_value(existing, 1, "payload_json")
        if isinstance(existing_payload, str):
            # JSONB adapters vary; compare canonical strings through the already
            # canonical request material rather than assuming one driver shape.
            import json
            existing_payload = json.dumps(json.loads(existing_payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        else:
            import json
            existing_payload = json.dumps(existing_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if existing_payload != payload_json:
            raise StateEventConflict("observation hash is reused with different canonical evidence")
        return str(_row_value(existing, 0, "id")), True
