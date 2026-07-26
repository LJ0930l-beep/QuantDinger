"""Focused atomicity contracts for the PR-10 enforcement repository."""

from __future__ import annotations

from uuid import uuid4
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from tests.pr10_contract_loader import load_pr10_contracts


modules = load_pr10_contracts()
decimal, contracts, risk = modules.decimal, modules.contracts, modules.hard_risk
enforcement, repository = modules.enforcement, modules.repository


class _Cursor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []
        self.closed = False

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def fetchone(self):
        return self.responses.pop(0) if self.responses else None

    def fetchall(self):
        return self.responses.pop(0) if self.responses else []

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _facts():
    scope = enforcement.RiskEnforcementScope(
        str(uuid4()), str(uuid4()), 1, 2, "account-a", "BTCUSDT", "swap",
        contracts.OrderAction.OPEN, contracts.Actor.STRATEGY,
        "strategy-a", "correlation-a",
    )
    policy = risk.RiskLimitPolicy(
        "policy-1", "USDT", decimal.QuoteAmount("1000"), decimal.QuoteAmount("800"),
        decimal.QuoteAmount("900"), "5", decimal.QuoteAmount("10"), decimal.QuoteAmount("100"), "0.50",
    )
    exposure = risk.RiskExposureSnapshot(
        "account-a", "BTCUSDT", "USDT", "100", "100", "100", "900", "1000", "1000", "0",
        contracts.ReconciliationHealth.HEALTHY, risk.MarketDataHealth.FRESH, True,
    )
    request = risk.HardRiskRequest(contracts.OrderAction.OPEN, contracts.Actor.STRATEGY, None, "10", "10", "10", "2")
    disabled = risk.KillSwitchState(0, False)
    decision_value = risk.evaluate_hard_risk(
        policy=policy, snapshot=exposure, request=request,
        kill_switches=risk.KillSwitchSnapshot(disabled, disabled, disabled),
    )
    policy_fact = enforcement.RiskPolicySnapshotFact(str(uuid4()), scope, policy)
    disabled = risk.KillSwitchState(0, False)
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    input_fact = enforcement.RiskInputSnapshotFact(str(uuid4()), scope, "input-1", exposure, risk.KillSwitchSnapshot(disabled, disabled, disabled), now, now)
    decision = enforcement.RiskDecisionFact(str(uuid4()), scope, policy_fact, input_fact, decision_value, now, now)
    reservation = enforcement.build_risk_reservation_fact(
        reservation_id=str(uuid4()), decision=decision, request=request, reservation_kind="OPEN_CAPACITY",
    )
    return policy_fact, input_fact, decision, reservation


class RiskEnforcementRepositoryTests(unittest.TestCase):
    def test_persist_writes_one_atomic_graph_when_all_insertions_succeed(self):
        policy, inputs, decision, reservation = _facts()
        command_row = (
            "OPEN", "STRATEGY", "strategy-a", 1, 2, "account-a",
            1, 2, "account-a", "BTCUSDT", "swap",
        )
        cursor = _Cursor([
            command_row, (policy.snapshot_id,), (inputs.snapshot_id,),
            (decision.decision_id,), (reservation.reservation_id,),
            (policy.snapshot_id,), (inputs.snapshot_id,),
            (decision.decision_id,), (reservation.reservation_id,),
        ])
        connection = _Connection(cursor)
        result = repository.RiskEnforcementRepository().persist(
            connection, policy_snapshot=policy, input_snapshot=inputs, decision=decision, reservation=reservation,
        )
        self.assertEqual(result.disposition, repository.RiskEnforcementDisposition.CREATED)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(cursor.closed)
        self.assertIn("qd_risk_decisions", "\n".join(query for query, _ in cursor.queries))
        self.assertIn("qd_risk_reservations", "\n".join(query for query, _ in cursor.queries))

    def test_mixed_graph_fails_before_any_database_side_effect(self):
        policy, inputs, decision, reservation = _facts()
        other_policy, _, _, _ = _facts()
        cursor = _Cursor([])
        connection = _Connection(cursor)
        with self.assertRaises(repository.RiskEnforcementConflict):
            repository.RiskEnforcementRepository().persist(
                connection, policy_snapshot=other_policy, input_snapshot=inputs, decision=decision, reservation=reservation,
            )
        self.assertEqual(cursor.queries, [])
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)

    def test_reservation_replay_compares_database_numeric_scale_by_decimal_value(self):
        _, inputs, decision, reservation = _facts()
        scope, demand = decision.scope, reservation.demand
        padded = Decimal("10.000000000000000000")
        row = (
            reservation.reservation_id, decision.decision_id, scope.command_id,
            scope.economic_order_id, scope.tenant_id, scope.credential_id,
            scope.account_scope, reservation.reservation_kind, demand.valuation_currency,
            padded, Decimal("2.000000000000000000"), inputs.input_hash, "ACTIVE",
            padded, padded, padded, scope.correlation_id,
        )
        cursor = _Cursor([None, [row]])
        replayed = repository.RiskEnforcementRepository()._insert_or_verify_reservation(
            cursor, reservation,
        )
        self.assertFalse(replayed)
