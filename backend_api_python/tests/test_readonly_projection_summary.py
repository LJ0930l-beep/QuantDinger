from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import types
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
app = types.ModuleType("app"); app.__path__ = [str(ROOT / "app")]
domain = types.ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
services = types.ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app", app); sys.modules.setdefault("app.domain", domain); sys.modules.setdefault("app.services", services)

from app.domain.readonly_projection_summary_contracts import ReadonlyProjectionGenerationSummary, ReadonlyProjectionSummaryError
from app.services.readonly_projection_repository import ReadonlyProjectionRepository, ReadonlyProjectionRepositoryError
from app.services.readonly_projection_summary_service import ReadonlyProjectionSummaryService, ReadonlyProjectionSummaryServiceError


UTC = timezone.utc
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
        self.closed = False

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, rows):
        self.cursor_value = Cursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value


def row(state="READY"):
    return (
        str(uuid4()), "candidate", "a" * 64, state,
        10, 10, 10, 10,
    )


class ReadonlyProjectionSummaryTests(unittest.TestCase):
    def test_contract_is_typed_and_fingerprinted(self):
        value = ReadonlyProjectionGenerationSummary(
            str(uuid4()), "candidate", "a" * 64, "READY", 10, 10, 10, 10, 2, NOW
        )
        self.assertEqual(len(value.summary_fingerprint), 64)
        self.assertFalse(value.to_public_dict()["live_enabled"])
        with self.assertRaises(ReadonlyProjectionSummaryError):
            ReadonlyProjectionGenerationSummary(str(uuid4()), "candidate", "a" * 64, "READY", 1, 2, 1, 1, 0, NOW)

    def test_repository_is_select_only_and_closes_cursor(self):
        connection = Connection([row(), (2,)])
        result = ReadonlyProjectionRepository().read_latest_generation(connection, consumer_name="candidate", as_of=NOW)
        self.assertIsNotNone(result)
        self.assertEqual(result.checkpoint_count, 2)
        self.assertTrue(connection.cursor_value.closed)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(len(connection.cursor_value.queries), 2)

    def test_missing_generation_is_unavailable(self):
        connection = Connection([None])
        self.assertIsNone(ReadonlyProjectionRepository().read_latest_generation(connection, consumer_name="candidate", as_of=NOW))

    def test_service_rejects_untyped_provider(self):
        service = ReadonlyProjectionSummaryService(lambda consumer, as_of: {"state": "READY"})
        with self.assertRaises(ReadonlyProjectionSummaryServiceError):
            service.read_response(consumer_name="candidate", as_of=NOW)


if __name__ == "__main__":
    unittest.main()
