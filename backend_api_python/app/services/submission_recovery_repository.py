"""Atomic storage for reducer-produced submission-recovery decisions only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from uuid import uuid4

from app.domain.order_state_machine import StateEventConflict
from app.domain.submission_recovery_contracts import RecoveryDecision, SubmissionRecoveryContractError
from app.services.order_state_repository import Connection, Cursor, OrderStateRepository, StateEventResult, _row_value


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
    def __init__(self, state_repository: OrderStateRepository | None = None) -> None:
        self._states = state_repository or OrderStateRepository()

    def apply(self, connection: Connection, decision: RecoveryDecision) -> RecoveryPersistenceResult:
        cursor = connection.cursor()
        try:
            # The immutable scope locks are always acquired in this order.
            self._lock_and_verify_order_scope(cursor, decision)
            self._lock_and_verify_attempt_scope(cursor, decision)
            self._lock_and_verify_exchange_order_scope(cursor, decision)
            self._lock_and_verify_snapshot_facts(cursor, decision)
            observation_id, observation_replayed = self._append_or_replay_observation(cursor, decision)
            if observation_replayed:
                result = self._replay_existing_events(cursor, decision)
                connection.commit()
                return RecoveryPersistenceResult(observation_id, result[0], result[1], RecoveryDisposition.REPLAYED)
            self._verify_fresh_ingress_versions(cursor, decision)
            order_result = self._states._apply_order_locked(cursor, decision.order_transition) if decision.order_transition else None
            attempt_result = self._states._apply_attempt_locked(cursor, decision.attempt_transition) if decision.attempt_transition else None
            connection.commit()
            return RecoveryPersistenceResult(observation_id, order_result, attempt_result,
                                             RecoveryDisposition.APPLIED if (order_result or attempt_result) else RecoveryDisposition.OBSERVATION_ONLY)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    @staticmethod
    def _lock_and_verify_order_scope(cursor: Cursor, decision: RecoveryDecision) -> None:
        scope = decision.order.scope
        cursor.execute("""SELECT id FROM qd_economic_orders WHERE id=%s AND tenant_id=%s AND credential_id=%s
                          AND account_scope=%s AND instrument_id=%s AND market_type=%s FOR UPDATE""",
                       (decision.order.id, scope.tenant_id, scope.credential_id, scope.account_scope, scope.instrument_id, scope.market_type))
        if cursor.fetchone() is None:
            raise SubmissionRecoveryContractError("persisted economic-order scope mismatch")

    @staticmethod
    def _lock_and_verify_attempt_scope(cursor: Cursor, decision: RecoveryDecision) -> None:
        fact, scope = decision.attempt, decision.attempt.scope
        cursor.execute("""SELECT id FROM qd_submission_attempts WHERE id=%s AND economic_order_id=%s AND tenant_id=%s
                          AND credential_id=%s AND account_scope=%s AND instrument_id=%s AND exchange=%s AND market_type=%s
                          AND canonical_client_order_id=%s AND venue_client_order_id=%s
                          AND venue_capability_snapshot_id=%s AND recovery_policy_snapshot_id=%s
                          AND client_id_algorithm_version=%s AND broker_prefix_normalization_version=%s AND broker_prefix=%s FOR UPDATE""",
                       (fact.id, scope.economic_order_id, scope.tenant_id, scope.credential_id, scope.account_scope,
                        scope.instrument_id, scope.exchange, scope.market_type, fact.canonical_client_order_id,
                        fact.venue_client_order_id, fact.venue_capability_snapshot_id, fact.recovery_policy_snapshot_id,
                        fact.client_id_algorithm_version, fact.broker_prefix_normalization_version, fact.broker_prefix))
        if cursor.fetchone() is None:
            raise SubmissionRecoveryContractError("persisted attempt scope, identity, or snapshot mismatch")

    @staticmethod
    def _lock_and_verify_exchange_order_scope(cursor: Cursor, decision: RecoveryDecision) -> None:
        fact = decision.exchange_order
        if fact is None:
            return
        cursor.execute("""SELECT id FROM qd_exchange_orders WHERE id=%s AND attempt_id=%s AND economic_order_id=%s
                          AND exchange=%s AND market_type=%s AND account_scope=%s AND instrument_id=%s
                          AND exchange_order_id=%s AND venue_client_order_id=%s FOR KEY SHARE""",
                       (fact.exchange_order_pk, fact.attempt_id, fact.economic_order_id, fact.exchange, fact.market_type,
                        fact.account_scope, fact.instrument_id, fact.exchange_order_id, fact.venue_client_order_id))
        if cursor.fetchone() is None:
            raise SubmissionRecoveryContractError("persisted exchange-order identity mismatch")

    @staticmethod
    def _lock_and_verify_snapshot_facts(cursor: Cursor, decision: RecoveryDecision) -> None:
        c, p = decision.capability, decision.policy
        cursor.execute("""SELECT c.id, c.exchange, c.market_type, c.capability_version, c.profile_hash,
                          c.query_by_exchange_order_id, c.query_by_client_order_id, p.id, p.capability_snapshot_id,
                          p.exchange, p.market_type, p.policy_version, p.policy_hash, p.capability_query_by_client_order_id,
                          p.client_id_query_authoritative, p.order_history_authoritative, p.fill_history_authoritative,
                          p.not_found_min_query_count, p.not_found_grace_seconds, p.not_found_action
                          FROM qd_venue_capability_snapshots c JOIN qd_submission_recovery_policy_snapshots p ON p.capability_snapshot_id=c.id
                          WHERE c.id=%s AND p.id=%s FOR KEY SHARE OF c,p""", (c.id, p.id))
        row = cursor.fetchone()
        expected = (c.id, c.exchange, c.market_type, c.capability_version, c.profile_hash, c.query_by_exchange_order_id,
                    c.query_by_client_order_id, p.id, p.capability_snapshot_id, p.exchange, p.market_type, p.policy_version,
                    p.policy_hash, p.capability_query_by_client_order_id, p.client_id_query_authoritative,
                    p.order_history_authoritative, p.fill_history_authoritative, p.not_found_min_query_count,
                    p.not_found_grace_seconds, p.not_found_action)
        if row is None or tuple(row) != expected:
            raise SubmissionRecoveryContractError("persisted capability/policy facts mismatch")

    @staticmethod
    def _verify_fresh_ingress_versions(cursor: Cursor, decision: RecoveryDecision) -> None:
        cursor.execute("SELECT state,version,last_event_seq FROM qd_economic_orders WHERE id=%s FOR UPDATE", (decision.order.id,))
        row = cursor.fetchone()
        if row is None or (str(_row_value(row, 0, "state")), int(_row_value(row, 1, "version")), int(_row_value(row, 2, "last_event_seq"))) != (decision.order.state.value, decision.order.version, decision.order.last_event_seq):
            raise StateEventConflict("fresh recovery economic order has advanced or drifted")
        cursor.execute("SELECT state,version,last_event_seq FROM qd_submission_attempts WHERE id=%s FOR UPDATE", (decision.attempt.id,))
        row = cursor.fetchone()
        if row is None or (str(_row_value(row, 0, "state")), int(_row_value(row, 1, "version")), int(_row_value(row, 2, "last_event_seq"))) != (decision.attempt.state.value, decision.attempt.version, decision.attempt.last_event_seq):
            raise StateEventConflict("fresh recovery attempt has advanced or drifted")

    @staticmethod
    def _append_or_replay_observation(cursor: Cursor, decision: RecoveryDecision) -> tuple[str, bool]:
        obs = decision.observation
        cursor.execute("""SELECT id,payload_json FROM qd_exchange_order_observations WHERE attempt_id=%s AND observation_source=%s
                          AND payload_json ->> 'query_invocation_id'=%s FOR UPDATE""",
                       (obs.attempt_id, obs.observation_source, obs.query_invocation_id))
        existing = cursor.fetchone()
        if existing is not None:
            existing_json = json.dumps(json.loads(_row_value(existing, 1, "payload_json")) if isinstance(_row_value(existing, 1, "payload_json"), str) else _row_value(existing, 1, "payload_json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if existing_json != obs.canonical_payload_json:
                raise StateEventConflict("query invocation is reused with different observation facts")
            return str(_row_value(existing, 0, "id")), True
        cursor.execute("""INSERT INTO qd_exchange_order_observations (id,attempt_id,observation_source,payload_hash,payload_json,observed_at)
                          VALUES (%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING RETURNING id""",
                       (str(uuid4()), obs.attempt_id, obs.observation_source, obs.payload_hash, obs.canonical_payload_json, obs.observed_at))
        inserted = cursor.fetchone()
        if inserted is not None:
            return str(_row_value(inserted, 0, "id")), False
        cursor.execute("SELECT id,payload_json FROM qd_exchange_order_observations WHERE attempt_id=%s AND observation_source=%s AND payload_hash=%s FOR UPDATE",
                       (obs.attempt_id, obs.observation_source, obs.payload_hash))
        existing = cursor.fetchone()
        if existing is None:
            raise StateEventConflict("observation uniqueness conflict is not visible")
        existing_json = json.dumps(json.loads(_row_value(existing, 1, "payload_json")) if isinstance(_row_value(existing, 1, "payload_json"), str) else _row_value(existing, 1, "payload_json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if existing_json != obs.canonical_payload_json:
            raise StateEventConflict("observation hash is reused with different facts")
        return str(_row_value(existing, 0, "id")), True

    def _replay_existing_events(self, cursor: Cursor, decision: RecoveryDecision) -> tuple[StateEventResult | None, StateEventResult | None]:
        if decision.order_transition is None and decision.attempt_transition is None:
            return None, None
        order_result = self._states._apply_order_locked(cursor, decision.order_transition) if decision.order_transition else None
        attempt_result = self._states._apply_attempt_locked(cursor, decision.attempt_transition) if decision.attempt_transition else None
        results = tuple(result for result in (order_result, attempt_result) if result is not None)
        if any(result.disposition.value != "REPLAYED" for result in results):
            raise StateEventConflict("recovery replay has partial event history")
        return order_result, attempt_result
