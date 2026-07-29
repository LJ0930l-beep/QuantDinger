"""PostgreSQL call-layer coverage for durable Canonical Entry V2 persistence.

The test becomes active only once the separately owned expand-only migration is
present in the checkout.  It intentionally tests caller-owned transaction
composition rather than owning a service or runtime transaction boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import unittest
from uuid import uuid4

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
DurableEntryGraphV2 = m.entry_v2.DurableEntryGraphV2
EconomicOrderSubject = m.entry_v2.EconomicOrderSubject


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INIT_SQL = MIGRATIONS / "init.sql"
DURABLE_MIGRATION = MIGRATIONS / "20260729_durable_entry_specifications.sql"


@unittest.skipUnless(
    os.getenv("DATABASE_URL") and DURABLE_MIGRATION.exists(),
    "requires CI PostgreSQL DATABASE_URL and durable-entry schema lane",
)
class DurableEntryRepositoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        cls.psycopg2 = psycopg2
        connection = cls._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                for migration in sorted(
                    path for path in MIGRATIONS.glob("2026*.sql") if path.name != "init.sql"
                ):
                    cursor.execute(migration.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _connection(cls):
        return cls.psycopg2.connect(os.environ["DATABASE_URL"])

    def _graph(self, *, key=None, command_id=None, economic_order_id=None, quantity="1"):
        suffix = uuid4().hex
        connection = self._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
                    (f"durable-entry-{suffix}", "test"),
                )
                tenant_id = cursor.fetchone()[0]
                cursor.execute(
                    "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) VALUES (%s, %s, %s) RETURNING id",
                    (tenant_id, f"durable-entry-{suffix}", "{}"),
                )
                credential_id = cursor.fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        specification = CanonicalEntryRequestV2(
            tenant_id=tenant_id, credential_id=credential_id, account_scope="account-a",
            instrument_id="BTC-USDT", market_type="usdm", action=OrderAction.OPEN,
            economic_intent=CanonicalEconomicIntentV2(
                side=OrderSide.BUY, quantity=Quantity(quantity),
                quantity_semantics=m.entry_v2.QuantitySemantics.ABSOLUTE,
                execution_kind=ExecutionKind.LIMIT, limit_price=Price("100"),
                position_side=PositionSide.NET,
            ),
            actor=EntryActorContext(Actor.HUMAN, "human-1", EntrySource.REST),
            risk_effect=RiskEffect.INCREASE_RISK,
            idempotency_key=key or f"entry-{suffix}", correlation_id="corr-1",
            occurred_at=datetime(2026, 7, 29, tzinfo=timezone.utc), mode=EntryMode.PAPER,
        )
        return DurableEntryGraphV2(
            command_id or uuid4(), specification,
            EconomicOrderSubject(economic_order_id or uuid4()),
        )

    def test_create_exact_replay_conflict_and_outer_rollback_reuse(self):
        graph = self._graph()
        repository = r.DurableEntryRepository()
        connection = self._connection()
        try:
            created = repository.persist_durable_entry(connection, graph)
            self.assertEqual(d.DurableEntryPersistDisposition.CREATED, created.disposition)
            connection.commit()
            replayed = repository.persist_durable_entry(connection, graph)
            self.assertEqual(d.DurableEntryPersistDisposition.REPLAYED, replayed.disposition)
            connection.rollback()

            changed = DurableEntryGraphV2(
                graph.command_id,
                CanonicalEntryRequestV2(
                    graph.specification.tenant_id, graph.specification.credential_id,
                    graph.specification.account_scope, graph.specification.instrument_id,
                    graph.specification.market_type, graph.specification.action,
                    CanonicalEconomicIntentV2(
                        side=OrderSide.BUY, quantity=Quantity("2"),
                        quantity_semantics=m.entry_v2.QuantitySemantics.ABSOLUTE,
                        execution_kind=ExecutionKind.LIMIT, limit_price=Price("100"),
                    ), graph.specification.actor, graph.specification.risk_effect,
                    graph.specification.idempotency_key, graph.specification.correlation_id,
                    graph.specification.occurred_at, graph.specification.mode,
                ), graph.subject,
            )
            with self.assertRaises(d.DurableEntryConflict):
                repository.persist_durable_entry(connection, changed)
            connection.rollback()

            rollback_graph = self._graph()
            repository.persist_durable_entry(connection, rollback_graph)
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM qd_durable_entry_specifications WHERE command_id = %s",
                    (rollback_graph.command_id,),
                )
                self.assertEqual(0, cursor.fetchone()[0])
            reused = repository.persist_durable_entry(connection, rollback_graph)
            self.assertEqual(d.DurableEntryPersistDisposition.CREATED, reused.disposition)
        finally:
            connection.rollback()
            connection.close()

    def test_two_connections_create_and_replay_without_raw_driver_error(self):
        graph = self._graph()
        outcomes: list[object] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def persist_once():
            connection = self._connection()
            try:
                barrier.wait(timeout=10)
                result = r.DurableEntryRepository().persist_durable_entry(connection, graph)
                connection.commit()
                outcome: object = result.disposition
            except Exception as exc:
                connection.rollback()
                outcome = exc
            finally:
                connection.close()
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=persist_once, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive(), "durable entry concurrency test timed out")
        self.assertCountEqual(
            outcomes,
            [d.DurableEntryPersistDisposition.CREATED, d.DurableEntryPersistDisposition.REPLAYED],
        )


if __name__ == "__main__":
    unittest.main()
