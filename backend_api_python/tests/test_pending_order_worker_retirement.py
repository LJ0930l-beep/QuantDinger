from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend_api_python/app/services/pending_order_worker.py"


def _method(name: str) -> ast.FunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing method {name}")


class PendingOrderWorkerRetirementTests(unittest.TestCase):
    def test_start_is_terminally_disabled_before_database_or_thread_work(self):
        method = _method("start")
        self.assertIsInstance(method.body[0], ast.Expr)
        self.assertIn("permanently disabled", ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), method.body[0]))
        self.assertIsInstance(method.body[1], ast.Return)
        self.assertIsInstance(method.body[1].value, ast.Constant)
        self.assertFalse(method.body[1].value.value)

    def test_tick_returns_before_legacy_queue_or_exchange_dispatch(self):
        method = _method("_tick")
        self.assertIsInstance(method.body[0], ast.Return)
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("legacy queue is permanently disabled", source)
        self.assertNotIn("ENABLE_PENDING_ORDER_WORKER=true", source)

    def test_retirement_does_not_add_a_new_direct_order_call(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("place_new_order", source)
        self.assertNotIn("create_live_order", source)


if __name__ == "__main__":
    unittest.main()
