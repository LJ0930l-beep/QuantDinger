"""Static boundary checks for the pure PR-11 entry-contract layer."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


DOMAIN = Path(__file__).resolve().parents[1] / "app" / "domain"
MODULES = (
    DOMAIN / "canonical_entry_contracts.py",
    DOMAIN / "canonical_entry_adapters.py",
    DOMAIN / "protection_entry_contracts.py",
)
FORBIDDEN_IMPORT_TERMS = (
    "service",
    "repository",
    "route",
    "flask",
    "executor",
    "worker",
    "exchange",
    "gateway",
)
FORBIDDEN_CALL_TERMS = (
    "submit_order",
    "place_order",
    "cancel_order",
    "create_order",
)


class CanonicalEntryArchitectureTests(unittest.TestCase):
    def test_entry_contract_modules_are_pure_and_have_no_live_mode(self):
        for path in MODULES:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            imported = []
            calls = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        calls.append(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        calls.append(node.func.attr)
            haystack = " ".join(imported).lower()
            self.assertFalse(any(term in haystack for term in FORBIDDEN_IMPORT_TERMS), path)
            self.assertFalse(any(name in FORBIDDEN_CALL_TERMS for name in calls), path)
            self.assertNotIn("LIVE", source, path)

    def test_every_declared_source_has_only_a_pure_adapter_boundary(self):
        source = (DOMAIN / "canonical_entry_adapters.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        public = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("adapt_")
        }
        self.assertEqual(
            set(public),
            {
                "adapt_rest",
                "adapt_manual",
                "adapt_strategy",
                "adapt_agent",
                "adapt_mcp",
                "adapt_grid",
                "adapt_protection",
            },
        )
        for name, function in public.items():
            calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
            self.assertEqual(len(calls), 1, name)
            self.assertIsInstance(calls[0].func, ast.Name, name)
            self.assertEqual(calls[0].func.id, "_adapt", name)


if __name__ == "__main__":
    unittest.main()
