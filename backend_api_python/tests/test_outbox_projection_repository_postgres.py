"""PostgreSQL atomicity, replay, and lease contracts for PR-07 storage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from tests.pr07_contract_loader import load_outbox_projection_repository


MODULES = load_outbox_projection_repository()
OutboxEvent = MODULES.OutboxEvent
OutboxProjectionRepository = MODULES.OutboxProjectionRepository
OutboxPersistDisposition = MODULES.OutboxPersistDisposition
OutboxConflict = MODULES.OutboxConflict
OutboxLeaseConflict = MODULES.OutboxLeaseConflict
ProjectionGenerationConflict = MODULES.ProjectionGenerationConflict
ProjectionGap = MODULES.ProjectionGap

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INIT_SQL = MIGRATIONS / "init.sql"
INCREMENTAL = tuple(sorted(MIGRATIONS.glob("2026072[2-5]_*.sql")))
NOW = datetime(2026, 7, 26, 6, 0, tzinfo=timezone.utc)


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class OutboxProjectionRepositoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute(INIT_SQL.read_text(encoding="utf-8"))
                for migration in INCREMENTAL:
                    cursor.execute(migration.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()

    def setUp(self):
        import psycopg2

        self.connection = psycopg2.connect(os.environ["DATABASE_URL"])
        self.connection.autocommit = False
        self.repository = OutboxProjectionRepository()

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _event(self, version=0, payload=None):
        return OutboxEvent(
            "ECONOMIC_ORDER", str(uuid4()), version, "FILL_APPLIED", "v1",
            payload or {"fill": f"fill-{version}"},
        )

    def test_persist_replays_only_identical_immutable_event(self):
        event = self._event()
        self.assertEqual(
            self.repository.persist_event(self.connection, event, available_at=NOW).disposition,
            OutboxPersistDisposition.CREATED,
        )
        self.assertEqual(
            self.repository.persist_event(self.connection, event, available_at=NOW).disposition,
            OutboxPersistDisposition.REPLAYED,
        )
        conflicting = OutboxEvent(
            event.aggregate_type, event.aggregate_id, event.aggregate_version,
            event.event_type, event.schema_version, {"fill": "different"},
        )
        with self.assertRaises(OutboxConflict):
            self.repository.persist_event(self.connection, conflicting, available_at=NOW)

    def test_lease_fencing_allows_only_exact_owner_and_token_to_publish(self):
        event = self._event()
        self.repository.persist_event(self.connection, event, available_at=NOW)
        lease = self.repository.lease_next(
            self.connection, lease_owner="postgres-projection-worker", now_utc=NOW,
            lease_duration=timedelta(seconds=10),
        )
        self.assertIsNotNone(lease)
        wrong = MODULES.LeasedOutboxEvent(event, "postgres-projection-worker", lease.lease_fencing_token + 1, lease.lease_expires_at)
        with self.assertRaises(OutboxLeaseConflict):
            self.repository.mark_published(self.connection, wrong, published_at=NOW)
        self.repository.mark_published(self.connection, lease, published_at=NOW)
        self.assertIsNone(self.repository.lease_next(
            self.connection, lease_owner="postgres-projection-worker", now_utc=NOW,
            lease_duration=timedelta(seconds=10),
        ))

    def test_projection_checkpoint_is_atomic_replay_safe_and_gap_closed(self):
        aggregate_id = str(uuid4())
        first = OutboxEvent("ECONOMIC_ORDER", aggregate_id, 0, "FILL_APPLIED", "v1", {"fill": "first"})
        gap = OutboxEvent("ECONOMIC_ORDER", aggregate_id, 2, "FILL_APPLIED", "v1", {"fill": "gap"})
        self.repository.persist_event(self.connection, first, available_at=NOW)
        self.repository.persist_event(self.connection, gap, available_at=NOW)
        supported = {("FILL_APPLIED", "v1")}
        applied = self.repository.apply_to_projection(
            self.connection, consumer_name="ledger-read-model", event=first,
            supported_schemas=supported, now_utc=NOW,
        )
        self.assertFalse(applied.result.idempotent_replay)
        replay = self.repository.apply_to_projection(
            self.connection, consumer_name="ledger-read-model", event=first,
            supported_schemas=supported, now_utc=NOW,
        )
        self.assertTrue(replay.result.idempotent_replay)
        with self.assertRaises(ProjectionGap):
            self.repository.apply_to_projection(
                self.connection, consumer_name="ledger-read-model", event=gap,
                supported_schemas=supported, now_utc=NOW,
            )

    def test_rebuild_generation_is_replay_safe_and_not_silently_reused(self):
        fingerprint = "a" * 64
        started = self.repository.start_rebuild(
            self.connection, consumer_name="ledger-read-model",
            build_fingerprint=fingerprint, source_high_watermark=7,
        )
        replay = self.repository.start_rebuild(
            self.connection, consumer_name="ledger-read-model",
            build_fingerprint=fingerprint, source_high_watermark=7,
        )
        self.assertEqual(replay.generation_id, started.generation_id)
        with self.assertRaises(ProjectionGenerationConflict):
            self.repository.start_rebuild(
                self.connection, consumer_name="ledger-read-model",
                build_fingerprint=fingerprint, source_high_watermark=8,
            )
        self.assertEqual(
            self.repository.complete_rebuild(self.connection, started, now_utc=NOW).state,
            "READY",
        )

    def test_two_connections_create_one_event_and_one_replay(self):
        import psycopg2

        event = self._event()
        barrier = threading.Barrier(2)
        outcomes, failures = [], []

        def persist_once():
            connection = psycopg2.connect(os.environ["DATABASE_URL"])
            try:
                barrier.wait(timeout=10)
                outcome = OutboxProjectionRepository().persist_event(connection, event, available_at=NOW)
                outcomes.append(outcome.disposition)
            except Exception as exc:  # test captures any raw driver failure
                failures.append(exc)
            finally:
                connection.close()

        workers = [threading.Thread(target=persist_once, daemon=True) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertCountEqual(outcomes, [OutboxPersistDisposition.CREATED, OutboxPersistDisposition.REPLAYED])
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM qd_transactional_outbox WHERE event_id = %s", (event.event_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
