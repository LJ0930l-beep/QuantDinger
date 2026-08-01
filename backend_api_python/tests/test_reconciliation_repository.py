from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import UUID

from tests.pr09_contract_loader import load_pr09_repository


modules = load_pr09_repository()
s = modules.contracts
r = modules.repository
NOW = datetime(2026, 7, 28, 9, 1, tzinfo=timezone.utc)
SHA = "a" * 64


def comparison():
    fact = s.ReconciliationFactValue("1", s.ReconciliationFactKind.QUANTITY, "BTC")
    external = s.ReconciliationSourceSnapshot("venue", "facts-v1", 1, 2, "primary", "binance", "swap", "BTCUSDT", None, NOW, {"position": fact})
    local = s.ReconciliationSourceSnapshot("local", "facts-v1", 1, 2, "primary", "binance", "swap", "BTCUSDT", None, NOW, {"position": fact}, UUID("22222222-2222-2222-2222-222222222222"), 7)
    run = s.ReconciliationRun(
        UUID("11111111-1111-1111-1111-111111111111"), 1, 2, "primary", "binance", "swap", "BTCUSDT", None,
        UUID("22222222-2222-2222-2222-222222222222"), "ledger", SHA, 7,
        external.source_identity, external.source_version, external.source_fingerprint,
        NOW, NOW, NOW, "audit-correlation", s.ReconciliationPolicySnapshot("reconciliation-policy-v1", True),
    )
    return s.compare_reconciliation_state(run, local, external)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters=()):
        if "INSERT INTO qd_reconciliation_runs" in statement:
            if statement.count("%s") != len(parameters):
                raise AssertionError("reconciliation run SQL placeholders must match canonical parameters")
        self.statements.append((" ".join(statement.split()), parameters))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Connection:
    def __init__(self, rows):
        self.cursor_object = Cursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_object

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ReconciliationRepositoryTests(unittest.TestCase):
    def _generation(self, result):
        run = result.run
        return (run.local_consumer_name, run.local_generation_build_fingerprint, "READY", 7, 7, NOW)

    def test_created_result_is_caller_owned_and_has_no_commit_or_rollback(self):
        result = comparison()
        connection = Connection([self._generation(result), (result.run.run_id,), (result.run.run_id,), ("checkpoint",)])
        persisted = r.ReconciliationRepository().persist_result(connection, result, completed_at=NOW)
        self.assertEqual(persisted.disposition, r.ReconciliationPersistDisposition.CREATED)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        statements = "\n".join(item[0] for item in connection.cursor_object.statements)
        self.assertIn("FOR UPDATE", statements)
        self.assertIn("qd_reconciliation_checkpoints", statements)

    def test_generation_not_ready_and_driver_errors_are_typed(self):
        result = comparison()
        connection = Connection([("ledger", SHA, "BUILDING", 7, 7, NOW)])
        with self.assertRaises(r.ReconciliationRunConflict):
            r.ReconciliationRepository().persist_result(connection, result, completed_at=NOW)

        class BrokenConnection:
            def cursor(self):
                raise RuntimeError("driver failure")

        with self.assertRaises(r.ReconciliationRepositoryError) as caught:
            r.ReconciliationRepository().persist_result(BrokenConnection(), result, completed_at=NOW)
        self.assertNotIn("driver failure", str(caught.exception))

    def test_repository_has_no_transaction_control_text(self):
        with open(r.__file__, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(".commit(", text)
        self.assertNotIn(".rollback(", text)
        self.assertIn("c.reconciliation_run_id", text)
        self.assertNotIn("c.run_id", text)


if __name__ == "__main__":
    unittest.main()
