"""PostgreSQL contracts for immutable reconciliation facts and derived health."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import unittest
import uuid

from tests.pr09_contract_loader import load_pr09_repository


MODULES = load_pr09_repository()
s = MODULES.contracts
r = MODULES.repository
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INIT_SQL = MIGRATIONS / "init.sql"
INCREMENTAL = tuple(sorted(path for path in MIGRATIONS.glob("2026*.sql") if path.name != "init.sql"))


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class ReconciliationRepositoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                for migration in INCREMENTAL:
                    cursor.execute(migration.read_text(encoding="utf-8"))
        finally:
            connection.commit()
            connection.close()

    def _connection(self):
        return self.psycopg2.connect(os.environ["DATABASE_URL"])

    def _result(self, *, suffix=None, run_id=None, external_value="1"):
        suffix = suffix or uuid.uuid4().hex
        connection = self._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id", (f"reconcile_{suffix}", "test"))
                tenant_id = cursor.fetchone()[0]
                cursor.execute("INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) VALUES (%s, %s, %s) RETURNING id", (tenant_id, f"reconcile-{suffix}", "{}"))
                credential_id = cursor.fetchone()[0]
                generation_id = uuid.uuid4()
                cursor.execute(
                    """INSERT INTO qd_projection_generations (
                           id, consumer_name, build_fingerprint, state, source_high_watermark,
                           processed_high_watermark, expected_event_count, applied_event_count, completed_at
                       ) VALUES (%s,%s,%s,'READY',7,7,0,0,NOW())""",
                    (str(generation_id), f"reconcile-{suffix}", "b" * 64),
                )
            connection.commit()
        finally:
            connection.close()
        observed = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
        fact = s.ReconciliationFactValue(external_value, s.ReconciliationFactKind.QUANTITY, "BTC")
        external = s.ReconciliationSourceSnapshot("venue", "facts-v1", tenant_id, credential_id, "primary", "binance", "swap", "BTCUSDT", None, observed, {"position": fact})
        local = s.ReconciliationSourceSnapshot("local", "facts-v1", tenant_id, credential_id, "primary", "binance", "swap", "BTCUSDT", None, observed, {"position": s.ReconciliationFactValue("1", s.ReconciliationFactKind.QUANTITY, "BTC")})
        run = s.ReconciliationRun(
            run_id or uuid.uuid4(), tenant_id, credential_id, "primary", "binance", "swap", "BTCUSDT", None,
            generation_id, f"reconcile-{suffix}", "b" * 64, 7, external.source_identity,
            external.source_version, external.source_fingerprint, observed, observed, observed,
            "audit-correlation", s.ReconciliationPolicySnapshot("reconciliation-policy-v1", True),
        )
        return s.compare_reconciliation_state(run, local, external)

    def test_atomic_replay_immutable_guards_and_complete_run_rejects_late_discrepancy(self):
        result = self._result(external_value="2")
        first = self._connection()
        try:
            created = r.ReconciliationRepository().persist_result(first, result, completed_at=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc))
            self.assertEqual(created.disposition, r.ReconciliationPersistDisposition.CREATED)
            first.commit()
        finally:
            first.close()
        second = self._connection()
        try:
            replay = r.ReconciliationRepository().persist_result(second, result, completed_at=datetime(2026, 7, 28, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(replay.disposition, r.ReconciliationPersistDisposition.REPLAYED)
            with second.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM qd_reconciliation_runs WHERE id = %s", (result.run.run_id,))
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SAVEPOINT immutable_reconciliation")
                with self.assertRaises(self.psycopg2.Error):
                    cursor.execute("UPDATE qd_reconciliation_discrepancies SET detail='changed' WHERE run_id=%s", (result.run.run_id,))
                cursor.execute("ROLLBACK TO SAVEPOINT immutable_reconciliation")
                cursor.execute("RELEASE SAVEPOINT immutable_reconciliation")
            second.rollback()
        finally:
            second.close()

    def test_two_connections_return_created_and_replayed_without_raw_driver_error(self):
        result = self._result(external_value="2")
        barrier, outcomes, lock = threading.Barrier(2), [], threading.Lock()

        def persist():
            connection = self._connection()
            try:
                barrier.wait(timeout=10)
                value = r.ReconciliationRepository().persist_result(connection, result, completed_at=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc))
                connection.commit()
                outcome = value.disposition
            except Exception as exc:
                connection.rollback()
                outcome = exc
            finally:
                connection.close()
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=persist) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive(), "reconciliation persistence concurrency test timed out")
        self.assertEqual(sorted(outcomes, key=str), sorted((r.ReconciliationPersistDisposition.CREATED, r.ReconciliationPersistDisposition.REPLAYED), key=str))


if __name__ == "__main__":
    unittest.main()
