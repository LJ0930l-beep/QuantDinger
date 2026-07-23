from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from tests.pr03_contract_loader import load_pr03_contracts


modules = load_pr03_contracts()
c = modules.contracts
d = modules.decimal_values
o = modules.order_contracts
repository_module = modules.repository
TEST_REPLAY_TOKEN = "-".join(("idem", "fixture", "one"))


def graph(replay_token=TEST_REPLAY_TOKEN):
    command = c.OrderCommand(
        command_id=uuid4(), tenant_id=1, user_id=1, credential_id=2,
        actor_type=o.Actor.STRATEGY, actor_id="strategy:1", source="strategy_v2",
        action=o.OrderAction.OPEN, account_scope="account-a", request_payload={"kind": "test"},
        idempotency_key=replay_token,
    )
    intent = c.OrderIntent(
        intent_id=uuid4(), economic_order_id=uuid4(), command_id=command.command_id,
        tenant_id=1, credential_id=2, account_scope="account-a", exchange_id="binance",
        instrument_id="BTC-USDT", market_type="usdm", side="BUY", target_quantity=d.Quantity("1"),
        instrument_rule_snapshot_id=uuid4(), instrument_rule_version="v1", order_type="LIMIT",
        execution_algo="DIRECT", rounding_mode="ROUND_DOWN", limit_price=d.Price("100"),
    )
    return c.CommandGraph(command, intent)


