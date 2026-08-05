from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import uuid4

from tests.pr12c_admission_loader import load_pr12c_admission


class _Cursor:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.closed = False
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def fetchone(self):
        return next(self._responses)

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


class RuntimeEntryAuthorityRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_pr12c_admission()
        cls.OrderAction = cls.modules.order.OrderAction
        cls.OrderSide = cls.modules.entry.OrderSide
        cls.PositionSide = cls.modules.entry.PositionSide
        cls.EntrySource = cls.modules.entry.EntrySource
        cls.EntryMode = cls.modules.entry.EntryMode
        cls.ExecutionKind = cls.modules.entry_v2.ExecutionKind
        cls.QuantitySemantics = cls.modules.entry_v2.QuantitySemantics
        cls.RuntimeEntryIngressV1 = cls.modules.runtime_ingress.RuntimeEntryIngressV1
        cls.RuntimeIngressPrincipal = cls.modules.runtime_ingress.RuntimeIngressPrincipal
        cls.build_request = staticmethod(cls.modules.runtime_ingress.build_runtime_entry_request)
        cls.derive_identity = staticmethod(cls.modules.runtime_ingress.derive_durable_entry_identity)
        cls.bind_manual = staticmethod(cls.modules.entrypoint_bindings.bind_manual_v2)
        cls.Repository = cls.modules.runtime_authority_repository.RuntimeEntryAuthorityRepository

    def _ingress(self):
        return self.RuntimeEntryIngressV1(
            credential_id=7, instrument_id="BTC-USDT", market_type="swap",
            action=self.OrderAction.OPEN, side=self.OrderSide.BUY,
            quantity="1", quantity_semantics=self.QuantitySemantics.ABSOLUTE,
            execution_kind=self.ExecutionKind.MARKET, idempotency_key="case-1",
        )

    def _principal(self):
        return self.RuntimeIngressPrincipal(tenant_id=3, actor_id="user-3", source=self.EntrySource.MANUAL)

    def test_resolution_uses_only_persisted_authority_and_never_owns_transaction(self):
        binding_id, instrument_id, rule_id = (str(uuid4()), str(uuid4()), str(uuid4()))
        cursor = _Cursor([
            (binding_id, 3, 7, "account-7", "binance"),
            (instrument_id, 3, 7, "account-7", "BTC-USDT", "swap", rule_id),
        ])
        connection = _Connection(cursor)
        result = self.Repository().resolve(connection, self._ingress(), self._principal())
        self.assertEqual(result.references.scope_binding_id, binding_id)
        self.assertEqual(result.references.instrument_authority_id, instrument_id)
        self.assertIsNone(result.references.position_subject_id)
        self.assertTrue(cursor.closed)
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))
        self.assertTrue(all("qd_runtime_entry_" in query for query, _ in cursor.queries))

    def test_persist_ingress_is_caller_owned_and_returns_typed_created_receipt(self):
        binding_id, instrument_id, rule_id = (str(uuid4()), str(uuid4()), str(uuid4()))
        resolve_cursor = _Cursor([
            (binding_id, 3, 7, "account-7", "binance"),
            (instrument_id, 3, 7, "account-7", "BTC-USDT", "swap", rule_id),
        ])
        repository = self.Repository()
        authority = repository.resolve(_Connection(resolve_cursor), self._ingress(), self._principal())
        request = self.build_request(
            self._ingress(), principal=self._principal(), scope=authority.facts.scope,
            correlation_id="correlation-1", occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            mode=self.EntryMode.PAPER,
        )
        graph = self.bind_manual(request, self.derive_identity(self._ingress(), principal=self._principal(), scope=authority.facts.scope))
        cursor = _Cursor([(graph.command_id,)])
        connection = _Connection(cursor)
        result = repository.persist_ingress(connection, graph, authority)
        self.assertEqual(result.disposition.value, "CREATED")
        self.assertEqual(result.command_id, graph.command_id)
        self.assertTrue(cursor.closed)
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))
        self.assertIn("ON CONFLICT DO NOTHING", cursor.queries[0][0])


if __name__ == "__main__":
    unittest.main()
