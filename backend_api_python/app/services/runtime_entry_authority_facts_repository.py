"""Caller-owned persistence for Runtime Entry authority projection facts.

The repository persists already-typed projection rows into the immutable
authority schema without committing, rolling back, contacting a venue, or
fabricating anything.  Every insert is idempotent: ``ON CONFLICT DO NOTHING``
followed by an exact-replay comparison when the row already exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from app.domain.runtime_entry_authority_projection_contracts import PROJECTION_CONTRACT_VERSION


class RuntimeEntryAuthorityFactsError(RuntimeError):
    """Typed failure at the projection-facts persistence boundary."""


class RuntimeEntryAuthorityFactsConflict(RuntimeEntryAuthorityFactsError):
    """A persisted identity names different immutable facts."""


class RuntimeEntryAuthorityFactsDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True)
class RuntimeEntryAuthorityFactsResult:
    id: str
    disposition: RuntimeEntryAuthorityFactsDisposition


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def _value(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


def _uuid(value: Any, field_name: str) -> str:
    try:
        return str(UUID(str(value))).lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeEntryAuthorityFactsConflict(f"persisted {field_name} must be UUID") from exc


def _timestamp(value: Any, field_name: str) -> str:
    from datetime import datetime, timezone

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeEntryAuthorityFactsConflict(f"persisted {field_name} must be UTC")
    return value.astimezone(timezone.utc).isoformat()


def _decimal_text(value: Any, field_name: str) -> str:
    from decimal import Decimal

    try:
        return format(Decimal(str(value)).normalize(), "f")
    except Exception as exc:
        raise RuntimeEntryAuthorityFactsConflict(f"persisted {field_name} must be numeric") from exc


class RuntimeEntryAuthorityFactsRepository:
    """Persist projected authority rows; never owns a transaction."""

    def persist_scope_binding(self, connection: Connection, facts: dict[str, Any]) -> RuntimeEntryAuthorityFactsResult:
        return self._upsert(
            connection,
            table="qd_runtime_entry_scope_bindings",
            facts=facts,
            conflict="ON CONFLICT (tenant_id, credential_id) DO NOTHING",
            replay_columns=("tenant_id", "credential_id", "account_scope", "exchange_id",
                            "source_identity", "source_version", "source_fingerprint", "contract_version"),
            replay_where="tenant_id = %s AND credential_id = %s",
            replay_params=(facts["tenant_id"], facts["credential_id"]),
            uuid_columns=("id",),
            replay_ignore=("source_fingerprint", "source_version"),
        )

    def persist_instrument_rule_snapshot(self, connection: Connection, facts: dict[str, Any]) -> RuntimeEntryAuthorityFactsResult:
        return self._upsert(
            connection,
            table="qd_instrument_rule_snapshots",
            facts=facts,
            conflict="ON CONFLICT (exchange, market_type, instrument_id, rule_version) DO NOTHING",
            replay_columns=("exchange", "market_type", "instrument_id", "rule_version",
                            "tick_size", "quantity_step", "minimum_quantity", "minimum_notional",
                            "price_scale", "quantity_scale", "rounding_policy_version"),
            replay_where="exchange = %s AND market_type = %s AND instrument_id = %s AND rule_version = %s",
            replay_params=(facts["exchange"], facts["market_type"], facts["instrument_id"], facts["rule_version"]),
            uuid_columns=("id",),
        )

    def persist_instrument_authority(self, connection: Connection, facts: dict[str, Any]) -> RuntimeEntryAuthorityFactsResult:
        return self._upsert(
            connection,
            table="qd_runtime_entry_instrument_authorities",
            facts=facts,
            conflict="ON CONFLICT (scope_binding_id, instrument_id, market_type) DO NOTHING",
            replay_columns=("contract_version", "scope_binding_id", "tenant_id", "credential_id",
                            "account_scope", "exchange_id", "instrument_id", "market_type",
                            "instrument_rule_snapshot_id", "source_identity", "source_version", "source_fingerprint"),
            replay_where="scope_binding_id = %s AND instrument_id = %s AND market_type = %s",
            replay_params=(facts["scope_binding_id"], facts["instrument_id"], facts["market_type"]),
            uuid_columns=("id", "scope_binding_id", "instrument_rule_snapshot_id"),
            replay_ignore=("source_fingerprint", "source_version"),
        )

    def persist_position_projection(self, connection: Connection, facts: dict[str, Any]) -> RuntimeEntryAuthorityFactsResult:
        return self._upsert(
            connection,
            table="qd_position_projections",
            facts=facts,
            conflict=("ON CONFLICT (tenant_id, credential_id, account_scope, instrument_id, side, projection_version) "
                      "WHERE strategy_id IS NULL DO NOTHING"),
            replay_columns=("tenant_id", "credential_id", "account_scope", "instrument_id", "side",
                            "quantity", "average_cost", "realized_pnl", "last_event_seq",
                            "projection_version", "policy_version"),
            replay_where=("tenant_id = %s AND credential_id = %s AND account_scope = %s "
                          "AND instrument_id = %s AND side = %s AND projection_version = %s"),
            replay_params=(facts["tenant_id"], facts["credential_id"], facts["account_scope"],
                           facts["instrument_id"], facts["side"], facts["projection_version"]),
            uuid_columns=("id",),
            nullable_columns=("strategy_id", "average_cost"),
        )

    def persist_position_subject(self, connection: Connection, facts: dict[str, Any]) -> RuntimeEntryAuthorityFactsResult:
        return self._upsert(
            connection,
            table="qd_runtime_entry_position_subjects",
            facts=facts,
            conflict=("ON CONFLICT (instrument_authority_id, position_side, position_projection_id, "
                      "reconciliation_checkpoint_id) DO NOTHING"),
            replay_columns=("contract_version", "scope_binding_id", "instrument_authority_id",
                            "reconciliation_checkpoint_id", "position_projection_id",
                            "tenant_id", "credential_id", "account_scope", "exchange_id",
                            "instrument_id", "market_type", "position_side"),
            replay_where=("instrument_authority_id = %s AND position_side = %s "
                          "AND position_projection_id = %s AND reconciliation_checkpoint_id = %s"),
            replay_params=(facts["instrument_authority_id"], facts["position_side"],
                           facts["position_projection_id"], facts["reconciliation_checkpoint_id"]),
            uuid_columns=("id", "scope_binding_id", "instrument_authority_id",
                          "reconciliation_checkpoint_id", "position_projection_id"),
        )

    def _upsert(
        self,
        connection: Connection,
        *,
        table: str,
        facts: dict[str, Any],
        conflict: str,
        replay_columns: tuple[str, ...],
        replay_where: str,
        replay_params: tuple[Any, ...],
        uuid_columns: tuple[str, ...],
        nullable_columns: tuple[str, ...] = (),
        replay_ignore: tuple[str, ...] = (),
    ) -> RuntimeEntryAuthorityFactsResult:
        import json

        cursor = connection.cursor()
        try:
            columns = tuple(facts)
            values = tuple(
                json.dumps(facts[column], ensure_ascii=False, sort_keys=True) if isinstance(facts[column], dict) else facts[column]
                for column in columns
            )
            cursor.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('%s' for _ in columns)}) {conflict} RETURNING id",
                values,
            )
            row = cursor.fetchone()
            if row is not None:
                return RuntimeEntryAuthorityFactsResult(_uuid(_value(row, 0, "id"), "id"), RuntimeEntryAuthorityFactsDisposition.CREATED)
            return RuntimeEntryAuthorityFactsResult(_uuid(facts["id"], "id"), self._assert_exact_replay(
                cursor, table, facts, replay_columns, replay_where, replay_params, uuid_columns, nullable_columns,
                replay_ignore=replay_ignore,
            ))
        finally:
            cursor.close()

    def _assert_exact_replay(
        self,
        cursor: Cursor,
        table: str,
        expected: dict[str, Any],
        replay_columns: tuple[str, ...],
        replay_where: str,
        replay_params: tuple[Any, ...],
        uuid_columns: tuple[str, ...],
        nullable_columns: tuple[str, ...],
        replay_ignore: tuple[str, ...] = (),
    ) -> RuntimeEntryAuthorityFactsDisposition:
        cursor.execute(
            f"SELECT {', '.join(replay_columns)} FROM {table} WHERE {replay_where} FOR UPDATE",
            replay_params,
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(f"SELECT id FROM {table} WHERE id = %s FOR UPDATE", (expected["id"],))
            if cursor.fetchone() is not None:
                raise RuntimeEntryAuthorityFactsConflict(f"{table} id names different facts")
            raise RuntimeEntryAuthorityFactsConflict(f"{table} uniqueness conflict is not visible")
        for index, column in enumerate(replay_columns):
            if column in replay_ignore:
                continue
            actual = _value(row, index, column)
            wanted = expected[column]
            if column in uuid_columns:
                equal = actual is None and wanted is None or actual is not None and wanted is not None and _uuid(actual, column) == _uuid(wanted, column)
            elif column in nullable_columns:
                equal = actual is None and (wanted is None or str(wanted) == "") or actual is not None and wanted is not None and _decimal_text(actual, column) == _decimal_text(wanted, column)
            elif column in {"tick_size", "quantity_step", "minimum_quantity", "minimum_notional",
                            "quantity", "average_cost", "realized_pnl", "last_event_seq"}:
                equal = _decimal_text(actual, column) == _decimal_text(wanted, column)
            else:
                equal = actual == wanted
            if not equal:
                raise RuntimeEntryAuthorityFactsConflict(f"{table} identity names different {column}")
        return RuntimeEntryAuthorityFactsDisposition.REPLAYED


__all__ = [
    "PROJECTION_CONTRACT_VERSION",
    "RuntimeEntryAuthorityFactsRepository",
    "RuntimeEntryAuthorityFactsResult",
    "RuntimeEntryAuthorityFactsDisposition",
    "RuntimeEntryAuthorityFactsError",
    "RuntimeEntryAuthorityFactsConflict",
]
