"""PostgreSQL replay and concurrency contracts for hard-risk enforcement."""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from tests.pr10_contract_loader import load_pr10_contracts


modules = load_pr10_contracts()
decimal, contracts, risk = modules.decimal, modules.contracts, modules.hard_risk
enforcement, repository = modules.enforcement, modules.repository
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INIT_SQL = MIGRATIONS / "init.sql"
INCREMENTAL = tuple(sorted(MIGRATIONS.glob("2026072[2-5]_*.sql")))


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class RiskEnforcementRepositoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                for migration in INCREMENTAL:
                    cursor.execute(migration.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()

    def _graph(self):
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        suffix = uuid4().hex
        try:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO qd_users(username, password_hash) VALUES (%s, 'risk-test') RETURNING id", (f"risk-enforcement-{suffix}",))
                user_id = cursor.fetchone()[0]
                cursor.execute("INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) VALUES (%s, %s, '{}') RETURNING id", (user_id, f"risk-enforcement-{suffix}"))
                credential_id = cursor.fetchone()[0]
                instrument_snapshot_id = str(uuid4())
                cursor.execute(
                    "INSERT INTO qd_instrument_rule_snapshots (id, exchange, market_type, instrument_id, rule_version, tick_size, quantity_step, minimum_quantity, minimum_notional, price_scale, quantity_scale, rounding_policy_version) VALUES (%s,%s,'swap','BTCUSDT','v1','0.01','0.001','0','0',2,3,'v1')",
                    (instrument_snapshot_id, f"risk-{suffix}"),
                )
                command_id, intent_id, economic_order_id = str(uuid4()), str(uuid4()), str(uuid4())
                cursor.execute(
                    "INSERT INTO qd_order_commands (id, tenant_id, user_id, credential_id, actor_type, actor_id, source, action, account_scope, request_fingerprint, idempotency_key, status) VALUES (%s,%s,%s,%s,'STRATEGY','risk-test','RISK_TEST','OPEN','account-a','request',%s,'ACCEPTED')",
                    (command_id, user_id, user_id, credential_id, f"risk-key-{suffix}"),
                )
                cursor.execute(
                    "INSERT INTO qd_order_intents_v2 (id, command_id, tenant_id, credential_id, economic_order_id, intent_version, account_scope, instrument_id, market_type, side, order_type, execution_algo, target_quantity, instrument_rule_snapshot_id, instrument_rule_version, rounding_mode, payload_hash) VALUES (%s,%s,%s,%s,%s,1,'account-a','BTCUSDT','swap','BUY','LIMIT','DIRECT','1',%s,'v1','ROUND_DOWN','payload')",
                    (intent_id, command_id, user_id, credential_id, economic_order_id, instrument_snapshot_id),
                )
                cursor.execute(
                    "INSERT INTO qd_economic_orders (id, intent_id, tenant_id, user_id, credential_id, account_scope, instrument_id, market_type, state, target_quantity) VALUES (%s,%s,%s,%s,%s,'account-a','BTCUSDT','swap','CREATED','1')",
                    (economic_order_id, intent_id, user_id, user_id, credential_id),
                )
            connection.commit()
            return command_id, economic_order_id, user_id, credential_id
        finally:
            connection.close()

    def _facts(self, graph):
        command_id, economic_order_id, tenant_id, credential_id = graph
        scope = enforcement.RiskEnforcementScope(command_id, economic_order_id, tenant_id, credential_id, "account-a", "BTCUSDT", "swap", contracts.OrderAction.OPEN, contracts.Actor.STRATEGY)
        policy = risk.RiskLimitPolicy("policy-1", "USDT", decimal.QuoteAmount("1000"), decimal.QuoteAmount("800"), decimal.QuoteAmount("900"), "5", decimal.QuoteAmount("10"), decimal.QuoteAmount("100"), "0.50")
        exposure = risk.RiskExposureSnapshot("account-a", "BTCUSDT", "USDT", "100", "100", "100", "900", "1000", "1000", "0", contracts.ReconciliationHealth.HEALTHY, risk.MarketDataHealth.FRESH, True)
        request = risk.HardRiskRequest(contracts.OrderAction.OPEN, contracts.Actor.STRATEGY, None, "10", "10", "10", "2")
        disabled = risk.KillSwitchState(0, False)
        evaluated = risk.evaluate_hard_risk(policy=policy, snapshot=exposure, request=request, kill_switches=risk.KillSwitchSnapshot(disabled, disabled, disabled))
        policy_fact = enforcement.RiskPolicySnapshotFact(str(uuid4()), scope, policy)
        input_fact = enforcement.RiskInputSnapshotFact(str(uuid4()), scope, "input-1", exposure)
        decision = enforcement.RiskDecisionFact(str(uuid4()), scope, policy_fact, input_fact, evaluated)
        reservation = enforcement.build_risk_reservation_fact(reservation_id=str(uuid4()), decision=decision, request=request, reservation_kind="OPEN_CAPACITY")
        return policy_fact, input_fact, decision, reservation

    def test_two_connections_create_one_enforcement_graph_and_one_replay(self):
        import psycopg2

        facts = self._facts(self._graph())
        barrier, outcomes, failures = threading.Barrier(2), [], []

        def persist_once():
            connection = psycopg2.connect(os.environ["DATABASE_URL"])
            try:
                barrier.wait(timeout=10)
                result = repository.RiskEnforcementRepository().persist(
                    connection, policy_snapshot=facts[0], input_snapshot=facts[1], decision=facts[2], reservation=facts[3],
                )
                outcomes.append(result.disposition)
            except Exception as exc:
                failures.append(exc)
            finally:
                connection.close()

        workers = [threading.Thread(target=persist_once, daemon=True) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertCountEqual(outcomes, [repository.RiskEnforcementDisposition.CREATED, repository.RiskEnforcementDisposition.REPLAYED])

    def test_mixed_policy_snapshot_rolls_back_before_any_partial_graph(self):
        import psycopg2

        facts = self._facts(self._graph())
        other = self._facts(self._graph())[0]
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with self.assertRaises(repository.RiskEnforcementConflict):
                repository.RiskEnforcementRepository().persist(
                    connection, policy_snapshot=other, input_snapshot=facts[1], decision=facts[2], reservation=facts[3],
                )
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM qd_risk_decisions WHERE id = %s", (facts[2].decision_id,))
                self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            connection.rollback()
            connection.close()
