"""Unit coverage for caller-owned durable Canonical Entry V2 persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import inspect
from pathlib import Path
import unittest
from uuid import UUID, uuid4

from tests.pr11_contract_loader import load_pr11_contracts


m = load_pr11_contracts()
d = m.durable_entry
r = m.durable_entry_repository
OrderAction = m.order.OrderAction
RiskEffect = m.order.RiskEffect
Actor = m.order.Actor
EntryActorContext = m.entry.EntryActorContext
EntrySource = m.entry.EntrySource
EntryMode = m.entry.EntryMode
ExecutionKind = m.entry.ExecutionKind
OrderSide = m.entry.OrderSide
PositionSide = m.entry.PositionSide
Price = m.decimals.Price
Quantity = m.decimals.Quantity
CanonicalEconomicIntentV2 = m.entry_v2.CanonicalEconomicIntentV2
CanonicalEntryRequestV2 = m.entry_v2.CanonicalEntryRequestV2
CanonicalEntryV2Error = m.entry_v2.CanonicalEntryV2Error
DurableEntryGraphV2 = m.entry_v2.DurableEntryGraphV2
EconomicOrderSubject = m.entry_v2.EconomicOrderSubject


class FakeCursor:
    def __init__(self, responses=(), *, execute_error: Exception | None = None):
        self.responses = list(responses)
        self.execute_error = execute_error
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, statement, params=()):
        self.executed.append((" ".join(statement.split()), params))
        if self.execute_error is not None:
            raise self.execute_error

    def fetchone(self):
        return self.responses.pop(0) if self.responses else None

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self.cursor_value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FailingCursorConnection:
    def cursor(self):
        raise RuntimeError("cursor unavailable")


class CursorSequenceConnection(FakeConnection):
    def __init__(self, cursors):
        self.cursors = list(cursors)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        self.cursor_value = self.cursors.pop(0)
        return self.cursor_value


def graph(*, command_id=None, key="entry-case-1", quantity="1", actor_id="human-1"):
    specification = CanonicalEntryRequestV2(
        tenant_id=1,
        credential_id=2,
        account_scope="account-a",
        instrument_id="BTC-USDT",
        market_type="usdm",
        action=OrderAction.OPEN,
        economic_intent=CanonicalEconomicIntentV2(
            side=OrderSide.BUY,
            quantity=Quantity(quantity),
            quantity_semantics=m.entry_v2.QuantitySemantics.ABSOLUTE,
            execution_kind=ExecutionKind.LIMIT,
            limit_price=Price("100"),
            position_side=PositionSide.NET,
        ),
        actor=EntryActorContext(Actor.HUMAN, actor_id, EntrySource.REST),
        risk_effect=RiskEffect.INCREASE_RISK,
        idempotency_key=key,
        correlation_id="corr-1",
        occurred_at=datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc),
        mode=EntryMode.PAPER,
    )
    return DurableEntryGraphV2(command_id or uuid4(), specification, EconomicOrderSubject(uuid4()))


class DurableEntryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = r.DurableEntryRepository()

    def _persisted_row(self, value, *, decimal_scale=False, command_uuid_object=False):
        facts = self.repository._row_facts(value)
        if decimal_scale:
            facts["quantity"] = Decimal("1.000000000000000000")
            facts["limit_price"] = Decimal("100.000000000000000000")
        if command_uuid_object:
            facts["command_id"] = UUID(value.command_id)
            facts["economic_order_id"] = UUID(value.subject.economic_order_id)
        return tuple(facts[column] for column in d.DURABLE_ENTRY_AUTHORITATIVE_COLUMNS)

    def test_create_is_caller_owned_and_uses_only_v2_specification_table(self):
        value = graph()
        connection = FakeConnection(FakeCursor([(value.command_id,)]))
        result = self.repository.persist_durable_entry(connection, value)
        self.assertEqual(d.DurableEntryPersistDisposition.CREATED, result.disposition)
        self.assertEqual(value.command_id, result.command_id)
        self.assertEqual(value.subject.economic_order_id, result.economic_order_id)
        self.assertEqual(0, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        self.assertTrue(connection.cursor_value.closed)
        sql = "\n".join(statement for statement, _ in connection.cursor_value.executed)
        self.assertIn(d.DURABLE_ENTRY_SPECIFICATION_TABLE, sql)
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertNotIn("qd_order_intents_v2", sql)
        self.assertNotIn("qd_economic_orders", sql)

    def test_exact_replay_compares_all_facts_and_normalizes_uuid_decimal_scale(self):
        value = graph()
        connection = FakeConnection(FakeCursor([None, self._persisted_row(
            value, decimal_scale=True, command_uuid_object=True,
        )]))
        result = self.repository.persist_durable_entry(connection, value)
        self.assertEqual(d.DurableEntryPersistDisposition.REPLAYED, result.disposition)
        self.assertEqual(0, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        self.assertTrue(connection.cursor_value.closed)

    def test_same_identity_with_any_changed_authoritative_fact_is_typed_conflict(self):
        value = graph()
        row = list(self._persisted_row(value))
        row[d.DURABLE_ENTRY_AUTHORITATIVE_COLUMNS.index("actor_id")] = "different-actor"
        connection = FakeConnection(FakeCursor([None, tuple(row)]))
        with self.assertRaises(d.DurableEntryConflict):
            self.repository.persist_durable_entry(connection, value)
        self.assertEqual(0, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        self.assertTrue(connection.cursor_value.closed)

    def test_replay_compares_each_authoritative_typed_column_not_only_fingerprints(self):
        value = graph()
        originals = self._persisted_row(value)
        alternate_by_column = {
            "contract_version": "other-contract",
            "command_id": str(uuid4()),
            "tenant_id": 99,
            "credential_id": 98,
            "account_scope": "other-account",
            "instrument_id": "ETH-USDT",
            "market_type": "spot",
            "action": "INCREASE",
            "risk_effect": "REDUCE_RISK",
            "side": "SELL",
            "quantity": Decimal("2.000000000000000000"),
            "quantity_semantics": "OTHER",
            "execution_kind": "MARKET",
            "limit_price": Decimal("101.000000000000000000"),
            "trigger_price": Decimal("99.000000000000000000"),
            "trigger_direction": "AT_OR_BELOW",
            "trigger_price_type": "MARK",
            "reduce_only": True,
            "position_side": "LONG",
            "cancel_target_kind": "CLIENT_ORDER_ID",
            "cancel_target_id": "other-target",
            "target_position_id": "other-position",
            "close_quantity": Decimal("1.000000000000000000"),
            "close_all": True,
            "economic_order_id": str(uuid4()),
            "economic_fingerprint": "a" * 64,
            "request_fingerprint": "b" * 64,
            "actor_type": "STRATEGY",
            "actor_id": "other-actor",
            "source": "STRATEGY",
            "mode": "SHADOW",
            "idempotency_key": "other-key",
            "correlation_id": "other-correlation",
            "occurred_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
        }
        self.assertEqual(set(d.DURABLE_ENTRY_AUTHORITATIVE_COLUMNS), set(alternate_by_column))
        for column, replacement in alternate_by_column.items():
            with self.subTest(column=column):
                row = list(originals)
                row[d.DURABLE_ENTRY_AUTHORITATIVE_COLUMNS.index(column)] = replacement
                connection = FakeConnection(FakeCursor([None, tuple(row)]))
                with self.assertRaises(d.DurableEntryConflict):
                    self.repository.persist_durable_entry(connection, value)
                self.assertEqual(0, connection.commits)
                self.assertEqual(0, connection.rollbacks)

    def test_command_id_collision_is_typed_conflict_not_driver_leak(self):
        value = graph()
        connection = FakeConnection(FakeCursor([None, None, (value.command_id,)]))
        with self.assertRaises(d.DurableEntryConflict):
            self.repository.persist_durable_entry(connection, value)
        self.assertTrue(connection.cursor_value.closed)

    def test_invalid_graph_and_driver_error_are_typed_and_never_control_transaction(self):
        connection = FakeConnection(FakeCursor())
        with self.assertRaises(d.DurableEntryIntegrityError):
            self.repository.persist_durable_entry(connection, object())
        self.assertEqual(0, connection.commits)
        self.assertEqual(0, connection.rollbacks)

        value = graph()
        driver_connection = FakeConnection(FakeCursor(execute_error=RuntimeError("driver failure")))
        with self.assertRaises(d.DurableEntryRepositoryError) as raised:
            self.repository.persist_durable_entry(driver_connection, value)
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(0, driver_connection.commits)
        self.assertEqual(0, driver_connection.rollbacks)
        self.assertTrue(driver_connection.cursor_value.closed)

        with self.assertRaises(d.DurableEntryRepositoryError) as cursor_error:
            self.repository.persist_durable_entry(FailingCursorConnection(), value)
        self.assertIsInstance(cursor_error.exception.__cause__, RuntimeError)

    def test_contract_validation_error_is_not_reclassified_as_driver_error(self):
        with self.assertRaises(CanonicalEntryV2Error):
            CanonicalEntryRequestV2(
                1, 2, "account-a", "BTC-USDT", "usdm", OrderAction.OPEN,
                object(), EntryActorContext(Actor.HUMAN, "human-1", EntrySource.REST),
                RiskEffect.INCREASE_RISK, "case", "corr", datetime.now(timezone.utc), EntryMode.PAPER,
            )

    def test_outer_rollback_can_reuse_connection_after_typed_driver_error(self):
        value = graph()
        failing = FakeCursor(execute_error=RuntimeError("injected failure"))
        succeeding = FakeCursor([(value.command_id,)])
        connection = CursorSequenceConnection([failing, succeeding])
        with self.assertRaises(d.DurableEntryRepositoryError):
            self.repository.persist_durable_entry(connection, value)
        self.assertTrue(failing.closed)
        self.assertEqual(0, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        # The repository deliberately leaves rollback to its caller; after the
        # caller recovers the transaction, the same connection is usable.
        connection.rollback()
        result = self.repository.persist_durable_entry(connection, value)
        self.assertEqual(d.DurableEntryPersistDisposition.CREATED, result.disposition)
        self.assertEqual(0, connection.commits)
        self.assertEqual(1, connection.rollbacks)
        self.assertTrue(succeeding.closed)

    def test_repository_source_never_commits_rolls_back_or_opens_legacy_graph(self):
        source = inspect.getsource(r.DurableEntryRepository.persist_durable_entry)
        self.assertNotIn(".commit(", source)
        self.assertNotIn(".rollback(", source)
        module_source = Path(r.__file__).read_text(encoding="utf-8")
        self.assertNotIn("qd_order_intents_v2", module_source)
        self.assertNotIn("qd_economic_orders", module_source)


if __name__ == "__main__":
    unittest.main()