class FakeCursor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed = []
        self.closed = False

    def execute(self, statement, params=()):
        self.executed.append((" ".join(statement.split()), params))

    def fetchone(self):
        if not self.responses:
            return None
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, responses):
        self.cursor_value = FakeCursor(responses)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class CommandIntentRepositoryTests(unittest.TestCase):
    def test_atomic_accept_inserts_command_intent_and_created_order(self):
        value = graph()
        connection = FakeConnection([(value.intent.instrument_rule_snapshot_id,), (value.command.command_id,)])
        result = repository_module.CommandIntentRepository().accept_command_graph(connection, value)
        self.assertEqual(result.disposition, c.CommandGraphDisposition.CREATED)
        self.assertEqual(result.state, o.EconomicOrderState.CREATED)
        self.assertEqual(connection.commits, 1)
        sql = "\n".join(statement for statement, _ in connection.cursor_value.executed)
        self.assertIn("qd_order_commands", sql)
        self.assertIn("qd_order_intents_v2", sql)
        self.assertIn("qd_economic_orders", sql)
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertNotIn("ON CONFLICT (tenant_id, source, idempotency_key)", sql)

    def test_idempotent_replay_returns_existing_graph_only_when_facts_match(self):
        value = graph()
        existing = (
            value.command.command_id, value.command.user_id, value.command.credential_id,
            value.command.actor_type.value, value.command.actor_id, value.command.action.value,
            value.command.account_scope, value.command.request_fingerprint,
        )
        linked = (
            value.intent.intent_id, value.intent.economic_order_id, value.intent.tenant_id,
            value.intent.credential_id, value.intent.account_scope, value.intent.instrument_id,
            value.intent.market_type, value.intent.side, value.intent.target_quantity.to_string(),
            value.intent.limit_price.to_string(), None, value.intent.instrument_rule_snapshot_id,
            value.intent.instrument_rule_version, value.intent.rounding_mode, value.intent.payload_hash,
            "CREATED",
        )
        connection = FakeConnection([
            (value.intent.instrument_rule_snapshot_id,), None, existing,
            linked,
        ])
        result = repository_module.CommandIntentRepository().accept_command_graph(connection, value)
        self.assertEqual(result.disposition, c.CommandGraphDisposition.REPLAYED)
        self.assertEqual(result.economic_order_id, value.intent.economic_order_id)
        self.assertEqual(connection.commits, 1)

    def test_idempotency_mismatch_rolls_back_without_writing_intent(self):
        value = graph()
        wrong = (
            value.command.command_id, value.command.user_id, 99,
            value.command.actor_type.value, value.command.actor_id, value.command.action.value,
            value.command.account_scope, value.command.request_fingerprint,
        )
        connection = FakeConnection([(value.intent.instrument_rule_snapshot_id,), None, wrong])
        with self.assertRaises(c.IdempotencyConflict):
            repository_module.CommandIntentRepository().accept_command_graph(connection, value)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("qd_order_intents_v2" in statement for statement, _ in connection.cursor_value.executed))

    def test_replay_rejects_different_intent_even_when_command_matches(self):
        value = graph()
        existing = (
            value.command.command_id, value.command.user_id, value.command.credential_id,
            value.command.actor_type.value, value.command.actor_id, value.command.action.value,
            value.command.account_scope, value.command.request_fingerprint,
        )
        linked = (
            value.intent.intent_id, value.intent.economic_order_id, value.intent.tenant_id,
            value.intent.credential_id, value.intent.account_scope, value.intent.instrument_id,
            value.intent.market_type, value.intent.side, "2", "100", None,
            value.intent.instrument_rule_snapshot_id, value.intent.instrument_rule_version,
            value.intent.rounding_mode, value.intent.payload_hash, "CREATED",
        )
        connection = FakeConnection([(value.intent.instrument_rule_snapshot_id,), None, existing, linked])
        with self.assertRaises(c.IdempotencyConflict):
            repository_module.CommandIntentRepository().accept_command_graph(connection, value)
        self.assertEqual(connection.rollbacks, 1)

    def test_replay_normalizes_database_numeric_uuid_and_jsonb_facts(self):
        value = graph()
        existing = (
            value.command.command_id, value.command.user_id, value.command.credential_id,
            value.command.actor_type.value, value.command.actor_id, value.command.action.value,
            value.command.account_scope, value.command.request_fingerprint,
        )
        linked = (
            value.intent.intent_id, value.intent.economic_order_id, value.intent.tenant_id,
            value.intent.credential_id, value.intent.account_scope, value.intent.instrument_id,
            value.intent.market_type, value.intent.side, Decimal("1.000000000000000000"),
            Decimal("100.000000000000000000"), None, value.intent.instrument_rule_snapshot_id,
            value.intent.instrument_rule_version, value.intent.rounding_mode, value.intent.payload_hash,
            "CREATED",
        )
        connection = FakeConnection([(value.intent.instrument_rule_snapshot_id,), None, existing, linked])
        result = repository_module.CommandIntentRepository().accept_command_graph(connection, value)
        self.assertEqual(result.disposition, c.CommandGraphDisposition.REPLAYED)

    def test_reservation_replay_normalizes_database_numeric_jsonb_and_timestamp(self):
        value = graph()
        expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
        reservation = c.RiskReservation(
            reservation_id=uuid4(), command_id=value.command.command_id,
            economic_order_id=value.intent.economic_order_id, tenant_id=1, credential_id=2,
            account_scope="account-a", reservation_kind="MARGIN", currency="USDT",
            reserved_notional=d.QuoteAmount("100"), reserved_margin=d.QuoteAmount("10"),
            reserved_position_qty=d.Quantity("1"), limits_snapshot={"b": "2", "a": "1"},
            risk_input_hash="d" * 64, expires_at=expires_at,
        )
        row = (
            reservation.reservation_id, reservation.economic_order_id, 1, 2, "account-a", "USDT",
            Decimal("100.000000000000000000"), Decimal("10.000000000000000000"),
            Decimal("1.000000000000000000"), {"a": "1", "b": "2"}, reservation.risk_input_hash,
            "ACTIVE", expires_at, 0,
        )
        connection = FakeConnection([(value.command.command_id,), row])
        result = repository_module.CommandIntentRepository().create_reservation(connection, reservation)
        self.assertEqual(result.disposition, c.ReservationTransitionDisposition.IDEMPOTENT_REPLAY)

    def test_snapshot_mismatch_fails_before_command_insert(self):
        value = graph()
        connection = FakeConnection([None])
        with self.assertRaises(c.InstrumentRuleSnapshotMismatch):
            repository_module.CommandIntentRepository().accept_command_graph(connection, value)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(len(connection.cursor_value.executed), 1)

    def test_reservation_create_and_cas_transition_are_explicit(self):
        value = graph()
        reservation = c.RiskReservation(
            reservation_id=uuid4(), command_id=value.command.command_id,
            economic_order_id=value.intent.economic_order_id, tenant_id=1, credential_id=2,
            account_scope="account-a", reservation_kind="MARGIN", currency="USDT",
            reserved_notional=d.QuoteAmount("100"), reserved_margin=d.QuoteAmount("10"),
            reserved_position_qty=d.Quantity("1"), limits_snapshot={"max": "100"},
            risk_input_hash="b" * 64, expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        connection = FakeConnection([(value.command.command_id,), None, (reservation.reservation_id, 0)])
        repo = repository_module.CommandIntentRepository()
        created = repo.create_reservation(connection, reservation)
        self.assertEqual(created.state, c.ReservationState.ACTIVE)
        self.assertEqual(created.disposition, c.ReservationTransitionDisposition.APPLIED)
        self.assertEqual(connection.commits, 1)

        transition_connection = FakeConnection([(1,)])
        consumed = repo.consume_reservation(transition_connection, reservation.reservation_id, 0)
        self.assertEqual(consumed.state, c.ReservationState.CONSUMED)
        self.assertEqual(consumed.version, 1)
        self.assertEqual(consumed.disposition, c.ReservationTransitionDisposition.APPLIED)

    def test_terminal_replay_is_idempotent_but_other_terminal_is_conflict(self):
        reservation_id = str(uuid4())
        repo = repository_module.CommandIntentRepository()
        replay_connection = FakeConnection([None, ("RELEASED", 2)])
        replay = repo.release_reservation(replay_connection, reservation_id, 1)
        self.assertEqual(replay.disposition, c.ReservationTransitionDisposition.IDEMPOTENT_REPLAY)
        conflict_connection = FakeConnection([None, ("CONSUMED", 2)])
        with self.assertRaises(c.ReservationStateConflict):
            repo.release_reservation(conflict_connection, reservation_id, 0)
        self.assertEqual(conflict_connection.rollbacks, 1)

    def test_terminal_replay_requires_the_original_expected_version(self):
        reservation_id = str(uuid4())
        repo = repository_module.CommandIntentRepository()
        replay = repo.consume_reservation(FakeConnection([None, ("CONSUMED", 1)]), reservation_id, 0)
        self.assertEqual(replay.disposition, c.ReservationTransitionDisposition.IDEMPOTENT_REPLAY)
        for version in (1, 9):
            with self.subTest(version=version):
                with self.assertRaises(c.ReservationStateConflict):
                    repo.consume_reservation(FakeConnection([None, ("CONSUMED", 1)]), reservation_id, version)

    def test_expiry_is_single_transactional_cas_with_boundary_and_terminal_rules(self):
        reservation_id = str(uuid4())
        expires_at = datetime.now(timezone.utc).replace(microsecond=0)
        repo = repository_module.CommandIntentRepository()
        applied = repo.expire_reservation(FakeConnection([(1,)]), reservation_id, 0, expires_at)
        self.assertEqual(applied.disposition, c.ReservationTransitionDisposition.APPLIED)
        before = FakeConnection([None, ("ACTIVE", 0, expires_at)])
        with self.assertRaises(c.ReservationStateConflict):
            repo.expire_reservation(before, reservation_id, 0, expires_at - timedelta(seconds=1))
        replay = repo.expire_reservation(FakeConnection([None, ("EXPIRED", 1, expires_at)]), reservation_id, 0, expires_at)
        self.assertEqual(replay.disposition, c.ReservationTransitionDisposition.IDEMPOTENT_REPLAY)
        with self.assertRaises(c.ReservationStateConflict):
            repo.expire_reservation(FakeConnection([None, ("EXPIRED", 1, expires_at)]), reservation_id, 1, expires_at)
        with self.assertRaises(c.ReservationStateConflict):
            repo.expire_reservation(FakeConnection([None, ("CONSUMED", 1, expires_at)]), reservation_id, 0, expires_at)


if __name__ == "__main__":
    unittest.main()
