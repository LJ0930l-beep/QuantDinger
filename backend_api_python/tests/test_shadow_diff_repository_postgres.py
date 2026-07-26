"""PostgreSQL contracts for PR-08 immutable shadow comparison persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import unittest
import uuid

from tests.pr08_contract_loader import load_pr08_repository


MODULES = load_pr08_repository()
s = MODULES.contracts
r = MODULES.repository
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INIT_SQL = MIGRATIONS / "init.sql"
INCREMENTAL = tuple(sorted(path for path in MIGRATIONS.glob("2026*.sql") if path.name != "init.sql"))


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class ShadowDiffRepositoryPostgresTests(unittest.TestCase):
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

    def _result(self, *, suffix=None, run_id=None, candidate_value="1"):
        suffix = suffix or uuid.uuid4().hex
        connection = self._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id", (f"shadow_{suffix}", "test"))
                tenant_id = cursor.fetchone()[0]
                cursor.execute("INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) VALUES (%s, %s, %s) RETURNING id", (tenant_id, f"shadow-{suffix}", "{}"))
                credential_id = cursor.fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        observed = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
        policy = s.ShadowTolerancePolicy("shadow-policy-v1", quantity_absolute="0")
        run = s.ShadowComparisonRun(run_id or uuid.uuid4(), tenant_id, credential_id, "primary", "BTCUSDT", "swap", policy, "a" * 64)
        legacy = s.ShadowSourceSnapshot("legacy", tenant_id, credential_id, "primary", "BTCUSDT", "swap", "v1", observed, s.ShadowSourceStatus.READY, {"position": s.ShadowFactValue("1", s.ShadowValueKind.QUANTITY, "BTC")})
        candidate = s.ShadowSourceSnapshot("candidate", tenant_id, credential_id, "primary", "BTCUSDT", "swap", "v1", observed, s.ShadowSourceStatus.READY, {"position": s.ShadowFactValue(candidate_value, s.ShadowValueKind.QUANTITY, "BTC")})
        return s.compare_shadow_state(run, legacy, candidate)

    def test_atomic_create_replay_conflict_and_append_only_guard(self):
        result = self._result(candidate_value="2")
        repository = r.ShadowDiffRepository()
        first = self._connection()
        try:
            created = repository.persist_comparison(first, result, completed_at=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc))
            self.assertEqual(created.disposition, r.ShadowPersistDisposition.CREATED)
            first.commit()
        finally:
            first.close()
        second = self._connection()
        try:
            replay = repository.persist_comparison(second, result, completed_at=datetime(2026, 7, 26, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(replay.disposition, r.ShadowPersistDisposition.REPLAYED)
            with second.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM qd_shadow_comparison_runs WHERE id = %s", (result.run.run_id,))
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SELECT COUNT(*) FROM qd_shadow_diff_facts WHERE run_id = %s", (result.run.run_id,))
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SAVEPOINT immutable_shadow")
                with self.assertRaises(self.psycopg2.Error):
                    cursor.execute("UPDATE qd_shadow_diff_facts SET detail = 'changed' WHERE run_id = %s", (result.run.run_id,))
                cursor.execute("ROLLBACK TO SAVEPOINT immutable_shadow")
                cursor.execute("RELEASE SAVEPOINT immutable_shadow")
            second.rollback()
        finally:
            second.close()

    def test_two_connections_return_created_and_replayed_without_raw_driver_error(self):
        result = self._result(candidate_value="2")
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def persist():
            connection = self._connection()
            try:
                barrier.wait(timeout=10)
                value = r.ShadowDiffRepository().persist_comparison(connection, result, completed_at=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc))
                connection.commit()
                outcome = value.disposition
            except Exception as exc:  # failure itself is asserted after both threads join
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
            self.assertFalse(thread.is_alive(), "shadow persistence concurrency test timed out")
        self.assertEqual(sorted(outcomes, key=str), sorted((r.ShadowPersistDisposition.CREATED, r.ShadowPersistDisposition.REPLAYED), key=str))


if __name__ == "__main__":
    unittest.main()
