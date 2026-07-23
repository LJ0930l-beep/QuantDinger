"""PostgreSQL contract coverage for PR-03; CI supplies DATABASE_URL."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tests.pr03_contract_loader import load_pr03_contracts


modules = load_pr03_contracts()
c = modules.contracts
d = modules.decimal_values
o = modules.order_contracts
repository_module = modules.repository


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class CommandIntentRepositoryPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg2
        self.connection = psycopg2.connect(os.environ["DATABASE_URL"])
        self.connection.autocommit = False
        self.repo = repository_module.CommandIntentRepository()
        self.suffix = uuid4().hex
        self.ids: list[str] = []
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
                (f"pr03_{self.suffix}", "test-only"),
            )
            self.user_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) "
                "VALUES (%s, %s, %s) RETURNING id",
                (self.user_id, f"pr03-{self.suffix}", "{}"),
            )
            self.credential_id = cursor.fetchone()[0]
            self.snapshot_id = str(uuid4())
            cursor.execute(
                "INSERT INTO qd_instrument_rule_snapshots "
                "(id, exchange, market_type, instrument_id, rule_version, tick_size, quantity_step, "
                "minimum_quantity, minimum_notional, price_scale, quantity_scale, rounding_policy_version) "
                "VALUES (%s, %s, 'usdm', 'BTC-USDT', 'v1', '0.01', '0.001', '0', '0', 2, 3, 'v1')",
                (self.snapshot_id, f"pr03-{self.suffix}"),
            )
        self.connection.commit()

    def tearDown(self):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("DELETE FROM qd_risk_reservations WHERE tenant_id = %s", (self.user_id,))
                cursor.execute("DELETE FROM qd_economic_orders WHERE tenant_id = %s", (self.user_id,))
                cursor.execute("DELETE FROM qd_order_intents_v2 WHERE tenant_id = %s", (self.user_id,))
                cursor.execute("DELETE FROM qd_order_commands WHERE tenant_id = %s", (self.user_id,))
                cursor.execute("DELETE FROM qd_instrument_rule_snapshots WHERE id = %s", (self.snapshot_id,))
                cursor.execute("DELETE FROM qd_exchange_credentials WHERE id = %s", (self.credential_id,))
                cursor.execute("DELETE FROM qd_users WHERE id = %s", (self.user_id,))
            self.connection.commit()
        finally:
            self.connection.close()

    def _graph(self, *, idempotency_key: str = "idem-1", command_id=None):
        command = c.OrderCommand(
            command_id=command_id or uuid4(), tenant_id=self.user_id, user_id=self.user_id,
            credential_id=self.credential_id, actor_type=o.Actor.STRATEGY, actor_id="strategy:1",
            source="strategy_v2", action=o.OrderAction.OPEN, account_scope="account-a",
            request_payload={"test": "postgres"}, idempotency_key=idempotency_key,
        )
        intent = c.OrderIntent(
            intent_id=uuid4(), economic_order_id=uuid4(), command_id=command.command_id,
            tenant_id=self.user_id, credential_id=self.credential_id, account_scope="account-a",
            exchange_id=f"pr03-{self.suffix}", instrument_id="BTC-USDT", market_type="usdm",
            side="BUY", target_quantity=d.Quantity("1"), instrument_rule_snapshot_id=self.snapshot_id,
            instrument_rule_version="v1", order_type="LIMIT", execution_algo="DIRECT",
            rounding_mode="ROUND_DOWN", limit_price=d.Price("100"),
        )
        return c.CommandGraph(command, intent)

    def test_atomic_graph_accept_replay_and_conflict_use_real_unique_constraints(self):
        graph = self._graph()
        created = self.repo.accept_command_graph(self.connection, graph)
        replay = self.repo.accept_command_graph(self.connection, graph)
        self.assertEqual(created.disposition, c.CommandGraphDisposition.CREATED)
        self.assertEqual(replay.disposition, c.CommandGraphDisposition.REPLAYED)
        self.assertEqual(replay.economic_order_id, graph.intent.economic_order_id)
        conflict = self._graph(idempotency_key=graph.command.idempotency_key)
        with self.assertRaises(c.IdempotencyConflict):
            self.repo.accept_command_graph(self.connection, conflict)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM qd_order_intents_v2 WHERE command_id = %s", (graph.command.command_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT state FROM qd_economic_orders WHERE id = %s", (graph.intent.economic_order_id,))
            self.assertEqual(cursor.fetchone()[0], "CREATED")

    def test_snapshot_mismatch_rolls_back_and_reservation_lifecycle_is_cas(self):
        bad = self._graph()
        bad_intent = c.OrderIntent(
            intent_id=bad.intent.intent_id, economic_order_id=bad.intent.economic_order_id,
            command_id=bad.command.command_id, tenant_id=bad.intent.tenant_id,
            credential_id=bad.intent.credential_id, account_scope=bad.intent.account_scope,
            exchange_id="wrong-exchange", instrument_id=bad.intent.instrument_id,
            market_type=bad.intent.market_type, side=bad.intent.side,
            target_quantity=bad.intent.target_quantity, instrument_rule_snapshot_id=bad.intent.instrument_rule_snapshot_id,
            instrument_rule_version=bad.intent.instrument_rule_version, order_type=bad.intent.order_type,
            execution_algo=bad.intent.execution_algo, rounding_mode=bad.intent.rounding_mode,
            limit_price=bad.intent.limit_price,
        )
        with self.assertRaises(c.InstrumentRuleSnapshotMismatch):
            self.repo.accept_command_graph(self.connection, c.CommandGraph(bad.command, bad_intent))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM qd_order_commands WHERE id = %s", (bad.command.command_id,))
            self.assertEqual(cursor.fetchone()[0], 0)

        graph = self._graph(idempotency_key="idem-2")
        self.repo.accept_command_graph(self.connection, graph)
        reservation = c.RiskReservation(
            reservation_id=uuid4(), command_id=graph.command.command_id,
            economic_order_id=graph.intent.economic_order_id, tenant_id=self.user_id,
            credential_id=self.credential_id, account_scope="account-a", reservation_kind="MARGIN",
            currency="USDT", reserved_notional=d.QuoteAmount("100"), reserved_margin=d.QuoteAmount("10"),
            reserved_position_qty=d.Quantity("1"), limits_snapshot={"max": "100"},
            risk_input_hash="c" * 64, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self.assertEqual(self.repo.create_reservation(self.connection, reservation).state, c.ReservationState.ACTIVE)
        replay = self.repo.create_reservation(self.connection, reservation)
        self.assertEqual(replay.disposition, c.ReservationTransitionDisposition.IDEMPOTENT_REPLAY)
        released = self.repo.release_reservation(self.connection, reservation.reservation_id, 0)
        self.assertEqual(released.state, c.ReservationState.RELEASED)
        self.assertEqual(self.repo.release_reservation(self.connection, reservation.reservation_id, 0).disposition,
                         c.ReservationTransitionDisposition.IDEMPOTENT_REPLAY)
        with self.assertRaises(c.ReservationStateConflict):
            self.repo.consume_reservation(self.connection, reservation.reservation_id, 1)


if __name__ == "__main__":
    unittest.main()
