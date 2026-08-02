import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = (
        "app", "app.domain", "app.services",
        "app.domain.readonly_paper_account_contracts",
        "app.services.readonly_paper_account_repository",
        "app.services.readonly_paper_account_service",
    )
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        for name, relative in (
            (names[3], "app/domain/readonly_paper_account_contracts.py"),
            (names[4], "app/services/readonly_paper_account_repository.py"),
            (names[5], "app/services/readonly_paper_account_service.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, ROOT / relative)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[names[3]], sys.modules[names[4]], sys.modules[names[5]]
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


C, R, S = _load()
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.sql = None

    def execute(self, sql, params):
        self.sql = (sql, params)

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, rows):
        self.cursor_obj = Cursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj


def row(status="filled"):
    return ("paper-1", "Crypto", "BTC_USDT", "buy", "market", Decimal("1.000"), None, Decimal("100"), Decimal("100"), status, "fixture", NOW)


class ReadonlyPaperAccountTests(unittest.TestCase):
    def test_repository_reads_decimal_facts_without_transaction_control(self):
        connection = Connection([row()])
        snapshot = R.ReadonlyPaperAccountRepository().read(connection, user_id=7)
        self.assertEqual(snapshot.filled_count, 1)
        self.assertEqual(snapshot.orders[0].quantity, Decimal("1"))
        self.assertTrue(connection.cursor_obj.closed)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)

    def test_legacy_timestamp_is_explicitly_normalized_to_utc(self):
        legacy = list(row())
        legacy[-1] = datetime(2026, 1, 1)
        snapshot = R.ReadonlyPaperAccountRepository().read(Connection([tuple(legacy)]), user_id=7)
        self.assertEqual(snapshot.orders[0].created_at.tzinfo, timezone.utc)

    def test_snapshot_fingerprint_and_public_output_are_stable(self):
        connection = Connection([row()])
        first = R.ReadonlyPaperAccountRepository().read(connection, user_id=7)
        second = R.ReadonlyPaperAccountRepository().read(Connection([row()]), user_id=7)
        self.assertEqual(first.snapshot_fingerprint, second.snapshot_fingerprint)
        public = first.to_public_dict()
        self.assertEqual(public["orders"][0]["quantity"], "1")
        self.assertFalse(public["live_enabled"])

    def test_malformed_numeric_row_fails_closed(self):
        with self.assertRaises(R.ReadonlyPaperAccountRepositoryError):
            R.ReadonlyPaperAccountRepository().read(Connection([("paper-1", "Crypto", "BTC_USDT", "buy", "market", 0.1, None, None, None, "filled", "", NOW)]), user_id=7)

    def test_service_returns_unavailable_without_provider(self):
        status, body = S.ReadonlyPaperAccountService().read_response(user_id=7)
        self.assertEqual(status, 503)
        self.assertFalse(body["live_enabled"])


if __name__ == "__main__":
    unittest.main()
