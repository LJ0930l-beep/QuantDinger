from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = ("app", "app.services", "app.services.paper_execution_account_service", "app.services.paper_execution_repository", "app.services.paper_execution_recovery_service")
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        account = ModuleType(names[2]); repository = ModuleType(names[3])
        account_calls = []
        def read_account(connection, *, user_id, limit):
            account_calls.append((connection, user_id, limit))
            return SimpleNamespace(snapshot_fingerprint="snapshot-hash", orders=(SimpleNamespace(order_uid="order-1"),))
        account.read_durable_paper_account = read_account
        class Repository:
            records = []
            def record_recovery_checkpoint(self, connection, **kwargs): self.records.append((connection, kwargs))
        repository.PaperExecutionRepository = Repository
        repository.PaperExecutionRepositoryError = RuntimeError
        sys.modules.update({"app": app, "app.services": services, names[2]: account, names[3]: repository})
        spec = importlib.util.spec_from_file_location(names[4], ROOT / "app" / "services" / "paper_execution_recovery_service.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec); sys.modules[names[4]] = module; spec.loader.exec_module(module)
        return module, account, repository
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = original


RECOVERY, ACCOUNT, REPOSITORY = _load()


class _Cursor:
    def __init__(self, row=None): self.row = row; self.closed = False
    def execute(self, *_args): return None
    def fetchone(self): return self.row
    def close(self): self.closed = True


class _Connection:
    def __init__(self, row=None): self.cursor_obj = _Cursor(row); self.commits = 0; self.rollbacks = 0
    def cursor(self): return self.cursor_obj
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class PaperRecoveryTests(unittest.TestCase):
    def test_recovery_appends_ready_checkpoint_without_owning_transaction(self):
        connection = _Connection((4,))
        result = RECOVERY.recover_durable_paper_account(connection, user_id=7, recovered_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual((result.checkpoint_version, result.snapshot_fingerprint, result.order_count), (5, "snapshot-hash", 1))
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(REPOSITORY.PaperExecutionRepository.records[-1][1]["status"], "READY")

    def test_recovery_rejects_non_utc_time_without_transaction_side_effect(self):
        connection = _Connection()
        with self.assertRaises(RECOVERY.PaperExecutionRecoveryError):
            RECOVERY.recover_durable_paper_account(connection, user_id=7, recovered_at=datetime(2026, 1, 1))
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))


if __name__ == "__main__":
    unittest.main()
