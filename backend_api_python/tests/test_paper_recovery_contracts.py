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
        "app.domain.paper_recovery_contracts",
        "app.services.readonly_paper_recovery_service",
    )
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        for name, relative in (
            (names[3], "app/domain/readonly_paper_account_contracts.py"),
            (names[4], "app/domain/paper_recovery_contracts.py"),
            (names[5], "app/services/readonly_paper_recovery_service.py"),
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


def snapshot():
    order = C.ReadonlyPaperOrderFact(
        "paper-recovery-1", "spot", "BTC_USDT", "buy", "market",
        Decimal("1"), None, Decimal("100"), Decimal("100"),
        C.PaperOrderStatus.FILLED, "fixture", NOW,
    )
    return C.ReadonlyPaperAccountSnapshot(7, (order,), NOW)


class PaperRecoveryTests(unittest.TestCase):
    def test_replay_requires_an_explicit_checkpoint(self):
        result = R.verify_paper_snapshot_recovery(snapshot())
        self.assertIs(result.status, R.PaperRecoveryStatus.CHECKPOINT_REQUIRED)
        self.assertTrue(result.replay_fingerprint)
        self.assertFalse(result.to_public_dict()["live_enabled"])

    def test_exact_snapshot_fingerprint_verifies_and_is_deterministic(self):
        value = snapshot()
        first = R.verify_paper_snapshot_recovery(value, expected_snapshot_fingerprint=value.snapshot_fingerprint)
        second = R.verify_paper_snapshot_recovery(value, expected_snapshot_fingerprint=value.snapshot_fingerprint)
        self.assertIs(first.status, R.PaperRecoveryStatus.VERIFIED)
        self.assertEqual(first, second)
        self.assertEqual(first.recovery_fingerprint, second.recovery_fingerprint)

    def test_changed_checkpoint_is_mismatch_without_guessing(self):
        result = R.verify_paper_snapshot_recovery(snapshot(), expected_snapshot_fingerprint="0" * 64)
        self.assertIs(result.status, R.PaperRecoveryStatus.MISMATCH)

    def test_service_keeps_provider_read_only_and_returns_typed_evidence(self):
        calls = []
        service = S.ReadonlyPaperRecoveryService(lambda user_id, limit: (calls.append((user_id, limit)) or snapshot()))
        status, body = service.read_response(user_id=7, limit=20, expected_snapshot_fingerprint=snapshot().snapshot_fingerprint)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "VERIFIED")
        self.assertEqual(calls, [(7, 20)])

    def test_service_does_not_treat_missing_provider_as_recovered(self):
        status, body = S.ReadonlyPaperRecoveryService().read_response(user_id=7)
        self.assertEqual(status, 503)
        self.assertFalse(body["live_enabled"])


if __name__ == "__main__":
    unittest.main()
