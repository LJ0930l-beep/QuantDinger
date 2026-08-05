from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function not found: {name}")


def _first_effective_statement(function: ast.FunctionDef) -> ast.stmt:
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        raise AssertionError(f"empty function: {function.name}")
    return body[0]


class RestrictedEntryRetirementTests(unittest.TestCase):
    def _parse(self, relative: str) -> ast.Module:
        path = ROOT / relative
        return ast.parse(path.read_text(encoding="utf-8"), filename=relative)

    def test_alpaca_order_and_cancel_are_terminal_410(self):
        tree = self._parse("backend_api_python/app/routes/alpaca.py")
        for name in ("place_order", "cancel_order"):
            statement = _first_effective_statement(_function(tree, name))
            self.assertIsInstance(statement, ast.Return)
            self.assertIsInstance(statement.value, ast.Tuple)
            self.assertEqual(ast.literal_eval(statement.value.elts[1]), 410)

    def test_ibkr_order_and_cancel_are_terminal_410(self):
        tree = self._parse("backend_api_python/app/routes/ibkr.py")
        for name in ("place_order", "cancel_order"):
            statement = _first_effective_statement(_function(tree, name))
            self.assertIsInstance(statement, ast.Return)
            self.assertIsInstance(statement.value, ast.Tuple)
            self.assertEqual(ast.literal_eval(statement.value.elts[1]), 410)

    def test_grid_runner_startup_and_tick_have_no_reactivation_path(self):
        tree = self._parse("backend_api_python/app/services/grid/runner.py")
        for name in ("startup", "tick"):
            statement = _first_effective_statement(_function(tree, name))
            self.assertIsInstance(statement, ast.Return)

    def test_retirement_messages_are_explicit_and_no_toggle_is_present(self):
        for relative in (
            "backend_api_python/app/routes/alpaca.py",
            "backend_api_python/app/routes/ibkr.py",
            "backend_api_python/app/services/grid/runner.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("ENABLE_ALPACA", source)
            self.assertNotIn("ENABLE_IBKR", source)
            self.assertNotIn("ENABLE_GRID", source)
        self.assertIn("permanently disabled", (ROOT / "backend_api_python/app/services/grid/runner.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
