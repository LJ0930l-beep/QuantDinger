"""Unit coverage for caller-owned independent durable-risk V2 persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
import re
import unittest

from tests.pr11_contract_loader import load_pr11_contracts


m = load_pr11_contracts()
risk = m.hard_risk
contracts = m.durable_risk_v2
repository_module = m.durable_risk_repository
entry = m.entry
entry_v2 = m.entry_v2
OrderAction = m.order.OrderAction
Actor = m.order.Actor
RiskEffect = m.order.RiskEffect
QuoteAmount = m.decimals.QuoteAmount
Quantity = m.decimals.Quantity


def graph(action=OrderAction.OPEN):
    reducing = action not in (OrderAction.OPEN, OrderAction.INCREASE)
    intent = entry_v2.CanonicalEconomicIntentV2(
        side=entry.OrderSide.BUY,
        quantity=None if reducing else Quantity("1"),
        quantity_semantics=None if reducing else entry_v2.QuantitySemantics.ABSOLUTE,
        execution_kind=entry.ExecutionKind.MARKET,
        reduce_only=reducing,
        target_position_id="position-1" if reducing else None,
        close_quantity=Quantity("1") if reducing else None,
    )
    specification = entry_v2.CanonicalEntryRequestV2(
        1, 2, "account-1", "BTCUSDT", "swap", action, intent,
        entry.EntryActorContext(Actor.HUMAN, "human-1", entry.EntrySource.REST),
        RiskEffect.REDUCE_RISK if reducing else RiskEffect.INCREASE_RISK,
        "case-1", "corr-1", datetime(2026, 7, 29, tzinfo=timezone.utc), entry.EntryMode.PAPER,
    )
    return entry_v2.DurableEntryGraphV2(
        "11111111-1111-1111-1111-111111111111", specification,
        entry_v2.EconomicOrderSubject("22222222-2222-2222-2222-222222222222"),
    )


def policy():
    return risk.RiskLimitPolicy("policy-1", "USDT", QuoteAmount("1000"), QuoteAmount("700"), QuoteAmount("600"), "4", QuoteAmount("100"), QuoteAmount("100"), "0.20")


def exposure():
    return risk.RiskExposureSnapshot("account-1", "BTCUSDT", "USDT", "100", "100", "100", "800", "500", "500", "0", m.order.ReconciliationHealth.HEALTHY, risk.MarketDataHealth.FRESH, True)


def switches():
    state = risk.KillSwitchState(1, False)
    return risk.KillSwitchSnapshot(state, state, state)


def request(action=OrderAction.OPEN):
    increasing = action in (OrderAction.OPEN, OrderAction.INCREASE)
    return risk.HardRiskRequest(action, Actor.HUMAN, None, "100" if increasing else "0", "100" if increasing else "0", "100" if increasing else "0", "25" if increasing else "0")


def facts(action=OrderAction.OPEN, with_reservation=True):
    return contracts.build_durable_risk_facts_v2(
        graph(action), policy=policy(), exposure=exposure(), kill_switches=switches(), request=request(action),
        observed_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        reservation_demand=risk.RiskReservationDemand("ignored-input-id", "account-1", "BTCUSDT", "USDT", "100", "100", "100", "25") if with_reservation else None,
    )


class FakeCursor:
    def __init__(self, entry_row, *, fail=False):
        self.entry_row = entry_row
        self.fail = fail
        self.closed = False
        self.executed = []
        self._next = None

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))
        if self.fail:
            raise RuntimeError("injected driver error")
        if "qd_durable_entry_specifications" in sql:
            self._next = self.entry_row
        elif "INSERT INTO" in sql:
            self._next = ("created",)
        else:
            self._next = None

    def fetchone(self):
        result, self._next = self._next, None
        return result

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = self.rollbacks = 0
    def cursor(self): return self.cursor_value
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class ReplayCursor(FakeCursor):
    """Small SQL-shaped fake proving replay compares persisted V2 facts."""
    def __init__(self, entry_row):
        super().__init__(entry_row)
        self.values = {}
        self.replay = False

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))
        normalized = " ".join(sql.split())
        if "qd_durable_entry_specifications" in normalized:
            self._next = self.entry_row
            return
        insert = re.search(r"INSERT INTO (qd_durable_risk_[a-z_]+) \((.*?)\) VALUES", normalized)
        if insert:
            table = insert.group(1)
            columns = tuple(item.strip() for item in insert.group(2).split(","))
            self.values.setdefault(table, dict(zip(columns, params)))
            self._next = None if self.replay else ("created",)
            return
        selected = re.search(r"SELECT (.*?) FROM (qd_durable_risk_[a-z_]+)", normalized)
        if selected:
            columns = tuple(item.strip() for item in selected.group(1).split(","))
            values = self.values[selected.group(2)]
            self._next = tuple(values[column] for column in columns)
            return
        self._next = None


class DurableRiskV2RepositoryTests(unittest.TestCase):
    def _entry_row(self, decision):
        spec = decision.scope.graph.specification
        return {
            "contract_version": "canonical-entry-v2", "command_id": decision.scope.command_id,
            "tenant_id": spec.tenant_id, "credential_id": spec.credential_id,
            "account_scope": spec.account_scope, "instrument_id": spec.instrument_id,
            "market_type": spec.market_type, "action": spec.action.value,
            "risk_effect": spec.risk_effect.value, "economic_order_id": decision.scope.economic_order_id,
            "economic_fingerprint": spec.economic_fingerprint, "request_fingerprint": spec.request_fingerprint,
            "actor_type": spec.actor.actor_type.value, "actor_id": spec.actor.actor_id,
            "source": spec.actor.entry_source.value, "mode": spec.mode.value,
            "correlation_id": spec.correlation_id, "occurred_at": spec.occurred_at,
        }

    def test_allowed_open_persists_v2_only_without_transaction_control(self):
        policy_fact, input_fact, decision, reservation = facts()
        connection = FakeConnection(FakeCursor(self._entry_row(decision)))
        result = repository_module.DurableRiskEnforcementRepositoryV2().persist_durable_risk(connection, policy_snapshot=policy_fact, input_snapshot=input_fact, decision=decision, reservation=reservation)
        self.assertEqual(contracts.DurableRiskPersistDisposition.CREATED, result.disposition)
        self.assertEqual(reservation.reservation_id, result.reservation_id)
        self.assertEqual(0, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        self.assertTrue(connection.cursor_value.closed)
        sql = "\n".join(item[0] for item in connection.cursor_value.executed)
        self.assertIn("qd_durable_entry_specifications", sql)
        self.assertIn("qd_durable_risk_decisions", sql)
        self.assertNotIn("qd_order_commands", sql)
        self.assertNotIn("qd_economic_orders", sql)

    def test_reduce_has_decision_but_never_reservation(self):
        policy_fact, input_fact, decision, reservation = facts(OrderAction.CLOSE, with_reservation=False)
        self.assertTrue(decision.decision.allowed)
        self.assertIsNone(reservation)

    def test_denied_decision_cannot_create_reservation(self):
        policy_fact, input_fact, decision, _ = facts(with_reservation=False)
        denied = risk.HardRiskDecision(policy().policy_version, "account-1", "BTCUSDT", "USDT", OrderAction.OPEN, RiskEffect.INCREASE_RISK, False, (risk.RiskRejectionCode.KILL_SWITCH,), decision.decision.projected)
        denied_fact = contracts.DurableRiskDecisionFactV2(decision.scope, policy_fact, input_fact, denied)
        demand = risk.RiskReservationDemand("ignored", "account-1", "BTCUSDT", "USDT", "1", "1", "1", "1")
        with self.assertRaises(contracts.DurableRiskEnforcementV2Error):
            contracts.DurableRiskReservationFactV2(denied_fact, demand)

    def test_driver_error_is_typed_and_never_controls_transaction(self):
        policy_fact, input_fact, decision, reservation = facts()
        connection = FakeConnection(FakeCursor(self._entry_row(decision), fail=True))
        with self.assertRaises(contracts.DurableRiskRepositoryError):
            repository_module.DurableRiskEnforcementRepositoryV2().persist_durable_risk(connection, policy_snapshot=policy_fact, input_snapshot=input_fact, decision=decision, reservation=reservation)
        self.assertEqual((0, 0), (connection.commits, connection.rollbacks))
        self.assertTrue(connection.cursor_value.closed)

    def test_exact_replay_is_typed_and_any_audit_fact_change_is_conflict(self):
        policy_fact, input_fact, decision, reservation = facts()
        cursor = ReplayCursor(self._entry_row(decision))
        connection = FakeConnection(cursor)
        repository = repository_module.DurableRiskEnforcementRepositoryV2()
        self.assertEqual(contracts.DurableRiskPersistDisposition.CREATED, repository.persist_durable_risk(connection, policy_snapshot=policy_fact, input_snapshot=input_fact, decision=decision, reservation=reservation).disposition)
        cursor.replay = True
        self.assertEqual(contracts.DurableRiskPersistDisposition.REPLAYED, repository.persist_durable_risk(connection, policy_snapshot=policy_fact, input_snapshot=input_fact, decision=decision, reservation=reservation).disposition)
        # Same economic scope but a different correlation audit fact has the
        # same deterministic locator and must fail exact replay comparison.
        altered_graph = graph()
        altered_spec = entry_v2.CanonicalEntryRequestV2(
            altered_graph.specification.tenant_id, altered_graph.specification.credential_id,
            altered_graph.specification.account_scope, altered_graph.specification.instrument_id,
            altered_graph.specification.market_type, altered_graph.specification.action,
            altered_graph.specification.economic_intent, altered_graph.specification.actor,
            altered_graph.specification.risk_effect, altered_graph.specification.idempotency_key,
            "corr-other", altered_graph.specification.occurred_at, altered_graph.specification.mode,
        )
        altered = entry_v2.DurableEntryGraphV2(altered_graph.command_id, altered_spec, altered_graph.subject)
        p2, i2, d2, r2 = contracts.build_durable_risk_facts_v2(altered, policy=policy(), exposure=exposure(), kill_switches=switches(), request=request(), observed_at=datetime(2026, 7, 29, tzinfo=timezone.utc), reservation_demand=risk.RiskReservationDemand("ignored", "account-1", "BTCUSDT", "USDT", "100", "100", "100", "25"))
        cursor.entry_row = self._entry_row(d2)
        with self.assertRaises(contracts.DurableRiskConflict):
            repository.persist_durable_risk(connection, policy_snapshot=p2, input_snapshot=i2, decision=d2, reservation=r2)
        self.assertEqual((0, 0), (connection.commits, connection.rollbacks))

    def test_source_has_no_legacy_tables_or_transaction_control(self):
        source = Path(repository_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".commit(", source)
        self.assertNotIn(".rollback(", source)
        self.assertNotIn("qd_order_commands", source)
        self.assertNotIn("qd_economic_orders", source)


if __name__ == "__main__":
    unittest.main()
