"""Caller-owned PostgreSQL authority resolution for Runtime Entry V1.

Only persisted server-side records can produce scope, instrument, and position
authority.  The repository has no route, exchange, executor, client, commit,
or rollback behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2
from app.domain.order_contracts import OrderAction
from app.domain.runtime_entry_authority_persistence_contracts import (
    RUNTIME_ENTRY_AUTHORITY_CONTRACT_VERSION,
    RUNTIME_ENTRY_INGRESS_TABLE,
    ResolvedRuntimeEntryAuthority,
    RuntimeEntryAuthorityConflict,
    RuntimeEntryAuthorityReferences,
    RuntimeEntryAuthorityRepositoryError,
    RuntimeEntryAuthorityUnavailable,
    RuntimeEntryIngressPersistDisposition,
    RuntimeEntryIngressPersistResult,
)
from app.domain.runtime_entry_ingress_contracts import RuntimeEntryIngressV1, RuntimeIngressPrincipal
from app.domain.runtime_entry_resolution_contracts import (
    CredentialOwnership,
    InstrumentAuthority,
    PositionSubjectAuthority,
    RuntimeEntryResolutionError,
    resolve_runtime_entry_facts,
)


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
        raise RuntimeEntryAuthorityConflict(f"persisted {field_name} must be UUID") from exc


def _timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeEntryAuthorityConflict(f"persisted {field_name} must be UTC")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise RuntimeEntryAuthorityConflict(f"persisted {field_name} must be UTC")
    return value.astimezone(timezone.utc).isoformat()


class RuntimeEntryAuthorityRepository:
    """Resolve and persist Runtime Entry authority without owning a transaction."""

    def resolve(
        self,
        connection: Connection,
        ingress: RuntimeEntryIngressV1,
        principal: RuntimeIngressPrincipal,
    ) -> ResolvedRuntimeEntryAuthority:
        if not isinstance(ingress, RuntimeEntryIngressV1) or not isinstance(principal, RuntimeIngressPrincipal):
            raise RuntimeEntryAuthorityRepositoryError("authority resolution requires typed ingress and principal")
        cursor = self._cursor(connection)
        try:
            binding = self._load_binding(cursor, ingress, principal)
            instrument = self._load_instrument(cursor, binding, ingress)
            position = self._load_position(cursor, binding, instrument, ingress)
            try:
                facts = resolve_runtime_entry_facts(
                    ingress, principal, binding["credential"], instrument["authority"], position["authority"] if position else None,
                )
            except RuntimeEntryResolutionError as exc:
                raise RuntimeEntryAuthorityUnavailable("persisted authority facts do not resolve ingress") from exc
            return ResolvedRuntimeEntryAuthority(
                facts=facts,
                references=RuntimeEntryAuthorityReferences(
                    scope_binding_id=binding["id"],
                    instrument_authority_id=instrument["id"],
                    position_subject_id=None if position is None else position["id"],
                ),
            )
        except RuntimeEntryAuthorityRepositoryError:
            raise
        except Exception as exc:
            raise RuntimeEntryAuthorityRepositoryError("runtime entry authority database operation failed") from exc
        finally:
            self._close(cursor)

    def persist_ingress(
        self,
        connection: Connection,
        graph: DurableEntryGraphV2,
        authority: ResolvedRuntimeEntryAuthority,
    ) -> RuntimeEntryIngressPersistResult:
        self._validate_graph_authority(graph, authority)
        cursor = self._cursor(connection)
        try:
            facts = self._ingress_facts(graph, authority)
            if self._insert(cursor, facts):
                return RuntimeEntryIngressPersistResult(graph.command_id, RuntimeEntryIngressPersistDisposition.CREATED, authority)
            self._assert_exact_replay(cursor, facts)
            return RuntimeEntryIngressPersistResult(graph.command_id, RuntimeEntryIngressPersistDisposition.REPLAYED, authority)
        except RuntimeEntryAuthorityRepositoryError:
            raise
        except Exception as exc:
            raise RuntimeEntryAuthorityRepositoryError("runtime entry ingress database operation failed") from exc
        finally:
            self._close(cursor)

    def _cursor(self, connection: Connection) -> Cursor:
        try:
            return connection.cursor()
        except Exception as exc:
            raise RuntimeEntryAuthorityRepositoryError("runtime entry authority cursor could not be opened") from exc

    def _close(self, cursor: Cursor) -> None:
        try:
            cursor.close()
        except Exception as exc:
            raise RuntimeEntryAuthorityRepositoryError("runtime entry authority cursor could not be closed") from exc

    def _load_binding(self, cursor: Cursor, ingress: RuntimeEntryIngressV1, principal: RuntimeIngressPrincipal) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT b.id, b.tenant_id, b.credential_id, b.account_scope, b.exchange_id
              FROM qd_runtime_entry_scope_bindings b
              JOIN qd_exchange_credentials c ON c.id = b.credential_id
             WHERE b.tenant_id = %s
               AND b.credential_id = %s
               AND c.user_id = b.tenant_id
               AND LOWER(c.exchange_id) = b.exchange_id
             FOR KEY SHARE
            """,
            (principal.tenant_id, ingress.credential_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeEntryAuthorityUnavailable("credential ownership and account scope are not persisted")
        credential = CredentialOwnership(
            tenant_id=_value(row, 1, "tenant_id"), credential_id=_value(row, 2, "credential_id"),
            account_scope=_value(row, 3, "account_scope"), exchange_id=_value(row, 4, "exchange_id"),
        )
        return {"id": _uuid(_value(row, 0, "id"), "scope_binding_id"), "credential": credential}

    def _load_instrument(self, cursor: Cursor, binding: dict[str, Any], ingress: RuntimeEntryIngressV1) -> dict[str, Any]:
        credential = binding["credential"]
        cursor.execute(
            """
            SELECT a.id, a.tenant_id, a.credential_id, a.account_scope,
                   a.instrument_id, a.market_type, a.instrument_rule_snapshot_id
              FROM qd_runtime_entry_instrument_authorities a
             WHERE a.scope_binding_id = %s
               AND a.tenant_id = %s
               AND a.credential_id = %s
               AND a.account_scope = %s
               AND a.instrument_id = %s
               AND a.market_type = %s
             FOR KEY SHARE
            """,
            (binding["id"], credential.tenant_id, credential.credential_id, credential.account_scope,
             ingress.instrument_id, ingress.market_type),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeEntryAuthorityUnavailable("instrument authority is not persisted for scope")
        authority = InstrumentAuthority(
            tenant_id=_value(row, 1, "tenant_id"), credential_id=_value(row, 2, "credential_id"),
            account_scope=_value(row, 3, "account_scope"), instrument_id=_value(row, 4, "instrument_id"),
            market_type=_value(row, 5, "market_type"), instrument_rule_snapshot_id=_value(row, 6, "instrument_rule_snapshot_id"),
        )
        return {"id": _uuid(_value(row, 0, "id"), "instrument_authority_id"), "authority": authority}

    def _load_position(
        self, cursor: Cursor, binding: dict[str, Any], instrument: dict[str, Any], ingress: RuntimeEntryIngressV1,
    ) -> dict[str, Any] | None:
        if ingress.action not in {OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION}:
            return None
        if ingress.target_position_id is None:
            raise RuntimeEntryAuthorityUnavailable("reducing ingress is missing persisted position subject")
        credential = binding["credential"]
        cursor.execute(
            """
            SELECT s.id, s.tenant_id, s.credential_id, s.account_scope,
                   s.instrument_id, s.market_type, s.position_side
              FROM qd_runtime_entry_position_subjects s
              JOIN qd_reconciliation_checkpoints c ON c.id = s.reconciliation_checkpoint_id
              JOIN qd_position_projections p ON p.id = s.position_projection_id
             WHERE s.id = %s
               AND s.scope_binding_id = %s
               AND s.instrument_authority_id = %s
               AND s.tenant_id = %s AND s.credential_id = %s AND s.account_scope = %s
               AND s.instrument_id = %s AND s.market_type = %s AND s.position_side = %s
               AND c.status = 'HEALTHY'
               AND p.quantity > 0
             FOR KEY SHARE OF s, c, p
            """,
            (ingress.target_position_id, binding["id"], instrument["id"], credential.tenant_id,
             credential.credential_id, credential.account_scope, ingress.instrument_id, ingress.market_type,
             ingress.position_side.value),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeEntryAuthorityUnavailable("target position authority is unavailable or not healthy")
        authority = PositionSubjectAuthority(
            position_id=_value(row, 0, "id"), tenant_id=_value(row, 1, "tenant_id"),
            credential_id=_value(row, 2, "credential_id"), account_scope=_value(row, 3, "account_scope"),
            instrument_id=_value(row, 4, "instrument_id"), market_type=_value(row, 5, "market_type"),
            position_side=ingress.position_side.__class__(_value(row, 6, "position_side")),
        )
        return {"id": authority.position_id, "authority": authority}

    def _validate_graph_authority(self, graph: DurableEntryGraphV2, authority: ResolvedRuntimeEntryAuthority) -> None:
        if not isinstance(graph, DurableEntryGraphV2) or not isinstance(authority, ResolvedRuntimeEntryAuthority):
            raise RuntimeEntryAuthorityRepositoryError("ingress persistence requires typed graph and authority")
        specification = graph.specification
        facts = authority.facts
        if (specification.tenant_id, specification.credential_id, specification.account_scope,
            specification.instrument_id, specification.market_type) != (
            facts.scope.tenant_id, facts.scope.credential_id, facts.scope.account_scope,
            facts.instrument.instrument_id, facts.instrument.market_type,
        ):
            raise RuntimeEntryAuthorityConflict("durable graph scope differs from resolved authority")
        reducing = specification.action in {OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION}
        if reducing != (authority.references.position_subject_id is not None):
            raise RuntimeEntryAuthorityConflict("durable graph position subject differs from action")
        if reducing and specification.economic_intent.target_position_id != authority.references.position_subject_id:
            raise RuntimeEntryAuthorityConflict("durable graph target position differs from authority")

    def _ingress_facts(self, graph: DurableEntryGraphV2, authority: ResolvedRuntimeEntryAuthority) -> dict[str, Any]:
        specification = graph.specification
        refs = authority.references
        return {
            "command_id": graph.command_id,
            "contract_version": RUNTIME_ENTRY_AUTHORITY_CONTRACT_VERSION,
            "scope_binding_id": refs.scope_binding_id,
            "instrument_authority_id": refs.instrument_authority_id,
            "position_subject_id": refs.position_subject_id,
            "tenant_id": specification.tenant_id,
            "credential_id": specification.credential_id,
            "account_scope": specification.account_scope,
            "instrument_id": specification.instrument_id,
            "market_type": specification.market_type,
            "action": specification.action.value,
            "actor_type": specification.actor.actor_type.value,
            "actor_id": specification.actor.actor_id,
            "source": specification.actor.entry_source.value,
            "mode": specification.mode.value,
            "idempotency_key": specification.idempotency_key,
            "economic_fingerprint": specification.economic_fingerprint,
            "request_fingerprint": specification.request_fingerprint,
            "correlation_id": specification.correlation_id,
            "occurred_at": specification.occurred_at,
        }

    def _insert(self, cursor: Cursor, facts: dict[str, Any]) -> bool:
        columns = tuple(facts)
        cursor.execute(
            f"INSERT INTO {RUNTIME_ENTRY_INGRESS_TABLE} ({', '.join(columns)}) "
            f"VALUES ({', '.join('%s' for _ in columns)}) ON CONFLICT DO NOTHING RETURNING command_id",
            tuple(facts[column] for column in columns),
        )
        return cursor.fetchone() is not None

    def _assert_exact_replay(self, cursor: Cursor, expected: dict[str, Any]) -> None:
        columns = tuple(expected)
        cursor.execute(
            f"SELECT {', '.join(columns)} FROM {RUNTIME_ENTRY_INGRESS_TABLE} "
            "WHERE tenant_id = %s AND credential_id = %s AND account_scope = %s "
            "AND idempotency_key = %s AND contract_version = %s FOR UPDATE",
            (expected["tenant_id"], expected["credential_id"], expected["account_scope"],
             expected["idempotency_key"], expected["contract_version"]),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(f"SELECT command_id FROM {RUNTIME_ENTRY_INGRESS_TABLE} WHERE command_id = %s FOR UPDATE", (expected["command_id"],))
            if cursor.fetchone() is not None:
                raise RuntimeEntryAuthorityConflict("command_id names a different runtime ingress")
            raise RuntimeEntryAuthorityConflict("runtime ingress uniqueness conflict is not visible")
        observed = {column: _value(row, index, column) for index, column in enumerate(columns)}
        for column in columns:
            actual, wanted = observed[column], expected[column]
            if column in {"command_id", "scope_binding_id", "instrument_authority_id", "position_subject_id"}:
                equal = actual is None and wanted is None or actual is not None and wanted is not None and _uuid(actual, column) == _uuid(wanted, column)
            elif column == "occurred_at":
                equal = _timestamp(actual, column) == _timestamp(wanted, column)
            else:
                equal = actual == wanted
            if not equal:
                raise RuntimeEntryAuthorityConflict(f"runtime ingress identity names different {column}")
