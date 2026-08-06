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
ProjectionGeneration = MODULES.ProjectionGeneration
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

    def test_outbox_database_guard_rejects_mutation_and_delete(self):
        import psycopg2

        event = self._event()
        self.repository.persist_event(self.connection, event, available_at=NOW)
        with self.connection.cursor() as cursor:
            cursor.execute("SAVEPOINT outbox_immutable")
            try:
                with self.assertRaises(psycopg2.Error):
                    cursor.execute("UPDATE qd_transactional_outbox SET payload_hash = %s WHERE event_id = %s", ("0" * 64, event.event_id))
                cursor.execute("ROLLBACK TO SAVEPOINT outbox_immutable")
                with self.assertRaises(psycopg2.Error):
                    cursor.execute("DELETE FROM qd_transactional_outbox WHERE event_id = %s", (event.event_id,))
            finally:
                cursor.execute("ROLLBACK TO SAVEPOINT outbox_immutable")
                cursor.execute("RELEASE SAVEPOINT outbox_immutable")

    def test_lease_fencing_allows_only_exact_owner_and_token_to_publish(self):
        event = self._event()
        # Use an isolated historical instant so committed rows left by an
        # interrupted local run (which use the shared NOW fixture) cannot be
        # selected by the repository's global FIFO lease query.
        lease_now = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.repository.persist_event(self.connection, event, available_at=lease_now)
        lease_owner = f"postgres-projection-worker-{uuid4().hex}"
        lease = self.repository.lease_next(
            self.connection, lease_owner=lease_owner, now_utc=lease_now,
            lease_duration=timedelta(seconds=10),
        )
        self.assertIsNotNone(lease)
        wrong = MODULES.LeasedOutboxEvent(event, lease_owner, lease.lease_fencing_token + 1, lease.lease_expires_at)
        with self.assertRaises(OutboxLeaseConflict):
            self.repository.mark_published(self.connection, wrong, published_at=NOW)
        self.repository.mark_published(self.connection, lease, published_at=lease_now)
        self.assertIsNone(self.repository.lease_next(
            self.connection, lease_owner=lease_owner, now_utc=lease_now,
            lease_duration=timedelta(seconds=10),
        ))

    def test_projection_checkpoint_is_atomic_replay_safe_and_gap_closed(self):
        aggregate_id = str(uuid4())
        first = OutboxEvent("ECONOMIC_ORDER", aggregate_id, 0, "FILL_APPLIED", "v1", {"fill": "first"})
        gap = OutboxEvent("ECONOMIC_ORDER", aggregate_id, 2, "FILL_APPLIED", "v1", {"fill": "gap"})
        self.repository.persist_event(self.connection, first, available_at=NOW)
        self.repository.persist_event(self.connection, gap, available_at=NOW)
        supported = {("FILL_APPLIED", "v1")}
        generation = self.repository.start_rebuild(
            self.connection, consumer_name="ledger-read-model",
            build_fingerprint="b" * 64, source_high_watermark=0, expected_event_count=1,
        )
        applied = self.repository.apply_to_projection(
            self.connection, consumer_name="ledger-read-model", event=first,
            supported_schemas=supported, now_utc=NOW, generation_id=generation.generation_id, source_offset=0,
        )
        self.assertFalse(applied.result.idempotent_replay)
        replay = self.repository.apply_to_projection(
            self.connection, consumer_name="ledger-read-model", event=first,
            supported_schemas=supported, now_utc=NOW, generation_id=generation.generation_id, source_offset=0,
        )
        self.assertTrue(replay.result.idempotent_replay)
        with self.assertRaises(ProjectionGap):
            self.repository.apply_to_projection(
                self.connection, consumer_name="ledger-read-model", event=gap,
                supported_schemas=supported, now_utc=NOW, generation_id=generation.generation_id, source_offset=2,
            )
        ready = self.repository.complete_rebuild(self.connection, generation, now_utc=NOW)
        self.assertFalse(ready.is_current)
        self.assertTrue(self.repository.promote_rebuild(self.connection, ready, now_utc=NOW).is_current)

    def test_rebuild_generation_is_replay_safe_and_not_silently_reused(self):
        fingerprint = "a" * 64
        started = self.repository.start_rebuild(
            self.connection, consumer_name="ledger-read-model",
            build_fingerprint=fingerprint, source_high_watermark=7, expected_event_count=8,
        )
        replay = self.repository.start_rebuild(
            self.connection, consumer_name="ledger-read-model",
            build_fingerprint=fingerprint, source_high_watermark=7, expected_event_count=8,
        )
        self.assertEqual(replay.generation_id, started.generation_id)
        with self.assertRaises(ProjectionGenerationConflict):
            self.repository.start_rebuild(
                self.connection, consumer_name="ledger-read-model",
                build_fingerprint=fingerprint, source_high_watermark=8, expected_event_count=9,
            )
        failed = self.repository.fail_rebuild(
            self.connection,
            ProjectionGeneration(
                started.generation_id, started.consumer_name, started.build_fingerprint,
                999, "BUILDING", 999, 999, 999, True,
            ),
            failure_reason="source replay unavailable",
        )
        self.assertEqual(failed.state, "FAILED")
        self.assertEqual((failed.source_high_watermark, failed.expected_event_count), (7, 8))

    def test_generation_event_identity_and_offset_conflicts_fail_closed(self):
        first = self._event(0)
        other = self._event(0)
        self.repository.persist_event(self.connection, first, available_at=NOW)
        self.repository.persist_event(self.connection, other, available_at=NOW)
        generation = self.repository.start_rebuild(
            self.connection, consumer_name="generation-fact-reader",
            build_fingerprint="c" * 64, source_high_watermark=1, expected_event_count=2,
        )
        supported = {("FILL_APPLIED", "v1")}
        self.repository.apply_to_projection(
            self.connection, consumer_name="generation-fact-reader", event=first,
            supported_schemas=supported, now_utc=NOW, generation_id=generation.generation_id,
            source_offset=0,
        )
        with self.assertRaises(ProjectionGenerationConflict):
            self.repository.apply_to_projection(
                self.connection, consumer_name="generation-fact-reader", event=other,
                supported_schemas=supported, now_utc=NOW, generation_id=generation.generation_id,
                source_offset=0,
            )
        with self.assertRaises(ProjectionGenerationConflict):
            self.repository.apply_to_projection(
                self.connection, consumer_name="generation-fact-reader", event=first,
                supported_schemas=supported, now_utc=NOW, generation_id=generation.generation_id,
                source_offset=1,
            )

    def test_complete_rebuild_rejects_offset_gap_and_event_fact_tampering(self):
        import psycopg2

        first, last = self._event(0), self._event(0)
        self.repository.persist_event(self.connection, first, available_at=NOW)
        self.repository.persist_event(self.connection, last, available_at=NOW)
        generation = self.repository.start_rebuild(
            self.connection, consumer_name="gap-reader",
            build_fingerprint="d" * 64, source_high_watermark=2, expected_event_count=3,
        )
        supported = {("FILL_APPLIED", "v1")}
        for event, offset in ((first, 0), (last, 2)):
            self.repository.apply_to_projection(
                self.connection, consumer_name="gap-reader", event=event,
                supported_schemas=supported, now_utc=NOW, generation_id=generation.generation_id,
                source_offset=offset,
            )
        with self.assertRaises(ProjectionGenerationConflict):
            self.repository.complete_rebuild(self.connection, generation, now_utc=NOW)
        with self.connection.cursor() as cursor:
            cursor.execute("SAVEPOINT generation_event_immutable")
            try:
                with self.assertRaises(psycopg2.Error):
                    cursor.execute(
                        "UPDATE qd_projection_generation_events SET payload_hash = %s "
                        "WHERE generation_id = %s AND source_offset = 0",
                        ("0" * 64, generation.generation_id),
                    )
                cursor.execute("ROLLBACK TO SAVEPOINT generation_event_immutable")
                with self.assertRaises(psycopg2.Error):
                    cursor.execute(
                        "DELETE FROM qd_projection_generation_events "
                        "WHERE generation_id = %s AND source_offset = 0",
                        (generation.generation_id,),
                    )
            finally:
                cursor.execute("ROLLBACK TO SAVEPOINT generation_event_immutable")
                cursor.execute("RELEASE SAVEPOINT generation_event_immutable")

    def test_completion_and_failure_reload_database_generation_facts(self):
        event = self._event(0)
        self.repository.persist_event(self.connection, event, available_at=NOW)
        generation = self.repository.start_rebuild(
            self.connection, consumer_name="forged-reader",
            build_fingerprint="e" * 64, source_high_watermark=0, expected_event_count=1,
        )
        self.repository.apply_to_projection(
            self.connection, consumer_name="forged-reader", event=event,
            supported_schemas={("FILL_APPLIED", "v1")}, now_utc=NOW,
            generation_id=generation.generation_id, source_offset=0,
        )
        forged = ProjectionGeneration(
            generation.generation_id, generation.consumer_name, generation.build_fingerprint,
            999, "BUILDING", 999, 999, 999, True,
        )
        ready = self.repository.complete_rebuild(self.connection, forged, now_utc=NOW)
        self.assertEqual((ready.source_high_watermark, ready.expected_event_count, ready.applied_event_count), (0, 1, 1))
        promoted = self.repository.promote_rebuild(self.connection, forged.__class__(
            ready.generation_id, ready.consumer_name, ready.build_fingerprint, 999,
            "READY", 999, 999, 999, False,
        ), now_utc=NOW)
        self.assertTrue(promoted.is_current)

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
                connection.commit()
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
