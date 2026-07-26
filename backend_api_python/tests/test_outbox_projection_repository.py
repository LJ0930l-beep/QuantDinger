"""Focused unit contracts for transactional outbox persistence boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from tests.pr07_contract_loader import load_outbox_projection_repository


MODULES = load_outbox_projection_repository()
OutboxEvent = MODULES.OutboxEvent
OutboxProjectionRepository = MODULES.OutboxProjectionRepository
OutboxPersistDisposition = MODULES.OutboxPersistDisposition
OutboxLeaseConflict = MODULES.OutboxLeaseConflict


class _Cursor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []
        self.closed = False

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def fetchone(self):
        return self.responses.pop(0) if self.responses else None

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


class OutboxProjectionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.event = OutboxEvent(
            aggregate_type="EconomicOrder",
            aggregate_id=str(uuid4()),
            aggregate_version=0,
            event_type="ORDER_CREATED",
            schema_version="v1",
            payload={"state": "CREATED"},
        )
        self.now = datetime(2026, 7, 26, tzinfo=timezone.utc)

    def test_persist_created_event_commits_exactly_once(self):
        cursor = _Cursor([(self.event.event_id,)])
        connection = _Connection(cursor)
        result = OutboxProjectionRepository().persist_event(connection, self.event, available_at=self.now)
        self.assertEqual(result.disposition, OutboxPersistDisposition.CREATED)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(cursor.closed)
        self.assertIn("ON CONFLICT DO NOTHING", cursor.queries[0][0])

    def test_lease_increments_fencing_token_without_worker_side_effect(self):
        row = (
            self.event.event_id, self.event.aggregate_type, self.event.aggregate_id,
            self.event.aggregate_version, self.event.event_type, {"state": "CREATED"},
            self.event.schema_version, self.event.payload_hash, 9,
        )
        cursor = _Cursor([row])
        lease = OutboxProjectionRepository().lease_next(
            _Connection(cursor), lease_owner="projection-publisher",
            now_utc=self.now, lease_duration=timedelta(seconds=30),
        )
        self.assertIsNotNone(lease)
        self.assertEqual(lease.lease_fencing_token, 9)
        self.assertEqual(lease.event, self.event)
        self.assertIn("FOR UPDATE SKIP LOCKED", cursor.queries[0][0])

    def test_mark_published_rejects_lost_lease_and_rolls_back(self):
        leased = MODULES.LeasedOutboxEvent(self.event, "projection-publisher", 1, self.now + timedelta(seconds=30))
        cursor = _Cursor([None])
        connection = _Connection(cursor)
        with self.assertRaises(OutboxLeaseConflict):
            OutboxProjectionRepository().mark_published(connection, leased, published_at=self.now)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)

    def test_non_utc_lease_clock_fails_closed_before_database_access(self):
        cursor = _Cursor([])
        connection = _Connection(cursor)
        with self.assertRaises(MODULES.OutboxRepositoryError):
            OutboxProjectionRepository().lease_next(
                connection, lease_owner="projection-publisher",
                now_utc=datetime(2026, 7, 26, 8, tzinfo=timezone(timedelta(hours=8))),
                lease_duration=timedelta(seconds=1),
            )
        self.assertEqual(cursor.queries, [])
