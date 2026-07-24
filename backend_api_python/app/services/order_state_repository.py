"""Atomic append-and-CAS persistence for authorized order state events.

No caller can pass free-form states into this repository.  It accepts the
pure-domain ``AuthorizedTransition`` produced by the state-machine reducer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Protocol
from uuid import uuid4

from app.domain.order_state_machine import AggregateType, AuthorizedTransition, StateEventConflict


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class StateEventDisposition(str, Enum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class StateEventResult:
    aggregate_id: str
    resulting_state: str
    resulting_version: int
    disposition: StateEventDisposition


def _row_value(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


class OrderStateRepository:
    """One-transaction event append plus guarded aggregate update."""

    def apply_order_transition(self, connection: Connection, transition: AuthorizedTransition) -> StateEventResult:
        if transition.aggregate_type is not AggregateType.ECONOMIC_ORDER:
            raise StateEventConflict("economic order repository requires an economic-order transition")
        cursor = connection.cursor()
        try:
            result = self._apply_order_locked(cursor, transition)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def apply_attempt_transition(self, connection: Connection, transition: AuthorizedTransition) -> StateEventResult:
        if transition.aggregate_type is not AggregateType.SUBMISSION_ATTEMPT:
            raise StateEventConflict("attempt repository requires a submission-attempt transition")
        cursor = connection.cursor()
        try:
            result = self._apply_attempt_locked(cursor, transition)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _apply_order_locked(self, cursor: Cursor, transition: AuthorizedTransition) -> StateEventResult:
        cursor.execute(
            """
            SELECT state, version, last_event_seq
             FROM qd_economic_orders
             WHERE id = %s AND tenant_id = %s AND credential_id = %s
               AND account_scope = %s AND instrument_id = %s AND market_type = %s
             FOR UPDATE
            """,
            (transition.aggregate_id, transition.aggregate_scope.tenant_id, transition.aggregate_scope.credential_id,
             transition.aggregate_scope.account_scope, transition.aggregate_scope.instrument_id, transition.aggregate_scope.market_type),
        )
        row = cursor.fetchone()
        if row is None:
            raise StateEventConflict("economic order does not exist")
        state, version, sequence = self._aggregate_values(row)
        replay = self._existing_order_event(cursor, transition)
        if replay is not None:
            return replay
        self._verify_aggregate(state, version, sequence, transition)
        cursor.execute(
            """
            INSERT INTO qd_order_state_events (
                id, economic_order_id, event_seq, from_state, to_state,
                reason_code, actor_type, evidence_hash, occurred_at,
                expected_version, resulting_version, idempotency_key,
                event_fingerprint, correlation_id, canonical_payload_json
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
            )
            """,
            self._event_params(transition, include_economic_order=True),
        )
        cursor.execute(
            """
            UPDATE qd_economic_orders
               SET state = %s, version = %s, last_event_seq = %s, updated_at = NOW()
             WHERE id = %s AND tenant_id = %s AND credential_id = %s AND account_scope = %s
               AND instrument_id = %s AND market_type = %s AND state = %s AND version = %s AND last_event_seq = %s
            RETURNING state, version
            """,
            (transition.target_state, transition.resulting_version, transition.event_seq,
             transition.aggregate_id, transition.aggregate_scope.tenant_id, transition.aggregate_scope.credential_id,
             transition.aggregate_scope.account_scope, transition.aggregate_scope.instrument_id, transition.aggregate_scope.market_type,
             transition.current_state, transition.expected_version,
             transition.expected_version),
        )
        changed = cursor.fetchone()
        if changed is None:
            raise StateEventConflict("economic order CAS did not apply")
        return StateEventResult(transition.aggregate_id, str(_row_value(changed, 0, "state")), int(_row_value(changed, 1, "version")), StateEventDisposition.APPLIED)

    def _apply_attempt_locked(self, cursor: Cursor, transition: AuthorizedTransition) -> StateEventResult:
        cursor.execute(
            """
            SELECT economic_order_id, state, version, last_event_seq
              FROM qd_submission_attempts
             WHERE id = %s AND economic_order_id = %s AND tenant_id = %s AND credential_id = %s
               AND account_scope = %s AND instrument_id = %s AND exchange = %s AND market_type = %s
             FOR UPDATE
            """,
            (transition.aggregate_id, transition.aggregate_scope.economic_order_id, transition.aggregate_scope.tenant_id,
             transition.aggregate_scope.credential_id, transition.aggregate_scope.account_scope,
             transition.aggregate_scope.instrument_id, transition.aggregate_scope.exchange, transition.aggregate_scope.market_type),
        )
        row = cursor.fetchone()
        if row is None:
            raise StateEventConflict("submission attempt does not exist")
        economic_order_id = str(_row_value(row, 0, "economic_order_id"))
        state, version, sequence = self._aggregate_values(row, offset=1)
        replay = self._existing_attempt_event(cursor, transition)
        if replay is not None:
            return replay
        self._verify_aggregate(state, version, sequence, transition)
        cursor.execute(
            """
            INSERT INTO qd_submission_attempt_state_events (
                id, attempt_id, economic_order_id, event_seq, expected_version,
                resulting_version, from_state, to_state, reason_code, actor_type,
                correlation_id, idempotency_key, event_fingerprint, evidence_hash,
                canonical_payload_json, occurred_at
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
            )
            """,
            (str(uuid4()), transition.aggregate_id, economic_order_id, transition.event_seq,
             transition.expected_version, transition.resulting_version, transition.current_state,
             transition.target_state, transition.reason_code, transition.actor.value,
             transition.correlation_id, transition.idempotency_key, transition.event_fingerprint,
             transition.evidence_hash, self._payload_with_contract(transition), transition.occurred_at),
        )
        cursor.execute(
            """
            UPDATE qd_submission_attempts
               SET state = %s, version = %s, last_event_seq = %s
             WHERE id = %s AND economic_order_id = %s AND tenant_id = %s AND credential_id = %s
               AND account_scope = %s AND instrument_id = %s AND exchange = %s AND market_type = %s
               AND state = %s AND version = %s AND last_event_seq = %s
            RETURNING state, version
            """,
            (transition.target_state, transition.resulting_version, transition.event_seq,
             transition.aggregate_id, transition.aggregate_scope.economic_order_id, transition.aggregate_scope.tenant_id,
             transition.aggregate_scope.credential_id, transition.aggregate_scope.account_scope,
             transition.aggregate_scope.instrument_id, transition.aggregate_scope.exchange, transition.aggregate_scope.market_type,
             transition.current_state, transition.expected_version,
             transition.expected_version),
        )
        changed = cursor.fetchone()
        if changed is None:
            raise StateEventConflict("submission attempt CAS did not apply")
        return StateEventResult(transition.aggregate_id, str(_row_value(changed, 0, "state")), int(_row_value(changed, 1, "version")), StateEventDisposition.APPLIED)

    def _existing_order_event(self, cursor: Cursor, transition: AuthorizedTransition) -> StateEventResult | None:
        cursor.execute(
            """
            SELECT to_state, resulting_version, event_fingerprint, idempotency_key
              FROM qd_order_state_events AS event
              JOIN qd_economic_orders AS aggregate ON aggregate.id = event.economic_order_id
             WHERE event.economic_order_id = %s AND aggregate.tenant_id = %s AND aggregate.credential_id = %s
               AND aggregate.account_scope = %s AND aggregate.instrument_id = %s AND aggregate.market_type = %s
               AND (event.idempotency_key = %s OR event.event_fingerprint = %s)
             FOR UPDATE
            """,
            (transition.aggregate_id, transition.aggregate_scope.tenant_id, transition.aggregate_scope.credential_id,
             transition.aggregate_scope.account_scope, transition.aggregate_scope.instrument_id, transition.aggregate_scope.market_type,
             transition.idempotency_key, transition.event_fingerprint),
        )
        return self._replay_or_conflict(cursor.fetchone(), transition)

    def _existing_attempt_event(self, cursor: Cursor, transition: AuthorizedTransition) -> StateEventResult | None:
        cursor.execute(
            """
            SELECT to_state, resulting_version, event_fingerprint, idempotency_key
              FROM qd_submission_attempt_state_events AS event
              JOIN qd_submission_attempts AS aggregate ON aggregate.id = event.attempt_id
             WHERE event.attempt_id = %s AND aggregate.economic_order_id = %s AND aggregate.tenant_id = %s
               AND aggregate.credential_id = %s AND aggregate.account_scope = %s AND aggregate.instrument_id = %s
               AND aggregate.exchange = %s AND aggregate.market_type = %s
               AND (event.idempotency_key = %s OR event.event_fingerprint = %s)
             FOR UPDATE
            """,
            (transition.aggregate_id, transition.aggregate_scope.economic_order_id, transition.aggregate_scope.tenant_id,
             transition.aggregate_scope.credential_id, transition.aggregate_scope.account_scope,
             transition.aggregate_scope.instrument_id, transition.aggregate_scope.exchange, transition.aggregate_scope.market_type,
             transition.idempotency_key, transition.event_fingerprint),
        )
        return self._replay_or_conflict(cursor.fetchone(), transition)

    @staticmethod
    def _aggregate_values(row: Any, offset: int = 0) -> tuple[str, int, int]:
        return (str(_row_value(row, offset, "state")), int(_row_value(row, offset + 1, "version")), int(_row_value(row, offset + 2, "last_event_seq")))

    @staticmethod
    def _verify_aggregate(state: str, version: int, sequence: int, transition: AuthorizedTransition) -> None:
        if version != sequence:
            raise StateEventConflict("aggregate version/sequence drift")
        if state != transition.current_state or version != transition.expected_version:
            raise StateEventConflict("aggregate state or version conflict")

    @staticmethod
    def _replay_or_conflict(row: Any | None, transition: AuthorizedTransition) -> StateEventResult | None:
        if row is None:
            return None
        fingerprint = str(_row_value(row, 2, "event_fingerprint"))
        idempotency_key = str(_row_value(row, 3, "idempotency_key"))
        if fingerprint != transition.event_fingerprint or idempotency_key != transition.idempotency_key:
            raise StateEventConflict("idempotency key has different immutable event facts")
        return StateEventResult(
            transition.aggregate_id,
            str(_row_value(row, 0, "to_state")),
            int(_row_value(row, 1, "resulting_version")),
            StateEventDisposition.REPLAYED,
        )

    @staticmethod
    def _payload_with_contract(transition: AuthorizedTransition) -> str:
        payload = json.loads(transition.canonical_payload_json)
        payload["contract_version"] = transition.contract_version
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _event_params(self, transition: AuthorizedTransition, *, include_economic_order: bool) -> tuple[Any, ...]:
        assert include_economic_order
        return (
            str(uuid4()), transition.aggregate_id, transition.event_seq, transition.current_state,
            transition.target_state, transition.reason_code, transition.actor.value,
            transition.evidence_hash, transition.occurred_at, transition.expected_version,
            transition.resulting_version, transition.idempotency_key, transition.event_fingerprint,
            transition.correlation_id, self._payload_with_contract(transition),
        )
