from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "backend_api_python/app/services/strategy_v2/live_execution.py"


def _module():
    spec = importlib.util.spec_from_file_location("strategy_v2_live_execution_retirement", SOURCE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load retired strategy queue module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _submit_function() -> ast.FunctionDef:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"), filename=str(SOURCE_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "submit":
            return node
    raise AssertionError("submit function not found")


class StrategyV2QueueRetirementTests(unittest.TestCase):
    def test_submit_is_terminal_and_does_not_write_legacy_queue(self):
        function = _submit_function()
        self.assertEqual(len(function.body), 1)
        self.assertIsInstance(function.body[0], ast.Raise)
        message = ast.literal_eval(function.body[0].exc.args[0])
        self.assertEqual(message, "strategyV2.legacyQueueDisabled")
        source = SOURCE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("pending_orders", source)
        self.assertNotIn("OrderIntentService", source)
        self.assertNotIn("get_db_connection", source)

    def test_submit_fails_closed_without_validating_or_calling_database(self):
        module = _module()
        request = module.LiveOrderRequest(
            strategy_id=1,
            strategy_run_id=2,
            user_id=3,
            symbol="BTC/USDT",
            action="open_long",
            quantity=1.0,
            reference_price=100.0,
            signal_timestamp=1,
            market_type="swap",
            execution_mode="live",
        )
        with self.assertRaisesRegex(RuntimeError, "strategyV2\\.legacyQueueDisabled"):
            module.StrategyV2OrderGateway().submit(request)


if __name__ == "__main__":
    unittest.main()
