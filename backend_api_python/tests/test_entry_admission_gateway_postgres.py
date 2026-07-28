"""Caller-owned transaction contract for the non-runtime entry gateway.

The ports are deliberately test-only adapters: this verifies that the gateway
preserves one PostgreSQL transaction boundary while the production adapters are
introduced in later runtime work.
"""
from __future__ import annotations

import os
import unittest

from tests.test_entry_admission_gateway import c, command, draft, g, graph, o


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class EntryAdmissionGatewayPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg2
        self.connection = psycopg2.connect(os.environ["DATABASE_URL"])
        self.connection.autocommit = False
        with self.connection.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE entry_gateway_facts (kind TEXT PRIMARY KEY, value TEXT NOT NULL) ON COMMIT PRESERVE ROWS")

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _insert(self, kind, value):
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO entry_gateway_facts(kind, value) VALUES (%s, %s) ON CONFLICT (kind) DO NOTHING RETURNING kind", (kind, value))
            return cursor.fetchone() is not None

    def _gateway(self, *, deny=False, fail_kind=None):
        outer = self
        class Mapper:
            def map(self, value): return graph(value)
        class Commands:
            def persist_command_graph(self, connection, value):
                if fail_kind == "command": raise g.EntryAdmissionConflict("typed command conflict")
                if outer._insert("command", value.command.command_id): return command()
                return command(True)
        class Risks:
            def persist_for_admission(self, connection, value, mapped):
                if deny:
                    outer._insert("risk-denial", "DENY")
                    return g.HardRiskPersistResult(False, None, g.HardRiskDisposition.CREATED)
                if fail_kind == "risk": raise g.EntryAdmissionConflict("typed risk conflict")
                created = outer._insert("reservation", "reservation-1")
                return g.HardRiskPersistResult(True, g.ReservationPersistResult("reservation-1", g.ReservationDisposition.CREATED if created else g.ReservationDisposition.REPLAYED), g.HardRiskDisposition.CREATED if created else g.HardRiskDisposition.REPLAYED)
        class Outbox:
            def persist_admission(self, connection, value, mapped):
                if fail_kind == "outbox": raise g.EntryAdmissionConflict("typed outbox conflict")
                created = outer._insert("outbox", "event-1")
                return g.OutboxPersistResult("event-1", g.OutboxDisposition.CREATED if created else g.OutboxDisposition.REPLAYED)
        return g.CanonicalEntryAdmissionGateway(mapper=Mapper(), command_graphs=Commands(), hard_risk=Risks(), outbox=Outbox())

    def _count(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM entry_gateway_facts")
            return cursor.fetchone()[0]

    def test_outer_commit_replay_and_rollback_are_atomic(self):
        value = draft(c.EntryMode.PAPER)
        self.assertEqual(self._gateway().admit(self.connection, value).disposition, g.EntryAdmissionDisposition.CREATED)
        self.connection.commit()
        self.assertEqual(self._count(), 3)
        self.assertEqual(self._gateway().admit(self.connection, value).disposition, g.EntryAdmissionDisposition.REPLAYED)
        self.connection.rollback()
        self.assertEqual(self._count(), 3)
        for failed_port in ("command", "risk", "outbox"):
            with self.subTest(failed_port=failed_port):
                with self.assertRaises(g.EntryAdmissionConflict): self._gateway(fail_kind=failed_port).admit(self.connection, value)
                self.connection.rollback()
                self.assertEqual(self._count(), 3)
        with self.connection.cursor() as cursor: cursor.execute("SELECT 1")

    def test_deny_persists_no_reservation_or_outbox_and_outer_rollback_erases_it(self):
        value = draft(c.EntryMode.PAPER)
        self.assertEqual(self._gateway(deny=True).admit(self.connection, value).disposition, g.EntryAdmissionDisposition.RISK_REJECTED)
        self.connection.rollback()
        self.assertEqual(self._count(), 0)
        self.assertEqual(self._gateway(deny=True).admit(self.connection, value).disposition, g.EntryAdmissionDisposition.RISK_REJECTED)
        self.connection.commit()
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT kind FROM entry_gateway_facts ORDER BY kind")
            self.assertEqual([row[0] for row in cursor.fetchall()], ["command", "risk-denial"])
