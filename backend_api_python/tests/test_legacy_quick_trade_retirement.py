"""Static fail-closed proof for legacy Quick Trade mutation routes."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROUTE = Path(__file__).resolve().parents[1] / "app" / "routes" / "quick_trade.py"


class LegacyQuickTradeRetirementTests(unittest.TestCase):
    def test_legacy_mutation_handlers_return_410_before_historical_execution_body(self):
        tree = ast.parse(ROUTE.read_text(encoding="utf-8"))
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {"place_order", "close_position"}
        }
        self.assertEqual(set(functions), {"place_order", "close_position"})
        for name, function in functions.items():
            body = function.body[1:] if (
                function.body and isinstance(function.body[0], ast.Expr)
                and isinstance(function.body[0].value, ast.Constant)
                and isinstance(function.body[0].value.value, str)
            ) else function.body
            self.assertIsInstance(body[0], ast.Return, f"{name} must fail closed before legacy logic")
            returned = body[0].value
            self.assertIsInstance(returned, ast.Tuple)
            self.assertEqual(getattr(returned.elts[1], "value", None), 410)


if __name__ == "__main__":
    unittest.main()
