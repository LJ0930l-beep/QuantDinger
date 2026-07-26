"""PR-05 must remain a persistence/reducer boundary, never a trading caller."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUBJECTS = (
    ROOT / "app" / "domain" / "order_state_machine.py",
    ROOT / "app" / "domain" / "submission_recovery_contracts.py",
    ROOT / "app" / "services" / "order_state_repository.py",
    ROOT / "app" / "services" / "submission_recovery_repository.py",
)
FORBIDDEN = (
    "submit_order", "create_order", "cancel_order", "PendingOrderWorker", "TradingExecutor",
    "app.services.live_trading", "exchange_adapter",
)


class PR05ArchitectureBoundaryTests(unittest.TestCase):
    def test_new_modules_do_not_call_existing_trading_or_worker_paths(self):
        for subject in SUBJECTS:
            source = subject.read_text(encoding="utf-8")
            for token in FORBIDDEN:
                with self.subTest(subject=subject.name, token=token):
                    self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
