"""Regression tests for the versioned trading-entry inventory and AST guard."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

from architecture.entrypoint_convergence_guard import (
    compare_with_baseline,
    load_baseline,
    load_manifest,
    scan_entrypoint_bypasses,
)
from tests.pr11_contract_loader import load_pr11_contracts


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "backend_api_python/architecture/entrypoint_convergence_manifest.json"


class EntrypointConvergenceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(MANIFEST_PATH)

    def test_inventory_is_complete_explicit_and_uses_existing_entry_sources(self):
        contracts = load_pr11_contracts().entry
        expected_sources = {item.value for item in contracts.EntrySource}
        records = self.manifest["entry_points"]
        self.assertEqual({item["source"] for item in records}, expected_sources)
        required = {
            "source", "path", "symbol", "input_type", "current_target", "creates_order",
            "calls_executor", "calls_exchange_client", "calls_legacy_quick_trade",
            "calls_command_intent_repository", "uses_canonical_entry", "current_mode", "live_risk",
        }
        for item in records:
            self.assertEqual(set(item), required)
            source_path = ROOT / item["path"]
            self.assertTrue(source_path.is_file(), item)
            self.assertIn(item["symbol"], _declared_symbols(source_path), item)

    def test_legacy_bypasses_are_exact_baselined_and_new_calls_fail(self):
        current = scan_entrypoint_bypasses(ROOT, self.manifest["guarded_paths"])
        comparison = compare_with_baseline(current, load_baseline(self.manifest))
        self.assertTrue(comparison.passed, comparison.new_violations)
        self.assertEqual(len(current), len(self.manifest["legacy_bypass_baseline"]))
        # SC-15 Tier 1+2 retired quick_trade.py and quick_trade/orders.py bypass bodies.
        # Remaining 9 records are legacy worker internals; may only decrease further.
        self.assertEqual(len(current), 9)
        for item in self.manifest["legacy_bypass_baseline"]:
            self.assertNotIn("*", item["path"])
            self.assertNotIn("*", item["symbol"])
            self.assertTrue(item["legacy_reason"].strip())

    def test_new_bypass_in_a_baselined_file_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "surface.py"
            target.write_text("def legacy(client):\n    return client.place_order('x')\n", encoding="utf-8")
            baseline = scan_entrypoint_bypasses(root, ["surface.py"])
            target.write_text("def legacy(client):\n    return client.place_order('x')\n\ndef new(client):\n    return client.place_order('y')\n", encoding="utf-8")
            current = scan_entrypoint_bypasses(root, ["surface.py"])
            comparison = compare_with_baseline(current, baseline)
            self.assertFalse(comparison.passed)
            self.assertEqual(comparison.new_violations[0].symbol, "new")

    def test_ast_detects_executor_exchange_quick_trade_and_persistence_bypasses(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "surface.py"
            target.write_text(
                "from app.services.trading_executor import TradingExecutor\n"
                "from app.services.quick_trade.credentials import create_exchange_client\n"
                "def bypass(client, repository):\n"
                "    client.place_market_order('BTC')\n"
                "    repository.create_reservation()\n"
                "    return _post('/api/agent/v1/quick-trade/orders')\n",
                encoding="utf-8",
            )
            categories = {item.category for item in scan_entrypoint_bypasses(root, ["surface.py"])}
            self.assertTrue({
                "direct_import", "direct_order_or_exchange_call", "direct_persistence_bypass",
                "direct_quick_trade_http",
            }.issubset(categories))

    def test_retiring_a_legacy_call_is_allowed_but_renaming_requires_a_new_reasoned_record(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "surface.py"
            target.write_text("def legacy(client):\n    return client.place_order('x')\n", encoding="utf-8")
            original = scan_entrypoint_bypasses(root, ["surface.py"])
            target.write_text("def renamed(client):\n    return client.place_order('x')\n", encoding="utf-8")
            renamed = scan_entrypoint_bypasses(root, ["surface.py"])
            self.assertFalse(compare_with_baseline(renamed, original).passed)
            target.write_text("def retired():\n    return None\n", encoding="utf-8")
            self.assertTrue(compare_with_baseline(scan_entrypoint_bypasses(root, ["surface.py"]), original).passed)

    def test_terminal_retirement_excludes_unreachable_bypass_but_reactivation_is_detected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "surface.py"
            target.write_text(
                "def retired(client):\n    return None\n    client.place_order('x')\n",
                encoding="utf-8",
            )
            retired = scan_entrypoint_bypasses(root, ["surface.py"])
            self.assertEqual(retired, ())
            target.write_text("def reactivated(client):\n    return client.place_order('x')\n", encoding="utf-8")
            comparison = compare_with_baseline(scan_entrypoint_bypasses(root, ["surface.py"]), retired)
            self.assertFalse(comparison.passed)
            self.assertEqual(comparison.new_violations[0].symbol, "reactivated")

    def test_restricted_agent_and_mcp_quick_trade_entrypoints_are_terminally_disabled(self):
        surfaces = {
            ROOT / "backend_api_python/app/routes/agent_v1/quick_trade.py": {"place_order", "kill_switch"},
            ROOT / "mcp_server/src/quantdinger_mcp/server.py": {"place_quick_order", "cancel_open_paper_orders"},
        }
        for path, expected in surfaces.items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in expected}
            self.assertEqual(set(found), expected)
            for function in found.values():
                body = function.body
                if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                    body = body[1:]
                self.assertIsInstance(body[0], ast.Return)

    def test_restricted_sources_default_disabled_and_no_live_entry_mode_exists(self):
        contracts = load_pr11_contracts().entry
        for source in (contracts.EntrySource.AGENT, contracts.EntrySource.MCP, contracts.EntrySource.GRID):
            self.assertIs(contracts.default_entry_mode(source), contracts.EntryMode.DISABLED)
        self.assertNotIn("LIVE", {item.value for item in contracts.EntryMode})

    def test_protection_contract_is_reducing_only(self):
        source = (ROOT / "backend_api_python/app/domain/canonical_entry_contracts.py").read_text(encoding="utf-8")
        self.assertIn("EntrySource.PROTECTION", source)
        self.assertIn("RiskEffect.REDUCE_RISK", source)
        self.assertIn("OrderAction.PROTECTION", source)

    def test_manifest_is_json_and_versioned(self):
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["manifest_version"], "entrypoint-convergence-v1")


if __name__ == "__main__":
    unittest.main()


def _declared_symbols(path: Path) -> set[str]:
    symbols: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            symbols.add(".".join((*self.scope, node.name)))
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return symbols
