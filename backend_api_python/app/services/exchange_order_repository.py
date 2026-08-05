"""Caller-owned persistence for the exchange-order fact after submission ack."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any
from uuid import UUID

from app.domain.order_contracts import ExchangeOrderNormalizedState
from app.domain.order_state_machine import SubmissionAttemptScope
from app.services.gate_testnet_order_client import GateTestnetOrderReceipt
from app.domain.gate_testnet_execution_contracts import GateTestnetExecutionRequest


class ExchangeOrderPersistenceError(RuntimeError):
    """Base error for typed exchange-order persistence failures."""


class ExchangeOrderConflict(ExchangeOrderPersistenceError):
    """An exchange-order identity exists with different immutable facts."""


def _uuid(value: object, name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ExchangeOrderPersistenceError(f"{name} must be a UUID") from exc


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise ExchangeOrderPersistenceError(f"{name} must be canonical ASCII text")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ExchangeOrderPersistenceError(f"{name} must use Decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ExchangeOrderPersistenceError(f"{name} must use Decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ExchangeOrderPersistenceError(f"{name} must be positive and finite")
    return result


def _row_value(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


@dataclass(frozen=True, slots=True)
class ExchangeOrderCreateFacts:
    id: str
    attempt_id: str
    economic_order_id: str
    scope: SubmissionAttemptScope
    child_role: str
    exchange_order_id: str
    venue_client_order_id: str
    normalized_state: ExchangeOrderNormalizedState
    requested_qty: Decimal
    raw_status: str
    raw_payload_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, SubmissionAttemptScope):
            raise ExchangeOrderPersistenceError("scope must be SubmissionAttemptScope")
        for value, name in ((self.id, "id"), (self.attempt_id, "attempt_id"),
                            (self.economic_order_id, "economic_order_id")):
            object.__setattr__(self, name, _uuid(value, name))
        for value, name in ((self.child_role, "child_role"), (self.exchange_order_id, "exchange_order_id"),
                            (self.venue_client_order_id, "venue_client_order_id"),
                            (self.raw_status, "raw_status"), (self.raw_payload_hash, "raw_payload_hash")):
            _text(value, name)
        if not isinstance(self.normalized_state, ExchangeOrderNormalizedState):
            raise ExchangeOrderPersistenceError("normalized_state must be typed")
        object.__setattr__(self, "requested_qty", _decimal(self.requested_qty, "requested_qty"))
        if self.economic_order_id != self.scope.economic_order_id:
            raise ExchangeOrderPersistenceError("economic-order scope mismatch")


def normalized_gate_state(raw_state: str) -> ExchangeOrderNormalizedState:
    """Map only documented terminal/open Gate states; unknown is fail closed."""

    value = _text(raw_state, "raw_status").lower()
    mapping = {
        "open": ExchangeOrderNormalizedState.SUBMITTED,
        "new": ExchangeOrderNormalizedState.SUBMITTED,
        "active": ExchangeOrderNormalizedState.SUBMITTED,
        "partial": ExchangeOrderNormalizedState.PARTIALLY_FILLED,
        "partially_filled": ExchangeOrderNormalizedState.PARTIALLY_FILLED,
        "closed": ExchangeOrderNormalizedState.FILLED,
        "finished": ExchangeOrderNormalizedState.FILLED,
        "filled": ExchangeOrderNormalizedState.FILLED,
        "cancelled": ExchangeOrderNormalizedState.CANCELLED,
        "canceled": ExchangeOrderNormalizedState.CANCELLED,
        "expired": ExchangeOrderNormalizedState.CANCELLED,
        "rejected": ExchangeOrderNormalizedState.REJECTED,
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise ExchangeOrderPersistenceError("unknown Gate order state cannot be normalized") from exc


def facts_from_gate_receipt(
    receipt: GateTestnetOrderReceipt,
    request: GateTestnetExecutionRequest,
    *,
    scope: SubmissionAttemptScope,
    attempt_id: str,
    attempt_row_id: str,
    child_role: str = "PRIMARY",
) -> ExchangeOrderCreateFacts:
    if not isinstance(receipt, GateTestnetOrderReceipt) or not isinstance(request, GateTestnetExecutionRequest):
        raise ExchangeOrderPersistenceError("typed Gate receipt and request are required")
    if receipt.market_type.value != scope.market_type or receipt.account_scope != scope.account_scope or receipt.instrument_id != scope.instrument_id:
        raise ExchangeOrderPersistenceError("Gate receipt scope mismatch")
    payload = {"exchange_order_id": receipt.exchange_order_id, "client_order_id": receipt.client_order_id,
               "raw_state": receipt.raw_state, "status_code": receipt.status_code,
               "response_fingerprint": receipt.response_fingerprint}
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ExchangeOrderCreateFacts(
        id=attempt_row_id,
        attempt_id=attempt_id,
        economic_order_id=scope.economic_order_id,
        scope=scope,
        child_role=child_role,
        exchange_order_id=receipt.exchange_order_id,
        venue_client_order_id=receipt.client_order_id,
        normalized_state=normalized_gate_state(receipt.raw_state),
        requested_qty=request.quantity,
        raw_status=receipt.raw_state,
        raw_payload_hash=payload_hash,
    )


class ExchangeOrderRepository:
    def persist_caller_owned(self, connection: Any, facts: ExchangeOrderCreateFacts):
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO qd_exchange_orders (
                    id, attempt_id, economic_order_id, child_role, exchange, tenant_id, credential_id,
                    market_type, account_scope, instrument_id, exchange_order_id, venue_client_order_id,
                    raw_status, normalized_state, requested_qty, raw_payload_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING RETURNING id
                """,
                (facts.id, facts.attempt_id, facts.economic_order_id, facts.child_role, facts.scope.exchange,
                 facts.scope.tenant_id, facts.scope.credential_id, facts.scope.market_type,
                 facts.scope.account_scope, facts.scope.instrument_id, facts.exchange_order_id,
                 facts.venue_client_order_id, facts.raw_status, facts.normalized_state.value,
                 facts.requested_qty, facts.raw_payload_hash),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return str(_row_value(inserted, 0, "id")), "APPLIED"
            cursor.execute(
                """SELECT id,attempt_id,economic_order_id,child_role,exchange,tenant_id,credential_id,
                          market_type,account_scope,instrument_id,exchange_order_id,venue_client_order_id,
                          raw_status,normalized_state,requested_qty,raw_payload_hash
                     FROM qd_exchange_orders
                    WHERE id=%s OR attempt_id=%s OR (exchange=%s AND credential_id=%s AND market_type=%s AND venue_client_order_id=%s)
                    ORDER BY id FOR UPDATE""",
                (facts.id, facts.attempt_id, facts.scope.exchange, facts.scope.credential_id,
                 facts.scope.market_type, facts.venue_client_order_id),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise ExchangeOrderConflict("exchange-order uniqueness conflict is not singular")
            row = rows[0]
            actual = tuple(_row_value(row, i, key) for i, key in enumerate((
                "id", "attempt_id", "economic_order_id", "child_role", "exchange", "tenant_id", "credential_id",
                "market_type", "account_scope", "instrument_id", "exchange_order_id", "venue_client_order_id",
                "raw_status", "normalized_state", "requested_qty", "raw_payload_hash")))
            actual = list(actual)
            for i in (0, 1, 2):
                actual[i] = str(actual[i])
            actual[14] = _decimal(actual[14], "requested_qty")
            expected = (facts.id, facts.attempt_id, facts.economic_order_id, facts.child_role, facts.scope.exchange,
                        facts.scope.tenant_id, facts.scope.credential_id, facts.scope.market_type,
                        facts.scope.account_scope, facts.scope.instrument_id, facts.exchange_order_id,
                        facts.venue_client_order_id, facts.raw_status, facts.normalized_state.value,
                        facts.requested_qty, facts.raw_payload_hash)
            if tuple(actual) != expected:
                raise ExchangeOrderConflict("exchange-order identity has different immutable facts")
            return facts.id, "REPLAYED"
        finally:
            cursor.close()

    def persist(self, connection: Any, facts: ExchangeOrderCreateFacts):
        try:
            result = self.persist_caller_owned(connection, facts)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


__all__ = [
    "ExchangeOrderConflict", "ExchangeOrderCreateFacts", "ExchangeOrderPersistenceError",
    "ExchangeOrderRepository", "facts_from_gate_receipt", "normalized_gate_state",
]
