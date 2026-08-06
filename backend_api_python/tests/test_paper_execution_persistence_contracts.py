from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import importlib.util
from types import ModuleType, SimpleNamespace
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
def _load_contracts() -> SimpleNamespace:
    names = ("app", "app.domain", "app.domain.paper_execution_contracts")
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        spec = importlib.util.spec_from_file_location(names[2], ROOT / "app" / "domain" / "paper_execution_contracts.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec); sys.modules[names[2]] = module; spec.loader.exec_module(module)
        return SimpleNamespace(module=module)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


C = _load_contracts().module
PaperExecutionContractError = C.PaperExecutionContractError
PaperExecutionFill = C.PaperExecutionFill
PaperExecutionOrder = C.PaperExecutionOrder
PaperExecutionEventType = C.PaperExecutionEventType
PaperExecutionOrderEvent = C.PaperExecutionOrderEvent
PaperExecutionStatus = C.PaperExecutionStatus


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PaperExecutionContractTests(unittest.TestCase):
    def _order(self, **changes):
        values = {
            "order_id": "11111111-1111-4111-8111-111111111111",
            "user_id": 7,
            "idempotency_key": "paper-case-1",
            "request_fingerprint": "request-fingerprint",
            "market": "gate",
            "symbol": "BTC_USDT",
            "market_type": "perpetual",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": Decimal("0.010000000000000000"),
            "limit_price": None,
            "status": PaperExecutionStatus.FILLED,
            "created_at": NOW,
            "fill_quantity": Decimal("0.010000000000000000"),
            "fill_price": Decimal("100"),
            "fee_amount": Decimal("0.001"),
            "fee_asset": "USDT",
        }
        values.update(changes)
        return PaperExecutionOrder(**values)

    def test_decimal_normalization_is_stable_and_no_float(self):
        one = self._order(quantity=Decimal("0.01"))
        two = self._order(quantity=Decimal("0.010000000000000000"))
        self.assertEqual(one.fingerprint, two.fingerprint)
        with self.assertRaises(PaperExecutionContractError):
            self._order(quantity=0.01)

    def test_limit_and_fill_contracts_are_typed(self):
        with self.assertRaises(PaperExecutionContractError):
            self._order(order_type="LIMIT")
        order = self._order(order_type="LIMIT", limit_price=Decimal("101"), status=PaperExecutionStatus.CREATED,
                            fill_quantity=Decimal("0"), fill_price=None)
        fill = PaperExecutionFill(
            fill_id=str(uuid.uuid4()), order_id=order.order_id, quantity=Decimal("0.01"),
            price=Decimal("101"), fee_amount=Decimal("0.001"), fee_asset="USDT", occurred_at=NOW,
        )
        self.assertEqual(fill.order_id, order.order_id)
        self.assertTrue(fill.fingerprint)

    def test_fill_cannot_overrun_order(self):
        with self.assertRaises(PaperExecutionContractError):
            self._order(fill_quantity=Decimal("0.02"))

    def test_order_event_is_typed_and_sequenced(self):
        order = self._order()
        event = PaperExecutionOrderEvent(
            event_id=str(uuid.uuid4()), order_id=order.order_id, event_seq=1,
            event_type=PaperExecutionEventType.CANCEL_REQUESTED, occurred_at=NOW,
        )
        self.assertEqual(event.event_seq, 1)
        with self.assertRaises(PaperExecutionContractError):
            PaperExecutionOrderEvent(event.event_id, order.order_id, 0, PaperExecutionEventType.CANCELLED, NOW)

    def test_scope_and_status_are_explicit(self):
        self.assertEqual(self._order().market_type, "perpetual")
        with self.assertRaises(PaperExecutionContractError):
            self._order(market_type="future")


class PaperExecutionSchemaTests(unittest.TestCase):
    def test_incremental_migration_is_mirrored_verbatim_in_init(self):
        migration = (ROOT / "migrations" / "20260804_paper_execution_persistence.sql").read_text(encoding="utf-8")
        init_sql = (ROOT / "migrations" / "init.sql").read_text(encoding="utf-8")
        self.assertIn(migration, init_sql)

    def test_schema_is_expand_only_and_non_live(self):
        migration = (ROOT / "migrations" / "20260804_paper_execution_persistence.sql").read_text(encoding="utf-8")
        self.assertIn("NUMERIC(38,18)", migration)
        self.assertIn("ON DELETE RESTRICT", migration)
        self.assertIn("append-only", migration)
        self.assertIn("qd_paper_execution_order_events", migration)
        self.assertNotIn("AGENT_LIVE_TRADING_ENABLED", migration)
        self.assertNotIn("exchange_client", migration)


class _Cursor:
    def __init__(self, *, insert_error: Exception | None = None, row=None, rows=None, rowcount: int = 1):
        self.insert_error = insert_error
        self.row = row
        self.rows = rows
        self.rowcount = rowcount
        self.closed = False
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append((query, params))
        if query.lstrip().startswith("INSERT") and self.insert_error is not None:
            raise self.insert_error

    def fetchone(self):
        return self.row

    def fetchall(self):
        if self.rows is not None:
            return self.rows
        return [] if self.row is None else [self.row]

    def close(self):
        self.closed = True


class _FillCursor:
    def __init__(self, order_quantity=Decimal("1"), existing_quantity=Decimal("0")):
        self.order_quantity = order_quantity
        self.existing_quantity = existing_quantity
        self.rowcount = 1
        self.closed = False
        self.queries = []
        self._select_count = 0
        self._rows = []

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def fetchone(self):
        self._select_count += 1
        if self._select_count == 1:
            return (self.order_quantity,)
        return (self.existing_quantity,)

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class _EventCursor:
    def __init__(self):
        self.rowcount = 1
        self.closed = False
        self.queries = []
        self._fetch_count = 0

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def fetchone(self):
        self._fetch_count += 1
        return ("11111111-1111-4111-8111-111111111111",) if self._fetch_count == 1 else (1,)

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class PaperExecutionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = ("app", "app.domain", "app.domain.paper_execution_contracts", "app.services", "app.services.paper_execution_repository")
        missing = object(); cls._previous = {name: sys.modules.get(name, missing) for name in names}; cls._names = names; cls._missing = missing
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services; sys.modules[names[2]] = C
        spec = importlib.util.spec_from_file_location(names[4], ROOT / "app" / "services" / "paper_execution_repository.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec); sys.modules[names[4]] = module; spec.loader.exec_module(module)
        cls.repo = module.PaperExecutionRepository()
        cls.conflict = module.PaperExecutionConflict

    @classmethod
    def tearDownClass(cls):
        for name in reversed(cls._names):
            original = cls._previous[name]
            if original is cls._missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def test_created_does_not_commit_and_closes_cursor(self):
        cursor = _Cursor(); connection = _Connection(cursor)
        order = PaperExecutionContractTests()._order()
        result = self.repo.persist_order(connection, order)
        self.assertEqual(result.disposition.value, "CREATED")
        self.assertEqual(connection.commits, 0); self.assertEqual(connection.rollbacks, 0); self.assertTrue(cursor.closed)

    def test_replay_does_not_commit_or_rollback(self):
        order = PaperExecutionContractTests()._order()
        cursor = _Cursor(rowcount=0, rows=[(order.order_id, order.user_id, order.idempotency_key, order.fingerprint)]); connection = _Connection(cursor)
        result = self.repo.persist_order(connection, order)
        self.assertEqual(result.disposition.value, "REPLAYED")
        self.assertEqual(connection.commits, 0); self.assertEqual(connection.rollbacks, 0); self.assertTrue(cursor.closed)

    def test_primary_key_collision_with_different_idempotency_key_is_typed_conflict(self):
        order = PaperExecutionContractTests()._order()
        cursor = _Cursor(
            rowcount=0,
            rows=[(order.order_id, order.user_id, "different-key", "different-fingerprint")],
        )
        connection = _Connection(cursor)
        with self.assertRaises(self.conflict):
            self.repo.persist_order(connection, order)
        self.assertEqual(connection.commits, 0); self.assertEqual(connection.rollbacks, 0); self.assertTrue(cursor.closed)

    def test_idempotency_collision_with_different_order_id_is_typed_conflict(self):
        order = PaperExecutionContractTests()._order()
        cursor = _Cursor(
            rowcount=0,
            rows=[("22222222-2222-4222-8222-222222222222", order.user_id, order.idempotency_key, "different-fingerprint")],
        )
        connection = _Connection(cursor)
        with self.assertRaises(self.conflict):
            self.repo.persist_order(connection, order)
        self.assertEqual(connection.commits, 0); self.assertEqual(connection.rollbacks, 0); self.assertTrue(cursor.closed)

    def test_append_fill_locks_authenticated_owner_without_commit(self):
        cursor = _FillCursor()
        connection = _Connection(cursor)
        order = PaperExecutionContractTests()._order(status=PaperExecutionStatus.SUBMITTED,
                                                     fill_quantity=Decimal("0"), fill_price=None)
        fill = PaperExecutionFill(
            fill_id="22222222-2222-4222-8222-222222222222", order_id=order.order_id,
            quantity=Decimal("0.1"), price=Decimal("100"), fee_amount=Decimal("0"),
            fee_asset="USDT", occurred_at=NOW,
        )
        result = self.repo.append_fill(connection, fill, user_id=order.user_id)
        self.assertEqual(result.value, "CREATED")
        self.assertIn("user_id = %s", cursor.queries[0][0])
        self.assertEqual(cursor.queries[0][1], (order.order_id, order.user_id))
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(cursor.closed)

    def test_duplicate_final_fill_replays_before_overfill_guard(self):
        order = PaperExecutionContractTests()._order(
            status=PaperExecutionStatus.SUBMITTED,
            fill_quantity=Decimal("0"), fill_price=None,
        )
        fill = PaperExecutionFill(
            fill_id="33333333-3333-4333-8333-333333333333", order_id=order.order_id,
            quantity=Decimal("1"), price=Decimal("100"), fee_amount=Decimal("0"),
            fee_asset="USDT", occurred_at=NOW,
        )
        cursor = _FillCursor(order_quantity=Decimal("1"), existing_quantity=Decimal("1"))
        cursor._rows = [(fill.fill_id, fill.order_id, fill.fingerprint)]
        result = self.repo.append_fill(_Connection(cursor), fill, user_id=order.user_id)
        self.assertEqual(result.value, "REPLAYED")
        self.assertTrue(cursor.closed)

    def test_order_event_locks_authenticated_owner_without_commit(self):
        cursor = _EventCursor()
        connection = _Connection(cursor)
        order = PaperExecutionContractTests()._order(status=PaperExecutionStatus.SUBMITTED,
                                                     fill_quantity=Decimal("0"), fill_price=None)
        event = PaperExecutionOrderEvent(
            event_id="44444444-4444-4444-8444-444444444444", order_id=order.order_id,
            event_seq=2, event_type=PaperExecutionEventType.CANCELLED, occurred_at=NOW,
        )
        result = self.repo.append_order_event(connection, event, user_id=order.user_id)
        self.assertEqual(result.value, "CREATED")
        self.assertIn("user_id = %s", cursor.queries[0][0])
        self.assertEqual(cursor.queries[0][1], (order.order_id, order.user_id))
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(cursor.closed)


if __name__ == "__main__":
    unittest.main()
