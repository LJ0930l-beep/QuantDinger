"""Caller-owned persistence for independent durable-entry hard-risk V2.

This deliberately never reads legacy command/order tables.  It locks the
already-persisted durable entry specification and persists only V2 risk facts.
The outer admission transaction owns commit and rollback on every path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Protocol
from uuid import UUID, uuid5

from app.domain.authoritative_risk_facts_contracts import RiskFactProvenance
from app.domain.durable_risk_enforcement_v2_contracts import DURABLE_RISK_UUID_NAMESPACE

from app.domain.durable_entry_persistence_contracts import (
    DURABLE_ENTRY_CONTRACT_VERSION,
    DURABLE_ENTRY_SPECIFICATION_TABLE,
)
from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2, EconomicOrderSubject
from app.domain.order_contracts import OrderAction
from app.domain.durable_risk_enforcement_v2_contracts import (
    DURABLE_RISK_DECISION_TABLE,
    DURABLE_RISK_INPUT_SNAPSHOT_TABLE,
    DURABLE_RISK_POLICY_SNAPSHOT_TABLE,
    DURABLE_RISK_RESERVATION_TABLE,
    DurableRiskConflict,
    DurableRiskDecisionFactV2,
    DurableRiskEnforcementV2Error,
    DurableRiskInputSnapshotFactV2,
    DurableRiskPersistDisposition,
    DurableRiskPersistResultV2,
    DurableRiskPolicySnapshotFactV2,
    DurableRiskRepositoryError,
    DurableRiskReservationFactV2,
)


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def _row(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


def _uuid(value: Any, field: str) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(str(value))).lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise DurableRiskConflict(f"persisted {field} must be UUID") from exc


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise DurableRiskConflict(f"persisted {field} must be Decimal-compatible")
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DurableRiskConflict(f"persisted {field} must be Decimal-compatible") from exc


def _timestamp(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DurableRiskConflict(f"persisted {field} must be timezone-aware UTC")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise DurableRiskConflict(f"persisted {field} must use UTC")
    return value.astimezone(timezone.utc).isoformat()


class DurableRiskEnforcementRepositoryV2:
    """INSERT-first exact-replay boundary; never commits, rolls back, or opens a connection."""

    def persist_durable_risk(
        self,
        connection: Connection,
        *,
        policy_snapshot: DurableRiskPolicySnapshotFactV2,
        input_snapshot: DurableRiskInputSnapshotFactV2,
        decision: DurableRiskDecisionFactV2,
        reservation: DurableRiskReservationFactV2 | None,
        provenance: tuple[RiskFactProvenance, ...] = (),
    ) -> DurableRiskPersistResultV2:
        self._validate_chain(policy_snapshot, input_snapshot, decision, reservation)
        self._validate_provenance(provenance)
        try:
            cursor = connection.cursor()
        except Exception as exc:
            raise DurableRiskRepositoryError("durable risk database cursor could not be opened") from exc
        try:
            self._lock_and_verify_durable_entry(cursor, decision)
            created = False
            created |= self._insert_or_verify_policy(cursor, policy_snapshot)
            created |= self._insert_or_verify_input(cursor, input_snapshot)
            created |= self._insert_or_verify_decision(cursor, decision)
            created |= self._insert_or_verify_provenance(cursor, input_snapshot, decision, provenance)
            if reservation is not None:
                created |= self._insert_or_verify_reservation(cursor, reservation)
            return self._result(decision, reservation, DurableRiskPersistDisposition.CREATED if created else DurableRiskPersistDisposition.REPLAYED)
        except (DurableRiskConflict, DurableRiskEnforcementV2Error, DurableRiskRepositoryError):
            raise
        except Exception as exc:
            raise DurableRiskRepositoryError("durable risk database operation failed") from exc
        finally:
            try:
                cursor.close()
            except Exception as exc:
                raise DurableRiskRepositoryError("durable risk database cursor could not be closed") from exc

    def load_complete_replay(
        self,
        connection: Connection,
        graph: DurableEntryGraphV2,
    ) -> DurableRiskPersistResultV2 | None:
        """Return an exact completed chain before any source facts are re-read."""

        if not isinstance(graph, DurableEntryGraphV2) or not isinstance(graph.subject, EconomicOrderSubject):
            raise DurableRiskConflict("durable risk replay requires a typed non-CANCEL durable graph")

        try:
            cursor = connection.cursor()
        except Exception as exc:
            raise DurableRiskRepositoryError("durable risk replay cursor could not be opened") from exc
        try:
            spec = graph.specification
            cursor.execute(
                f"""SELECT id, economic_order_id, durable_entry_contract_version, economic_fingerprint,
                           request_fingerprint, tenant_id, credential_id, account_scope, instrument_id,
                           market_type, action, risk_effect, actor_type, actor_id, source, mode,
                           correlation_id, entry_occurred_at, scope_fingerprint, audit_fingerprint,
                           allowed, decision_status, decision_fingerprint
                      FROM {DURABLE_RISK_DECISION_TABLE}
                     WHERE command_id = %s FOR UPDATE""",
                (graph.command_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            expected = (
                graph.subject.economic_order_id, DURABLE_ENTRY_CONTRACT_VERSION,
                spec.economic_fingerprint, spec.request_fingerprint, spec.tenant_id,
                spec.credential_id, spec.account_scope, spec.instrument_id, spec.market_type,
                spec.action.value, spec.risk_effect.value, spec.actor.actor_type.value,
                spec.actor.actor_id, spec.actor.entry_source.value, spec.mode.value,
                spec.correlation_id, spec.occurred_at,
            )
            actual = tuple(_row(row, index, name) for index, name in enumerate((
                "economic_order_id", "durable_entry_contract_version", "economic_fingerprint",
                "request_fingerprint", "tenant_id", "credential_id", "account_scope", "instrument_id",
                "market_type", "action", "risk_effect", "actor_type", "actor_id", "source", "mode",
                "correlation_id", "entry_occurred_at",
            ), start=1))
            for index, (observed, wanted) in enumerate(zip(actual, expected)):
                if index == 0:
                    equal = _uuid(observed, "economic_order_id") == _uuid(wanted, "economic_order_id")
                elif index == 16:
                    equal = _timestamp(observed, "entry_occurred_at") == _timestamp(wanted, "entry_occurred_at")
                else:
                    equal = observed == wanted
                if not equal:
                    raise DurableRiskConflict("durable risk replay scope conflicts with graph")
            decision_id = _uuid(_row(row, 0, "id"), "id")
            cursor.execute("SELECT source_kind FROM qd_durable_risk_fact_provenance WHERE risk_decision_id = %s FOR UPDATE", (decision_id,))
            source_kinds = { _row(item, 0, "source_kind") for item in cursor.fetchall() }
            required = {"POLICY", "ACCOUNT", "INSTRUMENT_RULES", "RECONCILIATION", "KILL_SWITCH_GLOBAL", "KILL_SWITCH_ACCOUNT", "KILL_SWITCH_STRATEGY", "ACTIVE_RESERVATIONS"}
            if spec.action in {OrderAction.OPEN, OrderAction.INCREASE}:
                required.add("MARKET")
            if source_kinds != required:
                raise DurableRiskConflict("existing durable risk chain lacks exact authoritative provenance")
            allowed = _row(row, 20, "allowed")
            status = _row(row, 21, "decision_status")
            cursor.execute(f"SELECT id FROM {DURABLE_RISK_RESERVATION_TABLE} WHERE decision_id = %s AND state = 'ACTIVE' FOR UPDATE", (decision_id,))
            reservations = cursor.fetchall()
            if allowed and spec.action in {OrderAction.OPEN, OrderAction.INCREASE}:
                if len(reservations) != 1:
                    raise DurableRiskConflict("existing allowed increasing risk chain lacks exactly one reservation")
                reservation_id = _uuid(_row(reservations[0], 0, "id"), "reservation_id")
            elif reservations:
                raise DurableRiskConflict("non-reserving durable risk replay has an active reservation")
            else:
                reservation_id = None
            return DurableRiskPersistResultV2(
                command_id=graph.command_id, economic_order_id=graph.subject.economic_order_id,
                durable_entry_contract_version=_row(row, 2, "durable_entry_contract_version"),
                economic_fingerprint=spec.economic_fingerprint, request_fingerprint=spec.request_fingerprint,
                tenant_id=spec.tenant_id, credential_id=spec.credential_id, account_scope=spec.account_scope,
                instrument_id=spec.instrument_id, market_type=spec.market_type, action=spec.action,
                risk_effect=spec.risk_effect, actor_type=spec.actor.actor_type.value, actor_id=spec.actor.actor_id,
                source=spec.actor.entry_source.value, mode=spec.mode.value, correlation_id=spec.correlation_id,
                entry_occurred_at=spec.occurred_at, scope_fingerprint=_row(row, 18, "scope_fingerprint"),
                audit_fingerprint=_row(row, 19, "audit_fingerprint"), decision_id=decision_id,
                reservation_id=reservation_id, allowed=allowed, decision_status=status,
                decision_fingerprint=_row(row, 22, "decision_fingerprint"),
                disposition=DurableRiskPersistDisposition.REPLAYED,
            )
        except (DurableRiskConflict, DurableRiskRepositoryError):
            raise
        except Exception as exc:
            raise DurableRiskRepositoryError("durable risk replay database operation failed") from exc
        finally:
            try:
                cursor.close()
            except Exception as exc:
                raise DurableRiskRepositoryError("durable risk replay cursor could not be closed") from exc

    @staticmethod
    def _validate_provenance(provenance: tuple[RiskFactProvenance, ...]) -> None:
        if not isinstance(provenance, tuple) or not all(isinstance(item, RiskFactProvenance) for item in provenance):
            raise DurableRiskRepositoryError("durable risk provenance must use typed immutable facts")
        kinds = [item.source_kind for item in provenance]
        if len(kinds) != len(set(kinds)):
            raise DurableRiskConflict("durable risk provenance cannot repeat a source kind")

    def _validate_chain(self, policy: DurableRiskPolicySnapshotFactV2, inputs: DurableRiskInputSnapshotFactV2, decision: DurableRiskDecisionFactV2, reservation: DurableRiskReservationFactV2 | None) -> None:
        if not all(isinstance(value, expected) for value, expected in ((policy, DurableRiskPolicySnapshotFactV2), (inputs, DurableRiskInputSnapshotFactV2), (decision, DurableRiskDecisionFactV2))):
            raise DurableRiskRepositoryError("durable risk persistence requires typed V2 facts")
        if decision.policy_snapshot != policy or decision.input_snapshot != inputs:
            raise DurableRiskConflict("decision must bind exact V2 snapshots")
        if reservation is not None:
            if reservation.decision != decision:
                raise DurableRiskConflict("reservation must bind exact V2 decision")
            if not decision.decision.allowed:
                raise DurableRiskConflict("denied durable risk decision cannot reserve capacity")
        elif decision.decision.allowed and decision.scope.graph.specification.action in {OrderAction.OPEN, OrderAction.INCREASE}:
            raise DurableRiskConflict("allowed OPEN/INCREASE durable risk decision requires a reservation")

    def _scope(self, decision: DurableRiskDecisionFactV2) -> dict[str, Any]:
        spec = decision.scope.graph.specification
        return {
            "contract_version": decision.scope.contract_version, "command_id": decision.scope.command_id,
            "economic_order_id": decision.scope.economic_order_id,
            "durable_entry_contract_version": decision.scope.durable_entry_contract_version,
            "economic_fingerprint": spec.economic_fingerprint, "request_fingerprint": spec.request_fingerprint,
            "tenant_id": spec.tenant_id, "credential_id": spec.credential_id,
            "account_scope": spec.account_scope, "instrument_id": spec.instrument_id,
            "market_type": spec.market_type, "action": spec.action.value,
            "risk_effect": spec.risk_effect.value, "actor_type": spec.actor.actor_type.value,
            "actor_id": spec.actor.actor_id, "source": spec.actor.entry_source.value,
            "mode": spec.mode.value, "correlation_id": spec.correlation_id,
            "entry_occurred_at": spec.occurred_at, "scope_fingerprint": decision.scope.scope_fingerprint,
            "audit_fingerprint": decision.scope.audit_fingerprint,
        }

    def _lock_and_verify_durable_entry(self, cursor: Cursor, decision: DurableRiskDecisionFactV2) -> None:
        scope = self._scope(decision)
        fields = (
            "contract_version", "command_id", "tenant_id", "credential_id", "account_scope", "instrument_id", "market_type", "action", "risk_effect", "economic_order_id", "economic_fingerprint", "request_fingerprint", "actor_type", "actor_id", "source", "mode", "correlation_id", "occurred_at",
        )
        cursor.execute(
            f"SELECT {', '.join(fields)} FROM {DURABLE_ENTRY_SPECIFICATION_TABLE} WHERE command_id = %s FOR UPDATE",
            (scope["command_id"],),
        )
        row = cursor.fetchone()
        if row is None:
            raise DurableRiskConflict("durable entry specification is absent")
        expected = {
            **scope,
            # qd_durable_entry_specifications records the entry contract, not
            # this independent durable-risk contract version.
            "contract_version": scope["durable_entry_contract_version"],
            "occurred_at": scope["entry_occurred_at"],
        }
        for index, field in enumerate(fields):
            actual, wanted = _row(row, index, field), expected[field]
            if field in {"command_id", "economic_order_id"}:
                equal = _uuid(actual, field) == _uuid(wanted, field)
            elif field == "occurred_at":
                equal = _timestamp(actual, field) == _timestamp(wanted, field)
            else:
                equal = actual == wanted
            if not equal:
                raise DurableRiskConflict(f"durable entry {field} does not match V2 risk scope")
        if scope["durable_entry_contract_version"] != DURABLE_ENTRY_CONTRACT_VERSION:
            raise DurableRiskConflict("unsupported durable entry contract version")

    def _insert_or_verify_policy(self, cursor: Cursor, fact: DurableRiskPolicySnapshotFactV2) -> bool:
        scope = self._scope_for_fact(fact.scope)
        policy = fact.policy
        values = {**scope, "id": fact.snapshot_id, "policy_hash": fact.policy_hash, "policy_version": policy.policy_version, "valuation_currency": policy.valuation_currency, "max_gross_notional": policy.max_gross_notional.value, "max_net_notional": policy.max_net_notional.value, "max_instrument_notional": policy.max_instrument_notional.value, "max_leverage": policy.max_leverage, "minimum_available_margin": policy.minimum_available_margin.value, "max_daily_loss": policy.max_daily_loss.value, "max_drawdown_ratio": policy.max_drawdown_ratio, "policy_payload_json": json.dumps(self._policy_payload(fact), sort_keys=True, separators=(",", ":"))}
        return self._insert_or_verify(cursor, DURABLE_RISK_POLICY_SNAPSHOT_TABLE, values, "id")

    def _insert_or_verify_input(self, cursor: Cursor, fact: DurableRiskInputSnapshotFactV2) -> bool:
        scope = self._scope_for_fact(fact.scope)
        exposure, switches = fact.exposure, fact.kill_switches
        values = {**scope, "id": fact.snapshot_id, "input_hash": fact.input_hash, "input_version": "risk-input-v2", "valuation_currency": exposure.valuation_currency, "gross_notional": exposure.gross_notional, "net_notional": exposure.net_notional, "instrument_notional": exposure.instrument_notional, "available_margin": exposure.available_margin, "equity": exposure.equity, "peak_equity": exposure.peak_equity, "daily_realized_pnl": exposure.daily_realized_pnl, "reconciliation_health": exposure.reconciliation_health.value, "market_data_health": exposure.market_data_health.value, "account_facts_verified": exposure.account_facts_verified, "global_kill_switch_version": switches.global_state.version, "global_kill_switch_enabled": switches.global_state.enabled, "global_kill_switch_mode": None if switches.global_state.mode is None else switches.global_state.mode.value, "account_kill_switch_version": switches.account_state.version, "account_kill_switch_enabled": switches.account_state.enabled, "account_kill_switch_mode": None if switches.account_state.mode is None else switches.account_state.mode.value, "strategy_kill_switch_version": switches.strategy_state.version, "strategy_kill_switch_enabled": switches.strategy_state.enabled, "strategy_kill_switch_mode": None if switches.strategy_state.mode is None else switches.strategy_state.mode.value, "exposure_payload_json": json.dumps(self._exposure_payload(fact), sort_keys=True, separators=(",", ":")), "kill_switch_payload_json": json.dumps(self._kill_payload(fact), sort_keys=True, separators=(",", ":")), "observed_at": fact.observed_at}
        return self._insert_or_verify(cursor, DURABLE_RISK_INPUT_SNAPSHOT_TABLE, values, "id")

    def _insert_or_verify_decision(self, cursor: Cursor, fact: DurableRiskDecisionFactV2) -> bool:
        scope = self._scope(fact)
        projected = fact.decision.projected
        values = {**scope, "id": fact.decision_id, "policy_snapshot_id": fact.policy_snapshot.snapshot_id, "input_snapshot_id": fact.input_snapshot.snapshot_id, "policy_hash": fact.policy_snapshot.policy_hash, "input_hash": fact.input_snapshot.input_hash, "decision_fingerprint": fact.decision_fingerprint, "allowed": fact.decision.allowed, "decision_status": fact.decision_status, "rejection_codes_json": json.dumps([item.value for item in fact.decision.rejections], separators=(",", ":")), "projected_gross_notional": projected.gross_notional, "projected_net_notional": projected.net_notional, "projected_instrument_notional": projected.instrument_notional, "projected_available_margin": projected.available_margin, "projected_leverage": projected.leverage, "projected_daily_loss": projected.daily_loss, "projected_drawdown_ratio": projected.drawdown_ratio, "projected_risk_payload_json": json.dumps(self._projected_payload(fact), sort_keys=True, separators=(",", ":"))}
        return self._insert_or_verify(cursor, DURABLE_RISK_DECISION_TABLE, values, "id")

    def _insert_or_verify_provenance(
        self,
        cursor: Cursor,
        input_snapshot: DurableRiskInputSnapshotFactV2,
        decision: DurableRiskDecisionFactV2,
        provenance: tuple[RiskFactProvenance, ...],
    ) -> bool:
        """Append source evidence after both immutable target facts exist."""

        created = False
        anchor = decision.scope.graph.specification.occurred_at
        for item in provenance:
            identifier = str(uuid5(
                DURABLE_RISK_UUID_NAMESPACE,
                f"risk-fact-provenance:{decision.decision_id}:{item.source_kind.value}:{item.source_fingerprint}",
            ))
            values = {
                "id": identifier,
                "contract_version": "authoritative-risk-facts-v1",
                "command_id": decision.scope.command_id,
                "risk_input_snapshot_id": input_snapshot.snapshot_id,
                "risk_decision_id": decision.decision_id,
                "source_kind": item.source_kind.value,
                "source_identity": item.source_identity,
                "source_version": item.source_version,
                "source_fingerprint": item.source_fingerprint,
                "source_observed_at": item.observed_at,
                "selection_anchor": anchor,
            }
            created |= self._insert_or_verify(cursor, "qd_durable_risk_fact_provenance", values, "id")
        return created

    def _insert_or_verify_reservation(self, cursor: Cursor, fact: DurableRiskReservationFactV2) -> bool:
        scope = self._scope(fact.decision)
        demand = fact.demand
        values = {**scope, "id": fact.reservation_id, "decision_id": fact.decision.decision_id, "reservation_hash": fact.reservation_hash, "valuation_currency": demand.valuation_currency, "reserved_gross_notional": demand.gross_notional, "reserved_net_notional": demand.net_notional, "reserved_instrument_notional": demand.instrument_notional, "reserved_margin": demand.margin, "state": "ACTIVE", "expires_at": fact.expires_at}
        return self._insert_or_verify(cursor, DURABLE_RISK_RESERVATION_TABLE, values, "id")

    def _insert_or_verify(self, cursor: Cursor, table: str, values: dict[str, Any], identifier: str) -> bool:
        columns = tuple(values)
        cursor.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)}) ON CONFLICT DO NOTHING RETURNING {identifier}",
            tuple(values[column] for column in columns),
        )
        if cursor.fetchone() is not None:
            return True
        cursor.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE {identifier} = %s FOR UPDATE", (values[identifier],))
        row = cursor.fetchone()
        if row is None:
            raise DurableRiskConflict(f"{table} uniqueness conflict is not visible")
        for index, column in enumerate(columns):
            if not self._equal(column, _row(row, index, column), values[column]):
                raise DurableRiskConflict(f"{table} identity names different immutable {column}")
        return False

    def _equal(self, field: str, actual: Any, wanted: Any) -> bool:
        if field in {"id", "command_id", "economic_order_id", "policy_snapshot_id", "input_snapshot_id", "decision_id", "risk_input_snapshot_id", "risk_decision_id"}:
            return _uuid(actual, field) == _uuid(wanted, field)
        if field in {"entry_occurred_at", "observed_at", "expires_at", "source_observed_at", "selection_anchor"}:
            return _timestamp(actual, field) == _timestamp(wanted, field)
        if field.startswith("reserved_") or field.startswith("max_") or field.startswith("minimum_") or field in {"gross_notional", "net_notional", "instrument_notional", "available_margin", "equity", "peak_equity", "daily_realized_pnl", "projected_gross_notional", "projected_net_notional", "projected_instrument_notional", "projected_available_margin", "projected_leverage", "projected_daily_loss", "projected_drawdown_ratio"}:
            return _decimal(actual, field) == _decimal(wanted, field)
        if field.endswith("_json"):
            try:
                return json.loads(actual) == json.loads(wanted) if isinstance(actual, str) else actual == json.loads(wanted)
            except (TypeError, ValueError):
                return False
        return actual == wanted

    def _scope_for_fact(self, scope: Any) -> dict[str, Any]:
        spec = scope.graph.specification
        return {"contract_version": scope.contract_version, "command_id": scope.command_id, "economic_order_id": scope.economic_order_id, "durable_entry_contract_version": scope.durable_entry_contract_version, "economic_fingerprint": spec.economic_fingerprint, "request_fingerprint": spec.request_fingerprint, "tenant_id": spec.tenant_id, "credential_id": spec.credential_id, "account_scope": spec.account_scope, "instrument_id": spec.instrument_id, "market_type": spec.market_type, "action": spec.action.value, "risk_effect": spec.risk_effect.value, "actor_type": spec.actor.actor_type.value, "actor_id": spec.actor.actor_id, "source": spec.actor.entry_source.value, "mode": spec.mode.value, "correlation_id": spec.correlation_id, "entry_occurred_at": spec.occurred_at, "scope_fingerprint": scope.scope_fingerprint, "audit_fingerprint": scope.audit_fingerprint}

    def _policy_payload(self, fact: DurableRiskPolicySnapshotFactV2) -> dict[str, str]:
        policy = fact.policy
        return {name: str(getattr(policy, name).value if hasattr(getattr(policy, name), "value") else getattr(policy, name)) for name in ("max_gross_notional", "max_net_notional", "max_instrument_notional", "max_leverage", "minimum_available_margin", "max_daily_loss", "max_drawdown_ratio")}

    def _exposure_payload(self, fact: DurableRiskInputSnapshotFactV2) -> dict[str, str]:
        return {name: str(getattr(fact.exposure, name)) for name in ("gross_notional", "net_notional", "instrument_notional", "available_margin", "equity", "peak_equity", "daily_realized_pnl", "reconciliation_health", "market_data_health", "account_facts_verified")}

    def _kill_payload(self, fact: DurableRiskInputSnapshotFactV2) -> dict[str, dict[str, Any]]:
        return {name: {"version": state.version, "enabled": state.enabled, "mode": None if state.mode is None else state.mode.value} for name, state in (("global", fact.kill_switches.global_state), ("account", fact.kill_switches.account_state), ("strategy", fact.kill_switches.strategy_state))}

    def _projected_payload(self, fact: DurableRiskDecisionFactV2) -> dict[str, str]:
        return {name: str(getattr(fact.decision.projected, name)) for name in ("gross_notional", "net_notional", "instrument_notional", "available_margin", "leverage", "daily_loss", "drawdown_ratio")}

    def _result(self, decision: DurableRiskDecisionFactV2, reservation: DurableRiskReservationFactV2 | None, disposition: DurableRiskPersistDisposition) -> DurableRiskPersistResultV2:
        scope = self._scope(decision)
        return DurableRiskPersistResultV2(
            command_id=scope["command_id"], economic_order_id=scope["economic_order_id"],
            durable_entry_contract_version=scope["durable_entry_contract_version"],
            economic_fingerprint=scope["economic_fingerprint"], request_fingerprint=scope["request_fingerprint"],
            tenant_id=scope["tenant_id"], credential_id=scope["credential_id"], account_scope=scope["account_scope"],
            instrument_id=scope["instrument_id"], market_type=scope["market_type"], action=decision.scope.graph.specification.action,
            risk_effect=decision.scope.graph.specification.risk_effect, actor_type=scope["actor_type"], actor_id=scope["actor_id"],
            source=scope["source"], mode=scope["mode"], correlation_id=scope["correlation_id"], entry_occurred_at=scope["entry_occurred_at"],
            scope_fingerprint=scope["scope_fingerprint"], audit_fingerprint=scope["audit_fingerprint"], decision_id=decision.decision_id,
            reservation_id=None if reservation is None else reservation.reservation_id, allowed=decision.decision.allowed,
            decision_status=decision.decision_status, decision_fingerprint=decision.decision_fingerprint, disposition=disposition,
        )
