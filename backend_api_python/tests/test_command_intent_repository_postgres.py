"""PostgreSQL contract coverage for PR-03; CI supplies DATABASE_URL."""

from __future__ import annotations

import os
import hashlib
import threading
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tests.pr03_contract_loader import load_pr03_contracts


modules = load_pr03_contracts()
c = modules.contracts
d = modules.decimal_values
o = modules.order_contracts
repository_module = modules.repository
DEFAULT_REPLAY_TOKEN = "-".join(("idem", "postgres", "one"))


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

    def _graph(self, *, replay_token: str = DEFAULT_REPLAY_TOKEN, command_id=None):
        command = c.OrderCommand(
            command_id=command_id or uuid4(), tenant_id=self.user_id, user_id=self.user_id,
            credential_id=self.credential_id, actor_type=o.Actor.STRATEGY, actor_id="strategy:1",
            source="strategy_v2", action=o.OrderAction.OPEN, account_scope="account-a",
            request_payload={"test": "postgres"}, idempotency_key=replay_token,
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

    def _reservation(self, graph, *, tag: str, expires_at=None):
        return c.RiskReservation(
            reservation_id=uuid4(), command_id=graph.command.command_id,
            economic_order_id=graph.intent.economic_order_id, tenant_id=self.user_id,
            credential_id=self.credential_id, account_scope="account-a", reservation_kind="MARGIN",
            currency="USDT", reserved_notional=d.QuoteAmount("100"), reserved_margin=d.QuoteAmount("10"),
            reserved_position_qty=d.Quantity("1"), limits_snapshot={"fixture": tag},
            risk_input_hash=hashlib.sha256(tag.encode("ascii")).hexdigest(), expires_at=expires_at,
        )

    def _parallel(self, first, second):
        import psycopg2

        barrier = threading.Barrier(2, timeout=10)
        results = [None, None]

        def run(index, operation):
            connection = psycopg2.connect(os.environ["DATABASE_URL"])
            connection.autocommit = False
            try:
                barrier.wait()
                results[index] = operation(connection)
            except BaseException as exc:  # assertions inspect typed result below
                results[index] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(0, first)), threading.Thread(target=run, args=(1, second))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive(), "concurrent repository test timed out")
        return results

    def test_atomic_graph_accept_replay_and_conflict_use_real_unique_constraints(self):
        graph = self._graph()
        created = self.repo.accept_command_graph(self.connection, graph)
        replay = self.repo.accept_command_graph(self.connection, graph)
        self.assertEqual(created.disposition, c.CommandGraphDisposition.CREATED)
        self.assertEqual(replay.disposition, c.CommandGraphDisposition.REPLAYED)
        self.assertEqual(replay.economic_order_id, graph.intent.economic_order_id)
        conflict = self._graph(replay_token=graph.command.idempotency_key)
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

        self.assertEqual(
            self.repo.accept_command_graph(self.connection, self._graph(replay_token="-".join(("idem", "after", "rollback")))).disposition,
            c.CommandGraphDisposition.CREATED,
        )

        graph = self._graph(replay_token="-".join(("idem", "two")))
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

    def _assert_parallel_same_graph_has_one_complete_graph(self, graph):
        results = self._parallel(
            lambda connection: repository_module.CommandIntentRepository().accept_command_graph(connection, graph),
            lambda connection: repository_module.CommandIntentRepository().accept_command_graph(connection, graph),
        )
        self.assertTrue(all(isinstance(result, (c.CommandGraphResult, c.IdempotencyConflict)) for result in results))
        successful = [result for result in results if isinstance(result, c.CommandGraphResult)]
        conflicts = [result for result in results if isinstance(result, c.IdempotencyConflict)]
        created = [result for result in successful if result.disposition is c.CommandGraphDisposition.CREATED]
        replayed = [result for result in successful if result.disposition is c.CommandGraphDisposition.REPLAYED]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(successful) + len(conflicts), 2)
        if replayed:
            self.assertEqual(len(replayed), 1)
            self.assertEqual(conflicts, [])
            replay = replayed[0]
        else:
            self.assertEqual(len(successful), 1)
            self.assertEqual(len(conflicts), 1)
            replay = self.repo.accept_command_graph(self.connection, graph)
            self.assertEqual(replay.disposition, c.CommandGraphDisposition.REPLAYED)
        self.assertEqual(replay.command_id, created[0].command_id)
        self.assertEqual(replay.intent_id, created[0].intent_id)
        self.assertEqual(replay.economic_order_id, created[0].economic_order_id)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM qd_order_commands WHERE id = %s", (graph.command.command_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM qd_order_intents_v2 WHERE command_id = %s", (graph.command.command_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM qd_economic_orders WHERE intent_id = %s", (graph.intent.intent_id,))
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_two_connections_create_one_graph_or_report_typed_conflict_twenty_times(self):
        for attempt in range(20):
            with self.subTest(attempt=attempt):
                graph = self._graph(replay_token=f"parallel-same-{attempt}-{self.suffix}")
                self._assert_parallel_same_graph_has_one_complete_graph(graph)

    def test_two_connections_same_key_different_graph_leave_one_graph(self):
        first = self._graph(replay_token="-".join(("parallel", "different", "graph")))
        second = self._graph(replay_token="-".join(("parallel", "different", "graph")))
        results = self._parallel(
            lambda connection: repository_module.CommandIntentRepository().accept_command_graph(connection, first),
            lambda connection: repository_module.CommandIntentRepository().accept_command_graph(connection, second),
        )
        self.assertTrue(all(isinstance(result, (c.CommandGraphResult, c.IdempotencyConflict)) for result in results))
        self.assertEqual(sum(isinstance(result, c.CommandGraphResult) for result in results), 1)
        self.assertEqual(sum(isinstance(result, c.IdempotencyConflict) for result in results), 1)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM qd_order_commands WHERE tenant_id = %s AND source = %s AND idempotency_key = %s",
                (self.user_id, first.command.source, first.command.idempotency_key),
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_same_command_id_different_idempotency_key_is_typed_conflict(self):
        command_id = uuid4()
        first = self._graph(replay_token=f"command-id-first-{self.suffix}", command_id=command_id)
        second = self._graph(replay_token=f"command-id-second-{self.suffix}", command_id=command_id)
        results = self._parallel(
            lambda connection: repository_module.CommandIntentRepository().accept_command_graph(connection, first),
            lambda connection: repository_module.CommandIntentRepository().accept_command_graph(connection, second),
        )
        self.assertTrue(all(isinstance(result, (c.CommandGraphResult, c.IdempotencyConflict)) for result in results))
        self.assertEqual(sum(getattr(result, "disposition", None) is c.CommandGraphDisposition.CREATED for result in results), 1)
        self.assertEqual(sum(isinstance(result, c.IdempotencyConflict) for result in results), 1)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM qd_order_commands WHERE id = %s", (command_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM qd_order_intents_v2 WHERE command_id = %s", (command_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM qd_economic_orders WHERE intent_id = %s", (first.intent.intent_id,))
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_same_key_different_command_id_is_typed_conflict_without_driver_exception(self):
        token = f"same-key-different-command-{self.suffix}"
        first = self._graph(replay_token=token)
        second = self._graph(replay_token=token)
        self.assertEqual(
            self.repo.accept_command_graph(self.connection, first).disposition,
            c.CommandGraphDisposition.CREATED,
        )
        with self.assertRaises(c.IdempotencyConflict):
            self.repo.accept_command_graph(self.connection, second)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM qd_order_commands WHERE tenant_id = %s AND source = %s AND idempotency_key = %s",
                (self.user_id, first.command.source, token),
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM qd_order_intents_v2 WHERE command_id = %s", (first.command.command_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM qd_economic_orders WHERE intent_id = %s", (first.intent.intent_id,))
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_two_connections_reservation_replay_conflict_and_terminal_races(self):
        graph = self._graph(replay_token="-".join(("parallel", "reservation")))
        self.repo.accept_command_graph(self.connection, graph)
        reservation = self._reservation(graph, tag="same", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        replay_results = self._parallel(
            lambda connection: repository_module.CommandIntentRepository().create_reservation(connection, reservation),
            lambda connection: repository_module.CommandIntentRepository().create_reservation(connection, reservation),
        )
        self.assertEqual(
            {result.disposition for result in replay_results if not isinstance(result, BaseException)},
            {c.ReservationTransitionDisposition.APPLIED, c.ReservationTransitionDisposition.IDEMPOTENT_REPLAY},
        )
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM qd_risk_reservations WHERE command_id = %s AND state = 'ACTIVE'", (graph.command.command_id,))
            self.assertEqual(cursor.fetchone()[0], 1)

        conflict_graph = self._graph(replay_token="-".join(("parallel", "reservation", "conflict")))
        self.repo.accept_command_graph(self.connection, conflict_graph)
        conflict_first = self._reservation(conflict_graph, tag="first", expires_at=reservation.expires_at)
        conflicting = self._reservation(conflict_graph, tag="different", expires_at=reservation.expires_at)
        results = self._parallel(
            lambda connection: repository_module.CommandIntentRepository().create_reservation(connection, conflict_first),
            lambda connection: repository_module.CommandIntentRepository().create_reservation(connection, conflicting),
        )
        self.assertEqual(sum(getattr(result, "disposition", None) is c.ReservationTransitionDisposition.APPLIED for result in results), 1)
        self.assertEqual(sum(isinstance(result, c.ReservationConflict) for result in results), 1)

        terminal_results = self._parallel(
            lambda connection: repository_module.CommandIntentRepository().consume_reservation(connection, reservation.reservation_id, 0),
            lambda connection: repository_module.CommandIntentRepository().release_reservation(connection, reservation.reservation_id, 0),
        )
        self.assertEqual(sum(getattr(result, "disposition", None) is c.ReservationTransitionDisposition.APPLIED for result in terminal_results), 1)
        self.assertEqual(sum(isinstance(result, c.ReservationStateConflict) for result in terminal_results), 1)

    def test_expire_and_consume_race_leaves_one_irreversible_terminal_state(self):
        graph = self._graph(replay_token="-".join(("parallel", "expire")))
        self.repo.accept_command_graph(self.connection, graph)
        expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        reservation = self._reservation(graph, tag="expire", expires_at=expires_at)
        self.repo.create_reservation(self.connection, reservation)
        results = self._parallel(
            lambda connection: repository_module.CommandIntentRepository().expire_reservation(connection, reservation.reservation_id, 0, expires_at),
            lambda connection: repository_module.CommandIntentRepository().consume_reservation(connection, reservation.reservation_id, 0),
        )
        self.assertEqual(sum(getattr(result, "disposition", None) is c.ReservationTransitionDisposition.APPLIED for result in results), 1)
        self.assertEqual(sum(isinstance(result, c.ReservationStateConflict) for result in results), 1)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT state, version FROM qd_risk_reservations WHERE id = %s", (reservation.reservation_id,))
            state, version = cursor.fetchone()
            self.assertIn(state, {"CONSUMED", "EXPIRED"})
            self.assertEqual(version, 1)


if __name__ == "__main__":
    unittest.main()
