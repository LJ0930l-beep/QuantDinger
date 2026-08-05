"""Static proof that retired mutation surfaces fail closed before legacy code."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _first_effective(node: ast.FunctionDef) -> ast.stmt:
    statements = list(node.body)
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(statements[0].value.value, str):
        statements = statements[1:]
    while statements and isinstance(statements[0], (ast.Import, ast.ImportFrom)):
        statements = statements[1:]
    if not statements:
        raise AssertionError(f"{node.name} is empty")
    return statements[0]


def _first_terminal(node: ast.FunctionDef) -> ast.stmt:
    statements = list(node.body)
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(statements[0].value.value, str):
        statements = statements[1:]
    for statement in statements:
        if isinstance(statement, (ast.Return, ast.Raise)):
            return statement
    raise AssertionError(f"{node.name} has no terminal statement")


class SC15TerminalGuardProofTests(unittest.TestCase):
    def test_route_and_helper_mutations_are_terminal(self):
        checks = {
            "app/routes/alpaca.py": {"place_order": ast.Return, "cancel_order": ast.Return},
            "app/routes/ibkr.py": {"place_order": ast.Return, "cancel_order": ast.Return},
            "app/routes/quick_trade.py": {"place_order": ast.Return, "close_position": ast.Return},
            "app/routes/agent_v1/quick_trade.py": {"place_order": ast.Return, "_place_live_order": ast.Raise},
            "app/services/quick_trade/orders.py": {"attach_quick_trade_protection": ast.Raise},
            "app/services/pending_order_worker.py": {"start": ast.Return, "_tick": ast.Return},
            "app/services/grid/exchange_orders.py": {"execute_grid_market_order": ast.Raise, "cancel_grid_order": ast.Raise},
            "app/services/grid/runner.py": {"startup": ast.Return, "tick": ast.Return},
        }
        for relative, expected in checks.items():
            functions = _functions(ROOT / relative)
            for name, statement_type in expected.items():
                self.assertIn(name, functions, f"missing {relative}:{name}")
                self.assertIsInstance(_first_terminal(functions[name]), statement_type, f"{relative}:{name} must fail closed before legacy body")

    def test_native_protection_provider_methods_are_terminal(self):
        functions = _functions(ROOT / "app/services/live_trading/native_protection.py")
        for name in ("_place_binance", "_place_okx", "_place_bitget", "_place_bybit", "_place_gate", "_place_htx"):
            self.assertIsInstance(_first_effective(functions[name]), ast.Raise, f"native protection {name} must remain disabled")

    def test_no_retirement_toggle_is_present(self):
        for relative in ("app/routes/quick_trade.py", "app/routes/agent_v1/quick_trade.py", "app/services/grid/runner.py", "app/services/pending_order_worker.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"ENABLE_(ALPACA|IBKR|GRID|QUICK_TRADE|LIVE)")


if __name__ == "__main__":
    unittest.main()
