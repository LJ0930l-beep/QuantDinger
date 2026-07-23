"""PostgreSQL persistence skeleton for PR-03 durable command facts.

The repository receives an already-open DB-API connection from its caller.  It
does not import Flask, exchange clients, workers, or runtime configuration.
Every mutation is contained in an explicit database transaction; PostgreSQL
unique constraints remain the final concurrency arbiter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Protocol

from app.domain.command_intent_contracts import (
    CommandGraph,
    CommandGraphDisposition,
    CommandGraphResult,
    CommandIntentContractError,
    IdempotencyConflict,
    InstrumentRuleSnapshotMismatch,
    ReservationConflict,
    ReservationState,
    ReservationStateConflict,
    ReservationTransitionDisposition,
    ReservationTransitionResult,
    RiskReservation,
    _aware_utc,
    canonical_json,
    canonical_uuid,
)
from app.domain.decimal_values import DecimalValueError, canonical_decimal_string
from app.domain.order_contracts import EconomicOrderState


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


def _row_value(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


def _json_parameter(canonical_json: str) -> str:
    # Passing canonical text and casting at the SQL boundary avoids a driver-
    # specific JSON adapter and preserves the exact hash material separately.
    return canonical_json


def _canonical_db_decimal(value: Any) -> str:
    """Normalize PostgreSQL NUMERIC facts before immutable replay comparison."""

    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        return canonical_decimal_string(parsed)
    except (InvalidOperation, ValueError, TypeError, DecimalValueError) as exc:
        raise CommandIntentContractError("persisted decimal fact is not canonical") from exc


def _canonical_db_json(value: Any) -> str:
    """Normalize driver-dependent JSONB values without accepting new facts."""

    try:
        decoded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(decoded, dict):
            raise CommandIntentContractError("persisted JSON fact must be an object")
        return canonical_json(decoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CommandIntentContractError("persisted JSON fact is not canonical") from exc


def _canonical_db_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CommandIntentContractError("persisted timestamp fact is not timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


class CommandIntentRepository:
    """Atomic command graph and reservation persistence over PR-02 tables."""

    def accept_command_graph(self, connection: Connection, graph: CommandGraph) -> CommandGraphResult:
        """Atomically insert command, v1 intent, and CREATED economic order.

        A duplicate idempotency key only replays if all safety-relevant command
        identity facts and canonical fingerprint match.  It never regenerates
        identifiers and never treats a generic database error as a replay.
        """

        cursor = connection.cursor()
        try:
            self._validate_snapshot(cursor, graph)
            inserted = self._insert_command(cursor, graph)
            if not inserted:
                result = self._load_matching_replay(cursor, graph)
                connection.commit()
                return result
            self._insert_intent(cursor, graph)
            self._insert_economic_order(cursor, graph)
            connection.commit()
            return CommandGraphResult(
                command_id=graph.command.command_id,
                intent_id=graph.intent.intent_id,
                economic_order_id=graph.intent.economic_order_id,
                state=EconomicOrderState.CREATED,
                disposition=CommandGraphDisposition.CREATED,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _validate_snapshot(self, cursor: Cursor, graph: CommandGraph) -> None:
        intent = graph.intent
        cursor.execute(
            """
            SELECT id FROM qd_instrument_rule_snapshots
            WHERE id = %s AND exchange = %s AND market_type = %s
              AND instrument_id = %s AND rule_version = %s
            """,
            (intent.instrument_rule_snapshot_id, intent.exchange_id, intent.market_type,
             intent.instrument_id, intent.instrument_rule_version),
        )
        if cursor.fetchone() is None:
            raise InstrumentRuleSnapshotMismatch(
                "instrument rule snapshot does not match exchange, market, instrument, and version"
            )

    def _insert_command(self, cursor: Cursor, graph: CommandGraph) -> bool:
        command = graph.command
        cursor.execute(
            """
            INSERT INTO qd_order_commands (
                id, tenant_id, user_id, credential_id, actor_type, actor_id,
                source, action, account_scope, strategy_id, request_json,
                request_fingerprint, idempotency_key, status, correlation_id,
                accepted_at
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'ACCEPTED',%s,NOW()
            ) ON CONFLICT (tenant_id, source, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (command.command_id, command.tenant_id, command.user_id, command.credential_id,
             command.actor_type.value, command.actor_id, command.source, command.action.value,
             command.account_scope, command.strategy_id, _json_parameter(command.canonical_request_json),
             command.request_fingerprint, command.idempotency_key, command.correlation_id),
        )
        return cursor.fetchone() is not None

    def _load_matching_replay(self, cursor: Cursor, graph: CommandGraph) -> CommandGraphResult:
        command = graph.command
        cursor.execute(
            """
            SELECT id, user_id, credential_id, actor_type, actor_id, action,
                   account_scope, request_fingerprint
              FROM qd_order_commands
             WHERE tenant_id = %s AND source = %s AND idempotency_key = %s
             FOR UPDATE
            """,
            (command.tenant_id, command.source, command.idempotency_key),
        )
        existing = cursor.fetchone()
        if existing is None:
            # A concurrent transaction can only reach here if the connection's
            # isolation mode is not PostgreSQL READ COMMITTED.  Failing closed
            # is safer than issuing a second command under uncertainty.
            raise IdempotencyConflict("existing idempotency command is not visible")
        expected = (command.command_id, command.user_id, command.credential_id,
                    command.actor_type.value, command.actor_id, command.action.value,
                    command.account_scope, command.request_fingerprint)
        observed = list(_row_value(existing, index, key) for index, key in enumerate(
            ("id", "user_id", "credential_id", "actor_type", "actor_id", "action", "account_scope", "request_fingerprint")
        ))
        observed[0] = str(observed[0])
        if tuple(observed) != expected:
            raise IdempotencyConflict("idempotency key names a different command")
        cursor.execute(
            """
            SELECT i.id, i.economic_order_id, i.tenant_id, i.credential_id,
                   i.account_scope, i.instrument_id, i.market_type, i.side,
                   i.target_quantity, i.limit_price, i.quote_notional,
                   i.instrument_rule_snapshot_id, i.instrument_rule_version,
                   i.rounding_mode, i.payload_hash, e.state
              FROM qd_order_intents_v2 AS i
              JOIN qd_economic_orders AS e ON e.intent_id = i.id
             WHERE i.command_id = %s AND i.intent_version = 1
            """,
            (command.command_id,),
        )
        linked = cursor.fetchone()
        if linked is None:
            raise IdempotencyConflict("existing command has no complete immutable graph")
        intent = graph.intent
        expected_intent = (
            intent.intent_id, intent.economic_order_id, intent.tenant_id, intent.credential_id,
            intent.account_scope, intent.instrument_id, intent.market_type, intent.side,
            intent.target_quantity.to_string(), None if intent.limit_price is None else intent.limit_price.to_string(),
            None if intent.quote_notional is None else intent.quote_notional.to_string(),
            intent.instrument_rule_snapshot_id, intent.instrument_rule_version, intent.rounding_mode,
            intent.payload_hash,
        )
        observed_intent = list(_row_value(linked, index, key) for index, key in enumerate(
            ("id", "economic_order_id", "tenant_id", "credential_id", "account_scope", "instrument_id",
             "market_type", "side", "target_quantity", "limit_price", "quote_notional",
             "instrument_rule_snapshot_id", "instrument_rule_version", "rounding_mode", "payload_hash")
        ))
        for index in (0, 1, 11):
            observed_intent[index] = str(observed_intent[index])
        for index in (8, 9, 10):
            if observed_intent[index] is not None:
                observed_intent[index] = _canonical_db_decimal(observed_intent[index])
        if tuple(observed_intent) != expected_intent:
            raise IdempotencyConflict("idempotency key names a different immutable intent")
        return CommandGraphResult(
            command_id=command.command_id,
            intent_id=str(_row_value(linked, 0, "id")),
            economic_order_id=str(_row_value(linked, 1, "economic_order_id")),
            state=EconomicOrderState(str(_row_value(linked, 15, "state"))),
            disposition=CommandGraphDisposition.REPLAYED,
        )

    def _insert_intent(self, cursor: Cursor, graph: CommandGraph) -> None:
        intent = graph.intent
        cursor.execute(
            """
            INSERT INTO qd_order_intents_v2 (
                id, command_id, tenant_id, credential_id, economic_order_id,
                intent_version, account_scope, instrument_id, market_type, side,
                position_side, reduce_only, order_type, execution_algo,
                time_in_force, target_quantity, limit_price, quote_notional,
                instrument_rule_snapshot_id, instrument_rule_version,
                rounding_mode, payload_hash
            ) VALUES (
                %s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (intent.intent_id, intent.command_id, intent.tenant_id, intent.credential_id,
             intent.economic_order_id, intent.account_scope, intent.instrument_id,
             intent.market_type, intent.side, intent.position_side, intent.reduce_only,
             intent.order_type, intent.execution_algo, intent.time_in_force,
             intent.target_quantity.to_string(), None if intent.limit_price is None else intent.limit_price.to_string(),
             None if intent.quote_notional is None else intent.quote_notional.to_string(),
             intent.instrument_rule_snapshot_id, intent.instrument_rule_version,
             intent.rounding_mode, intent.payload_hash),
        )

    def _insert_economic_order(self, cursor: Cursor, graph: CommandGraph) -> None:
        command, intent = graph.command, graph.intent
        cursor.execute(
            """
            INSERT INTO qd_economic_orders (
                id, intent_id, tenant_id, user_id, credential_id, account_scope,
                instrument_id, market_type, state, target_quantity
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'CREATED',%s)
            """,
            (intent.economic_order_id, intent.intent_id, intent.tenant_id, command.user_id,
             intent.credential_id, intent.account_scope, intent.instrument_id,
             intent.market_type, intent.target_quantity.to_string()),
        )

    def create_reservation(self, connection: Connection, reservation: RiskReservation) -> ReservationTransitionResult:
        cursor = connection.cursor()
        try:
            self._validate_reservation_scope(cursor, reservation)
            existing = self._reservation_row(cursor, reservation)
            if existing is not None:
                result = self._load_matching_reservation(cursor, reservation, existing)
                connection.commit()
                return result
            cursor.execute(
                """
                INSERT INTO qd_risk_reservations (
                    id, command_id, economic_order_id, tenant_id, credential_id,
                    account_scope, reservation_kind, currency, reserved_notional,
                    reserved_margin, reserved_position_qty, limits_snapshot_json,
                    risk_input_hash, state, expires_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'ACTIVE',%s)
                ON CONFLICT (command_id, reservation_kind) WHERE state = 'ACTIVE'
                DO NOTHING RETURNING id, version
                """,
                (reservation.reservation_id, reservation.command_id, reservation.economic_order_id,
                 reservation.tenant_id, reservation.credential_id, reservation.account_scope,
                 reservation.reservation_kind, reservation.currency, reservation.reserved_notional.to_string(),
                 reservation.reserved_margin.to_string(), reservation.reserved_position_qty.to_string(),
                 _json_parameter(reservation.canonical_limits_json), reservation.risk_input_hash,
                 reservation.expires_at),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                connection.commit()
                return ReservationTransitionResult(reservation.reservation_id, ReservationState.ACTIVE, 0,
                                                   ReservationTransitionDisposition.APPLIED)
            result = self._load_matching_reservation(cursor, reservation)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _validate_reservation_scope(self, cursor: Cursor, reservation: RiskReservation) -> None:
        cursor.execute(
            """
            SELECT c.id FROM qd_order_commands AS c
            JOIN qd_order_intents_v2 AS i ON i.command_id = c.id
            JOIN qd_economic_orders AS e ON e.id = %s AND e.intent_id = i.id
            WHERE c.id = %s AND c.tenant_id = %s AND c.credential_id = %s
              AND c.account_scope = %s AND e.tenant_id = %s AND e.credential_id = %s
              AND e.account_scope = %s
            """,
            (reservation.economic_order_id, reservation.command_id, reservation.tenant_id,
             reservation.credential_id, reservation.account_scope, reservation.tenant_id,
             reservation.credential_id, reservation.account_scope),
        )
        if cursor.fetchone() is None:
            raise ReservationConflict("reservation command, order, and scope do not match")

    def _reservation_row(self, cursor: Cursor, reservation: RiskReservation) -> Any:
        cursor.execute(
            """
            SELECT id, economic_order_id, tenant_id, credential_id, account_scope,
                   currency, reserved_notional, reserved_margin, reserved_position_qty,
                   limits_snapshot_json, risk_input_hash, state, expires_at, version
              FROM qd_risk_reservations
             WHERE command_id = %s AND reservation_kind = %s
             ORDER BY created_at DESC LIMIT 1 FOR UPDATE
            """,
            (reservation.command_id, reservation.reservation_kind),
        )
        return cursor.fetchone()

    def _load_matching_reservation(self, cursor: Cursor, reservation: RiskReservation, row: Any | None = None) -> ReservationTransitionResult:
        if row is None:
            row = self._reservation_row(cursor, reservation)
        if row is None:
            raise ReservationConflict("active reservation conflict is not visible")
        expected = (
            reservation.reservation_id, reservation.economic_order_id, reservation.tenant_id,
            reservation.credential_id, reservation.account_scope, reservation.currency,
            reservation.reserved_notional.to_string(), reservation.reserved_margin.to_string(),
            reservation.reserved_position_qty.to_string(), reservation.canonical_limits_json,
            reservation.risk_input_hash,
            None if reservation.expires_at is None else reservation.expires_at.isoformat(),
        )
        observed = list(_row_value(row, index, key) for index, key in enumerate(
            ("id", "economic_order_id", "tenant_id", "credential_id", "account_scope", "currency",
             "reserved_notional", "reserved_margin", "reserved_position_qty", "limits_snapshot_json", "risk_input_hash")
        ))
        observed.append(_row_value(row, 12, "expires_at"))
        normalized_observed = observed
        normalized_observed[0] = str(normalized_observed[0])
        normalized_observed[1] = str(normalized_observed[1])
        for index in (6, 7, 8):
            normalized_observed[index] = _canonical_db_decimal(normalized_observed[index])
        normalized_observed[9] = _canonical_db_json(normalized_observed[9])
        normalized_observed[11] = _canonical_db_timestamp(normalized_observed[11])
        if tuple(normalized_observed) != expected:
            raise ReservationConflict("reservation identity is reused with different immutable facts")
        return ReservationTransitionResult(
            reservation_id=reservation.reservation_id,
            state=ReservationState(str(_row_value(row, 11, "state"))),
            version=int(_row_value(row, 13, "version")),
            disposition=ReservationTransitionDisposition.IDEMPOTENT_REPLAY,
        )

    def consume_reservation(self, connection: Connection, reservation_id: str, expected_version: int) -> ReservationTransitionResult:
        return self._transition_reservation(connection, reservation_id, expected_version, ReservationState.CONSUMED)

    def release_reservation(self, connection: Connection, reservation_id: str, expected_version: int) -> ReservationTransitionResult:
        return self._transition_reservation(connection, reservation_id, expected_version, ReservationState.RELEASED)

    def expire_reservation(self, connection: Connection, reservation_id: str, expected_version: int, now_utc: Any) -> ReservationTransitionResult:
        normalized_now = _aware_utc(now_utc, "now_utc")
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT expires_at FROM qd_risk_reservations WHERE id = %s FOR UPDATE", (canonical_uuid(reservation_id, "reservation_id"),))
            row = cursor.fetchone()
            if row is None:
                raise ReservationStateConflict("reservation does not exist")
            expires_at = _row_value(row, 0, "expires_at")
            if expires_at is None or normalized_now <= expires_at:
                raise ReservationStateConflict("reservation is not expired at caller supplied UTC time")
            connection.rollback()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
        return self._transition_reservation(connection, reservation_id, expected_version, ReservationState.EXPIRED)

    def _transition_reservation(self, connection: Connection, reservation_id: str, expected_version: int, target: ReservationState) -> ReservationTransitionResult:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ReservationStateConflict("expected_version must be a non-negative integer")
        reservation_id = canonical_uuid(reservation_id, "reservation_id")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE qd_risk_reservations
                   SET state = %s, version = version + 1, updated_at = NOW()
                 WHERE id = %s AND state = 'ACTIVE' AND version = %s
                RETURNING version
                """,
                (target.value, reservation_id, expected_version),
            )
            changed = cursor.fetchone()
            if changed is not None:
                version = int(_row_value(changed, 0, "version"))
                connection.commit()
                return ReservationTransitionResult(reservation_id, target, version,
                                                   ReservationTransitionDisposition.APPLIED)
            cursor.execute("SELECT state, version FROM qd_risk_reservations WHERE id = %s FOR UPDATE", (reservation_id,))
            existing = cursor.fetchone()
            if existing is None:
                raise ReservationStateConflict("reservation does not exist")
            current_state = ReservationState(str(_row_value(existing, 0, "state")))
            current_version = int(_row_value(existing, 1, "version"))
            if current_state is target:
                connection.commit()
                return ReservationTransitionResult(reservation_id, target, current_version,
                                                   ReservationTransitionDisposition.IDEMPOTENT_REPLAY)
            raise ReservationStateConflict("reservation is terminal or CAS version is stale")
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
