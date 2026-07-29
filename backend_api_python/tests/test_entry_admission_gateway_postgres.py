"""PostgreSQL caller-owned composition checks for Canonical Entry V2 adapters.

This is deliberately not a gateway or runtime test: it verifies that the
durable entry, hard-risk V2, and admission-outbox adapters can share exactly
one caller-owned transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import unittest
from uuid import uuid4

from tests.pr12c_admission_loader import load_pr12c_admission
from tests.test_entry_admission_v2_adapters import (
    _Provider,
    exposure,
    graph,
    policy,
    request,
    switches,
)


m = load_pr12c_admission()
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INIT_SQL = MIGRATIONS / "init.sql"


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class EntryAdmissionV2PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        cls.psycopg2 = psycopg2
        connection = cls._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                for migration in sorted(MIGRATIONS.glob("2026*.sql")):
                    cursor.execute(migration.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _connection(cls):
        connection = cls.psycopg2.connect(os.environ["DATABASE_URL"])
        connection.autocommit = False
        return connection

    def setUp(self):
        self.connection = self._connection()
        suffix = uuid4().hex
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
                (f"entry-admission-v2-{suffix}", "test"),
            )
            self.tenant_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) "
                "VALUES (%s, %s, %s) RETURNING id",
                (self.tenant_id, f"entry-admission-v2-{suffix}", "{}"),
            )
            self.credential_id = cursor.fetchone()[0]
        self.connection.commit()

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _graph(self):
        value = graph()
        specification = m.entry_v2.CanonicalEntryRequestV2(
            self.tenant_id, self.credential_id, value.specification.account_scope,
            value.specification.instrument_id, value.specification.market_type,
            value.specification.action, value.specification.economic_intent,
            value.specification.actor, value.specification.risk_effect,
            f"entry-admission-{uuid4().hex}", value.specification.correlation_id,
            value.specification.occurred_at, value.specification.mode,
        )
        return m.entry_v2.DurableEntryGraphV2(uuid4(), specification, m.entry_v2.EconomicOrderSubject(uuid4()))

    def _inputs(self, value, *, denied=False):
        demand = m.hard_risk.RiskReservationDemand(
            "provider-demand", value.specification.account_scope,
            value.specification.instrument_id, "USDT", "100", "100", "100", "25",
        )
        return m.admission.DurableRiskAdmissionInputs(
            policy(), exposure(), switches(enabled=denied), request(value.specification.action),
            datetime(2026, 7, 29, tzinfo=timezone.utc), reservation_demand=demand,
        )

    def _persist_chain(self, value, *, denied=False):
        durable = m.durable_entry_repository.DurableEntryRepository().persist_durable_entry(self.connection, value)
        risk = m.adapters.DurableRiskAdmissionAdapter(provider=_Provider(self._inputs(value, denied=denied))).evaluate_and_persist(self.connection, value)
        if risk.allowed:
            outbox = m.adapters.AdmissionOutboxAdapter().persist_admission(self.connection, value, durable, risk)
        else:
            outbox = None
        return durable, risk, outbox

    def _count(self, table, identifier, column):
        with self.connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (identifier,))
            return cursor.fetchone()[0]

    def test_outer_rollback_erases_all_admission_facts_and_connection_remains_usable(self):
        value = self._graph()
        _, risk, outbox = self._persist_chain(value)
        self.assertTrue(risk.allowed)
        self.assertIsNotNone(risk.reservation_id)
        self.assertIsNotNone(outbox)
        self.connection.rollback()
        self.assertEqual(0, self._count("qd_durable_entry_specifications", value.command_id, "command_id"))
        self.assertEqual(0, self._count("qd_durable_risk_decisions", risk.decision_id, "id"))
        self.assertEqual(0, self._count("qd_transactional_outbox", outbox.event.event_id, "event_id"))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(1, cursor.fetchone()[0])

    def test_outer_commit_then_exact_replay_creates_no_duplicate_facts(self):
        value = self._graph()
        durable, risk, outbox = self._persist_chain(value)
        self.connection.commit()
        self.assertEqual(m.durable_entry.DurableEntryPersistDisposition.CREATED, durable.disposition)
        self.assertEqual(m.durable_risk.DurableRiskPersistDisposition.CREATED, risk.disposition)
        self.assertEqual(m.outbox_repository.OutboxPersistDisposition.CREATED, outbox.disposition)
        replayed = self._persist_chain(value)
        self.assertEqual(m.durable_entry.DurableEntryPersistDisposition.REPLAYED, replayed[0].disposition)
        self.assertEqual(m.durable_risk.DurableRiskPersistDisposition.REPLAYED, replayed[1].disposition)
        self.assertEqual(m.outbox_repository.OutboxPersistDisposition.REPLAYED, replayed[2].disposition)
        self.connection.rollback()

    def test_denied_risk_persists_no_reservation_and_no_outbox(self):
        value = self._graph()
        durable, risk, outbox = self._persist_chain(value, denied=True)
        self.assertEqual(m.durable_entry.DurableEntryPersistDisposition.CREATED, durable.disposition)
        self.assertFalse(risk.allowed)
        self.assertIsNone(risk.reservation_id)
        self.assertIsNone(outbox)
        self.connection.commit()
        self.assertEqual(0, self._count("qd_durable_risk_reservations", risk.decision_id, "decision_id"))
        self.assertEqual(0, self._count("qd_transactional_outbox", value.command_id, "aggregate_id"))


if __name__ == "__main__":
    unittest.main()
