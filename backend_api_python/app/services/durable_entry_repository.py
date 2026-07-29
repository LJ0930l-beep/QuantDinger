"""Caller-owned PostgreSQL persistence for Canonical Entry V2 facts.

This repository is deliberately a narrow persistence boundary.  It does not
create legacy command, intent, or economic-order rows and it does not own the
connection transaction: the admission gateway will compose it with risk and
outbox persistence in one outer transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from app.domain.canonical_entry_v2_contracts import (
    CancelTargetSubject,
    CanonicalEntryV2Error,
    DurableEntryGraphV2,
    EconomicOrderSubject,
)
from app.domain.decimal_values import DecimalValueError, canonical_decimal_string
from app.domain.durable_entry_persistence_contracts import (
    DURABLE_ENTRY_AUTHORITATIVE_COLUMNS,
    DURABLE_ENTRY_CONTRACT_VERSION,
    DURABLE_ENTRY_SPECIFICATION_TABLE,
    DurableEntryConflict,
    DurableEntryIntegrityError,
    DurableEntryPersistDisposition,
    DurableEntryPersistResult,
    DurableEntryRepositoryError,
    durable_entry_action_rule,
)


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def _row_value(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


def _canonical_uuid(value: Any, field_name: str) -> str:
    try:
        return str(UUID(str(value))).lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise DurableEntryIntegrityError(f"persisted {field_name} must be UUID") from exc


def _canonical_decimal(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise DurableEntryIntegrityError(f"persisted {field_name} must be Decimal-compatible")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        return canonical_decimal_string(parsed)
    except (InvalidOperation, TypeError, ValueError, DecimalValueError) as exc:
        raise DurableEntryIntegrityError(f"persisted {field_name} is not canonical Decimal") from exc


def _canonical_timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DurableEntryIntegrityError(f"persisted {field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise DurableEntryIntegrityError(f"persisted {field_name} must use UTC")
    return value.astimezone(timezone.utc).isoformat()


def _enum_value(value: Any) -> str | None:
    return None if value is None else value.value


class DurableEntryRepository:
    """Persist one fully typed V2 entry specification without commit/rollback."""

    def persist_durable_entry(
        self,
        connection: Connection,
        graph: DurableEntryGraphV2,
    ) -> DurableEntryPersistResult:
        """Insert first, then lock and compare every durable fact on conflict.

        The caller retains complete transaction ownership on all paths,
        including typed conflicts and raw database failures.  PostgreSQL unique
        constraints are the final arbiter; no fingerprint alone is accepted as
        replay proof.
        """

        self._validate_graph(graph)
        try:
            cursor = connection.cursor()
        except Exception as exc:
            raise DurableEntryRepositoryError("durable entry database cursor could not be opened") from exc
        try:
            if self._insert(cursor, graph):
                return self._result(graph, DurableEntryPersistDisposition.CREATED)
            return self._load_exact_replay(cursor, graph)
        except (DurableEntryRepositoryError, CanonicalEntryV2Error):
            raise
        except Exception as exc:
            raise DurableEntryRepositoryError("durable entry database operation failed") from exc
        finally:
            try:
                cursor.close()
            except Exception as exc:
                raise DurableEntryRepositoryError("durable entry database cursor could not be closed") from exc

    def _validate_graph(self, graph: DurableEntryGraphV2) -> None:
        if not isinstance(graph, DurableEntryGraphV2):
            raise DurableEntryIntegrityError("durable persistence requires DurableEntryGraphV2")
        specification = graph.specification
        rule = durable_entry_action_rule(specification.action)
        if specification.risk_effect is not rule.risk_effect:
            raise DurableEntryIntegrityError("graph risk effect conflicts with durable action rule")
        if not isinstance(graph.subject, rule.subject_type):
            raise DurableEntryIntegrityError("graph subject conflicts with durable action rule")
        if rule.economic_order_required:
            if not isinstance(graph.subject, EconomicOrderSubject):
                raise DurableEntryIntegrityError("economic order subject is required")
        elif not isinstance(graph.subject, CancelTargetSubject):
            raise DurableEntryIntegrityError("cancel target subject is required")

    def _row_facts(self, graph: DurableEntryGraphV2) -> dict[str, Any]:
        specification = graph.specification
        intent = specification.economic_intent
        economic_order_id = (
            graph.subject.economic_order_id
            if isinstance(graph.subject, EconomicOrderSubject)
            else None
        )
        return {
            "contract_version": DURABLE_ENTRY_CONTRACT_VERSION,
            "command_id": graph.command_id,
            "tenant_id": specification.tenant_id,
            "credential_id": specification.credential_id,
            "account_scope": specification.account_scope,
            "instrument_id": specification.instrument_id,
            "market_type": specification.market_type,
            "action": specification.action.value,
            "risk_effect": specification.risk_effect.value,
            "side": _enum_value(intent.side),
            "quantity": None if intent.quantity is None else intent.quantity.to_string(),
            "quantity_semantics": _enum_value(intent.quantity_semantics),
            "execution_kind": _enum_value(intent.execution_kind),
            "limit_price": None if intent.limit_price is None else intent.limit_price.to_string(),
            "trigger_price": None if intent.trigger_price is None else intent.trigger_price.to_string(),
            "trigger_direction": _enum_value(intent.trigger_direction),
            "trigger_price_type": _enum_value(intent.trigger_price_type),
            "reduce_only": intent.reduce_only,
            "position_side": intent.position_side.value,
            "cancel_target_kind": _enum_value(intent.cancel_target_kind),
            "cancel_target_id": intent.cancel_target_id,
            "target_position_id": intent.target_position_id,
            "close_quantity": None if intent.close_quantity is None else intent.close_quantity.to_string(),
            "close_all": intent.close_all,
            "economic_order_id": economic_order_id,
            "economic_fingerprint": specification.economic_fingerprint,
            "request_fingerprint": specification.request_fingerprint,
            "actor_type": specification.actor.actor_type.value,
            "actor_id": specification.actor.actor_id,
            "source": specification.actor.entry_source.value,
            "mode": specification.mode.value,
            "idempotency_key": specification.idempotency_key,
            "correlation_id": specification.correlation_id,
            "occurred_at": specification.occurred_at,
        }

    def _insert(self, cursor: Cursor, graph: DurableEntryGraphV2) -> bool:
        facts = self._row_facts(graph)
        columns = tuple(DURABLE_ENTRY_AUTHORITATIVE_COLUMNS)
        placeholders = ", ".join("%s" for _ in columns)
        cursor.execute(
            f"""
            INSERT INTO {DURABLE_ENTRY_SPECIFICATION_TABLE} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT DO NOTHING
            RETURNING command_id
            """,
            tuple(facts[column] for column in columns),
        )
        return cursor.fetchone() is not None

    def _load_exact_replay(
        self,
        cursor: Cursor,
        graph: DurableEntryGraphV2,
    ) -> DurableEntryPersistResult:
        facts = self._row_facts(graph)
        columns = tuple(DURABLE_ENTRY_AUTHORITATIVE_COLUMNS)
        cursor.execute(
            f"""
            SELECT {", ".join(columns)}
              FROM {DURABLE_ENTRY_SPECIFICATION_TABLE}
             WHERE tenant_id = %s
               AND credential_id = %s
               AND account_scope = %s
               AND idempotency_key = %s
               AND contract_version = %s
             FOR UPDATE
            """,
            (
                facts["tenant_id"], facts["credential_id"], facts["account_scope"],
                facts["idempotency_key"], facts["contract_version"],
            ),
        )
        row = cursor.fetchone()
        if row is None:
            self._raise_unresolved_conflict(cursor, graph)
        observed = {
            column: _row_value(row, index, column)
            for index, column in enumerate(columns)
        }
        self._assert_exact_facts(facts, observed)
        return self._result(graph, DurableEntryPersistDisposition.REPLAYED)

    def _raise_unresolved_conflict(self, cursor: Cursor, graph: DurableEntryGraphV2) -> None:
        cursor.execute(
            f"""
            SELECT command_id
              FROM {DURABLE_ENTRY_SPECIFICATION_TABLE}
             WHERE command_id = %s
             FOR UPDATE
            """,
            (graph.command_id,),
        )
        if cursor.fetchone() is not None:
            raise DurableEntryConflict("command_id names a different durable entry")
        raise DurableEntryConflict("durable entry uniqueness conflict is not visible")

    def _assert_exact_facts(self, expected: dict[str, Any], observed: dict[str, Any]) -> None:
        uuid_columns = {"command_id", "economic_order_id"}
        decimal_columns = {"quantity", "limit_price", "trigger_price", "close_quantity"}
        timestamp_columns = {"occurred_at"}
        for column in DURABLE_ENTRY_AUTHORITATIVE_COLUMNS:
            actual = observed[column]
            wanted = expected[column]
            if column in uuid_columns:
                if wanted is None:
                    equal = actual is None
                else:
                    equal = actual is not None and _canonical_uuid(actual, column) == _canonical_uuid(wanted, column)
            elif column in decimal_columns:
                equal = _canonical_decimal(actual, column) == _canonical_decimal(wanted, column)
            elif column in timestamp_columns:
                equal = _canonical_timestamp(actual, column) == _canonical_timestamp(wanted, column)
            else:
                equal = actual == wanted
            if not equal:
                raise DurableEntryConflict(f"idempotency identity names different {column}")

    def _result(
        self,
        graph: DurableEntryGraphV2,
        disposition: DurableEntryPersistDisposition,
    ) -> DurableEntryPersistResult:
        specification = graph.specification
        economic_order_id = (
            graph.subject.economic_order_id
            if isinstance(graph.subject, EconomicOrderSubject)
            else None
        )
        return DurableEntryPersistResult(
            command_id=graph.command_id,
            action=specification.action,
            subject=graph.subject,
            economic_order_id=economic_order_id,
            economic_fingerprint=specification.economic_fingerprint,
            request_fingerprint=specification.request_fingerprint,
            disposition=disposition,
        )
