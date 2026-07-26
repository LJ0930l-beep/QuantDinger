"""Atomic persistence for immutable hard-risk decision and reservation facts.

This repository is intentionally not connected to a gateway or execution
runtime.  It accepts only pre-evaluated pure-domain facts and persists them in
one transaction, leaving PostgreSQL constraints as the final race arbiter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from app.domain.risk_enforcement_contracts import (
    RiskDecisionFact,
    RiskEnforcementContractError,
    RiskInputSnapshotFact,
    RiskPolicySnapshotFact,
    RiskReservationFact,
)


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class RiskEnforcementRepositoryError(RuntimeError):
    """Base typed persistence failure; raw driver errors never mean replay."""


class RiskEnforcementConflict(RiskEnforcementRepositoryError):
    """An idempotency identity already names different immutable facts."""


class RiskEnforcementDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class RiskEnforcementPersistResult:
    decision_id: str
    reservation_id: str | None
    disposition: RiskEnforcementDisposition


def _row(row: Any, index: int, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[index]


def _policy_legacy_json(fact: RiskPolicySnapshotFact) -> str:
    policy = fact.policy
    value = {
        "contract_version": "hard-risk-enforcement-v1",
        "policy_version": policy.policy_version,
        "policy_hash": fact.policy_hash,
        "valuation_currency": policy.valuation_currency,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class RiskEnforcementRepository:
    """Write append-only risk facts and one active reservation atomically."""

    def persist(
        self,
        connection: Connection,
        *,
        policy_snapshot: RiskPolicySnapshotFact,
        input_snapshot: RiskInputSnapshotFact,
        decision: RiskDecisionFact,
        reservation: RiskReservationFact | None,
    ) -> RiskEnforcementPersistResult:
        self._validate_graph(policy_snapshot, input_snapshot, decision, reservation)
        cursor = connection.cursor()
        try:
            self._lock_and_verify_command_order(cursor, decision)
            created = False
            created |= self._insert_or_verify_policy(cursor, policy_snapshot)
            created |= self._insert_or_verify_input(cursor, input_snapshot)
            created |= self._insert_or_verify_decision(cursor, decision)
            if reservation is not None:
                created |= self._insert_or_verify_reservation(cursor, reservation)
            self._lock_and_verify_fact_chain(cursor, decision, reservation)
            return RiskEnforcementPersistResult(
                decision.decision_id,
                None if reservation is None else reservation.reservation_id,
                RiskEnforcementDisposition.CREATED if created else RiskEnforcementDisposition.REPLAYED,
            )
        except (RiskEnforcementRepositoryError, RiskEnforcementContractError):
            raise
        except Exception as exc:
            raise RiskEnforcementRepositoryError("risk enforcement database operation failed") from exc
        finally:
            cursor.close()

    def _validate_graph(self, policy: RiskPolicySnapshotFact, inputs: RiskInputSnapshotFact, decision: RiskDecisionFact, reservation: RiskReservationFact | None) -> None:
        if not isinstance(policy, RiskPolicySnapshotFact) or not isinstance(inputs, RiskInputSnapshotFact) or not isinstance(decision, RiskDecisionFact):
            raise RiskEnforcementRepositoryError("risk persistence requires immutable enforcement facts")
        if decision.policy_snapshot != policy or decision.input_snapshot != inputs:
            raise RiskEnforcementConflict("decision must name the exact persisted snapshot facts")
        if reservation is not None and reservation.decision != decision:
            raise RiskEnforcementConflict("reservation must name the exact persisted decision")
        if reservation is not None and not decision.decision.allowed:
            raise RiskEnforcementConflict("denied decision cannot persist a reservation")

    def _lock_and_verify_command_order(self, cursor: Cursor, fact: RiskDecisionFact) -> None:
        """Lock the durable command/order scope before any risk fact is written."""

        scope = fact.scope
        cursor.execute(
            """
            SELECT c.action, c.actor_type, c.actor_id, c.tenant_id,
                   c.credential_id, c.account_scope, e.tenant_id,
                   e.credential_id, e.account_scope, e.instrument_id, e.market_type
              FROM qd_order_commands AS c
              JOIN qd_economic_orders AS e ON e.id = %s
             WHERE c.id = %s
               AND c.tenant_id = %s AND c.credential_id = %s
               AND c.account_scope = %s
               AND e.tenant_id = %s AND e.credential_id = %s
               AND e.account_scope = %s AND e.instrument_id = %s AND e.market_type = %s
             FOR UPDATE OF c, e
            """,
            (scope.economic_order_id, scope.command_id, scope.tenant_id, scope.credential_id,
             scope.account_scope, scope.tenant_id, scope.credential_id, scope.account_scope,
             scope.instrument_id, scope.market_type),
        )
        row = cursor.fetchone()
        if row is None:
            raise RiskEnforcementConflict("command and economic order scope is absent or inconsistent")
        if (str(_row(row, 0, "action")), str(_row(row, 1, "actor_type")), str(_row(row, 2, "actor_id"))) != (scope.action.value, scope.actor.value, scope.actor_id):
            raise RiskEnforcementConflict("command action or actor facts do not match risk decision")

    def _lock_and_verify_fact_chain(self, cursor: Cursor, decision: RiskDecisionFact, reservation: RiskReservationFact | None) -> None:
        """Keep the full snapshot→decision→reservation graph locked and scoped."""

        scope = decision.scope
        cursor.execute(
            "SELECT id FROM qd_risk_policy_snapshots WHERE id = %s AND tenant_id = %s AND credential_id = %s AND account_scope = %s AND instrument_id = %s AND market_type = %s FOR UPDATE",
            (decision.policy_snapshot.snapshot_id, scope.tenant_id, scope.credential_id, scope.account_scope, scope.instrument_id, scope.market_type),
        )
        if cursor.fetchone() is None:
            raise RiskEnforcementConflict("policy snapshot scope is absent after persistence")
        cursor.execute(
            "SELECT id FROM qd_risk_input_snapshots WHERE id = %s AND tenant_id = %s AND credential_id = %s AND account_scope = %s AND instrument_id = %s AND market_type = %s FOR UPDATE",
            (decision.input_snapshot.snapshot_id, scope.tenant_id, scope.credential_id, scope.account_scope, scope.instrument_id, scope.market_type),
        )
        if cursor.fetchone() is None:
            raise RiskEnforcementConflict("input snapshot scope is absent after persistence")
        cursor.execute(
            "SELECT id FROM qd_risk_decisions WHERE id = %s AND command_id = %s AND economic_order_id = %s AND tenant_id = %s AND credential_id = %s AND account_scope = %s AND instrument_id = %s AND market_type = %s FOR UPDATE",
            (decision.decision_id, scope.command_id, scope.economic_order_id, scope.tenant_id, scope.credential_id, scope.account_scope, scope.instrument_id, scope.market_type),
        )
        if cursor.fetchone() is None:
            raise RiskEnforcementConflict("decision scope is absent after persistence")
        if reservation is not None:
            cursor.execute(
                "SELECT id FROM qd_risk_reservations WHERE id = %s AND decision_id = %s AND command_id = %s AND economic_order_id = %s FOR UPDATE",
                (reservation.reservation_id, decision.decision_id, scope.command_id, scope.economic_order_id),
            )
            if cursor.fetchone() is None:
                raise RiskEnforcementConflict("reservation scope is absent after persistence")

    def _insert_or_verify_policy(self, cursor: Cursor, fact: RiskPolicySnapshotFact) -> bool:
        scope, policy = fact.scope, fact.policy
        cursor.execute(
            """
            INSERT INTO qd_risk_policy_snapshots (
                id, tenant_id, credential_id, account_scope, instrument_id, market_type,
                valuation_currency, policy_version, policy_hash, max_gross_notional,
                max_net_notional, max_instrument_notional, max_leverage,
                minimum_available_margin, max_daily_loss, max_drawdown_ratio
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
            """,
            (fact.snapshot_id, scope.tenant_id, scope.credential_id, scope.account_scope,
             scope.instrument_id, scope.market_type, policy.valuation_currency,
             policy.policy_version, fact.policy_hash,
             str(policy.max_gross_notional.value), str(policy.max_net_notional.value),
             str(policy.max_instrument_notional.value), str(policy.max_leverage),
             str(policy.minimum_available_margin.value), str(policy.max_daily_loss.value),
             str(policy.max_drawdown_ratio)),
        )
        if cursor.fetchone() is not None:
            return True
        cursor.execute(
            """SELECT id, tenant_id, credential_id, account_scope, instrument_id, market_type,
                      valuation_currency, policy_version, policy_hash
                 FROM qd_risk_policy_snapshots WHERE id = %s FOR UPDATE""",
            (fact.snapshot_id,),
        )
        row = cursor.fetchone()
        expected = (fact.snapshot_id, scope.tenant_id, scope.credential_id, scope.account_scope,
                    scope.instrument_id, scope.market_type, policy.valuation_currency,
                    policy.policy_version, fact.policy_hash)
        if row is None or tuple(str(_row(row, i, key)) if i == 0 else _row(row, i, key) for i, key in enumerate(
            ("id", "tenant_id", "credential_id", "account_scope", "instrument_id", "market_type", "valuation_currency", "policy_version", "policy_hash")
        )) != expected:
            raise RiskEnforcementConflict("policy snapshot identity names different immutable facts")
        return False

    def _insert_or_verify_input(self, cursor: Cursor, fact: RiskInputSnapshotFact) -> bool:
        scope, exposure = fact.scope, fact.exposure
        cursor.execute(
            """
            INSERT INTO qd_risk_input_snapshots (
                id, tenant_id, credential_id, account_scope, instrument_id, market_type,
                input_version, input_hash, reconciliation_health, market_data_health,
                account_facts_verified, gross_notional, net_notional, instrument_notional,
                global_kill_switch_version, global_kill_switch_enabled, global_kill_switch_mode,
                account_kill_switch_version, account_kill_switch_enabled, account_kill_switch_mode,
                strategy_kill_switch_version, strategy_kill_switch_enabled, strategy_kill_switch_mode,
                available_margin, equity, peak_equity, daily_realized_pnl, observed_at, occurred_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
            """,
            (fact.snapshot_id, scope.tenant_id, scope.credential_id, scope.account_scope,
             scope.instrument_id, scope.market_type, fact.input_version, fact.input_hash,
             exposure.reconciliation_health.value, exposure.market_data_health.value,
             exposure.account_facts_verified, str(exposure.gross_notional), str(exposure.net_notional), str(exposure.instrument_notional),
             fact.kill_switches.global_state.version, fact.kill_switches.global_state.enabled, None if fact.kill_switches.global_state.mode is None else fact.kill_switches.global_state.mode.value,
             fact.kill_switches.account_state.version, fact.kill_switches.account_state.enabled, None if fact.kill_switches.account_state.mode is None else fact.kill_switches.account_state.mode.value,
             fact.kill_switches.strategy_state.version, fact.kill_switches.strategy_state.enabled, None if fact.kill_switches.strategy_state.mode is None else fact.kill_switches.strategy_state.mode.value,
             str(exposure.available_margin), str(exposure.equity), str(exposure.peak_equity), str(exposure.daily_realized_pnl), fact.observed_at, fact.occurred_at),
        )
        if cursor.fetchone() is not None:
            return True
        cursor.execute(
            """SELECT id, tenant_id, credential_id, account_scope, instrument_id, market_type,
                      input_version, input_hash
                 FROM qd_risk_input_snapshots WHERE id = %s FOR UPDATE""",
            (fact.snapshot_id,),
        )
        row = cursor.fetchone()
        expected = (fact.snapshot_id, scope.tenant_id, scope.credential_id, scope.account_scope,
                    scope.instrument_id, scope.market_type, fact.input_version, fact.input_hash)
        if row is None or tuple(str(_row(row, i, key)) if i == 0 else _row(row, i, key) for i, key in enumerate(
            ("id", "tenant_id", "credential_id", "account_scope", "instrument_id", "market_type", "input_version", "input_hash")
        )) != expected:
            raise RiskEnforcementConflict("input snapshot identity names different immutable facts")
        return False

    def _insert_or_verify_decision(self, cursor: Cursor, fact: RiskDecisionFact) -> bool:
        scope = fact.scope
        cursor.execute(
            """
            INSERT INTO qd_risk_decisions (
                id, command_id, economic_order_id, tenant_id, credential_id,
                account_scope, instrument_id, market_type, action, actor_type, actor_id, risk_effect,
                policy_snapshot_id, risk_input_snapshot_id, decision,
                decision_fingerprint, rejection_codes, projected_gross_notional,
                projected_net_notional, projected_instrument_notional, projected_available_margin,
                projected_leverage, projected_daily_loss, projected_drawdown_ratio,
                correlation_id, observed_at, occurred_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
            """,
            (fact.decision_id, scope.command_id, scope.economic_order_id, scope.tenant_id,
             scope.credential_id, scope.account_scope, scope.instrument_id, scope.market_type,
             scope.action.value, scope.actor.value, scope.actor_id, fact.decision.risk_effect.value,
             fact.policy_snapshot.snapshot_id, fact.input_snapshot.snapshot_id, fact.decision_status, fact.decision_fingerprint,
             json.dumps([item.value for item in fact.decision.rejections], separators=(",", ":")),
             str(fact.decision.projected.gross_notional), str(fact.decision.projected.net_notional),
             str(fact.decision.projected.instrument_notional), str(fact.decision.projected.available_margin),
             str(fact.decision.projected.leverage), str(fact.decision.projected.daily_loss), str(fact.decision.projected.drawdown_ratio),
             scope.correlation_id, fact.observed_at, fact.occurred_at),
        )
        if cursor.fetchone() is not None:
            return True
        cursor.execute(
            """SELECT id, command_id, economic_order_id, tenant_id, credential_id,
                      account_scope, instrument_id, market_type, policy_snapshot_id,
                      risk_input_snapshot_id, decision, decision_fingerprint
                 FROM qd_risk_decisions
                WHERE id = %s OR decision_fingerprint = %s
                ORDER BY id FOR UPDATE""",
            (fact.decision_id, fact.decision_fingerprint),
        )
        rows = cursor.fetchall()
        expected = (fact.decision_id, scope.command_id, scope.economic_order_id, scope.tenant_id,
                    scope.credential_id, scope.account_scope, scope.instrument_id, scope.market_type,
                    fact.policy_snapshot.snapshot_id, fact.input_snapshot.snapshot_id,
                    fact.decision_status, fact.decision_fingerprint)
        if len(rows) != 1 or tuple(str(_row(rows[0], i, key)) if i in (0, 1, 2, 8, 9) else _row(rows[0], i, key) for i, key in enumerate(
            ("id", "command_id", "economic_order_id", "tenant_id", "credential_id", "account_scope", "instrument_id", "market_type", "policy_snapshot_id", "risk_input_snapshot_id", "decision", "decision_fingerprint")
        )) != expected:
            raise RiskEnforcementConflict("decision identity names different immutable facts")
        return False

    def _insert_or_verify_reservation(self, cursor: Cursor, fact: RiskReservationFact) -> bool:
        decision, scope, demand = fact.decision, fact.decision.scope, fact.demand
        cursor.execute(
            """
            INSERT INTO qd_risk_reservations (
                id, command_id, economic_order_id, tenant_id, credential_id,
                account_scope, reservation_kind, currency, reserved_notional,
                reserved_margin, reserved_position_qty, limits_snapshot_json,
                risk_input_hash, state, expires_at, version, decision_id,
                instrument_id, market_type, action, policy_snapshot_id,
                risk_input_snapshot_id, enforcement_contract_version,
                reserved_gross_notional, reserved_net_notional, reserved_instrument_notional, correlation_id
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s::jsonb,%s,'ACTIVE',%s,0,%s,%s,%s,%s,%s,%s,'hard-risk-enforcement-v1',%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
            """,
            (fact.reservation_id, scope.command_id, scope.economic_order_id, scope.tenant_id,
             scope.credential_id, scope.account_scope, fact.reservation_kind,
             demand.valuation_currency, str(demand.gross_notional), str(demand.margin),
             _policy_legacy_json(decision.policy_snapshot), decision.input_snapshot.input_hash,
             fact.expires_at, decision.decision_id, scope.instrument_id, scope.market_type,
             scope.action.value, decision.policy_snapshot.snapshot_id, decision.input_snapshot.snapshot_id,
             str(demand.gross_notional), str(demand.net_notional), str(demand.instrument_notional), scope.correlation_id),
        )
        if cursor.fetchone() is not None:
            return True
        cursor.execute(
            """
            SELECT id, decision_id, command_id, economic_order_id, tenant_id,
                   credential_id, account_scope, reservation_kind, currency,
                   reserved_notional, reserved_margin, risk_input_hash, state,
                   reserved_gross_notional, reserved_net_notional,
                   reserved_instrument_notional, correlation_id
              FROM qd_risk_reservations
             WHERE id = %s
                OR (command_id = %s AND reservation_kind = %s AND state = 'ACTIVE')
             ORDER BY id FOR UPDATE
            """,
            (fact.reservation_id, scope.command_id, fact.reservation_kind),
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise RiskEnforcementConflict("reservation identities conflict with different rows")
        row = rows[0]
        observed = (
            str(_row(row, 0, "id")), str(_row(row, 1, "decision_id")), str(_row(row, 2, "command_id")),
            str(_row(row, 3, "economic_order_id")), _row(row, 4, "tenant_id"),
            _row(row, 5, "credential_id"), _row(row, 6, "account_scope"),
            _row(row, 7, "reservation_kind"), _row(row, 8, "currency"),
            str(_row(row, 9, "reserved_notional")), str(_row(row, 10, "reserved_margin")),
            _row(row, 11, "risk_input_hash"), _row(row, 12, "state"),
            str(_row(row, 13, "reserved_gross_notional")),
            str(_row(row, 14, "reserved_net_notional")),
            str(_row(row, 15, "reserved_instrument_notional")),
            _row(row, 16, "correlation_id"),
        )
        expected = (
            fact.reservation_id, decision.decision_id, scope.command_id, scope.economic_order_id,
            scope.tenant_id, scope.credential_id, scope.account_scope, fact.reservation_kind,
            demand.valuation_currency, str(demand.gross_notional), str(demand.margin),
            decision.input_snapshot.input_hash, "ACTIVE",
            str(demand.gross_notional), str(demand.net_notional),
            str(demand.instrument_notional), scope.correlation_id,
        )
        if observed != expected:
            raise RiskEnforcementConflict("reservation identity names different immutable facts")
        return False
