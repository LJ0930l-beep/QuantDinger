"""Static safety boundary checks for PR-08 Shadow Diff code."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "app"
MODULES = (ROOT / "domain" / "shadow_diff_contracts.py", ROOT / "services" / "shadow_diff_repository.py")
FORBIDDEN_IMPORT_TERMS = ("exchange", "executor", "worker", "route", "strategy", "trading", "outbox", "projection")
FORBIDDEN_CALLS = ("commit", "rollback", "submit_order", "place_order", "cancel_order")


class ShadowDiffArchitectureTests(unittest.TestCase):
    def test_shadow_diff_modules_have_no_runtime_or_venue_boundary(self):
        for path in MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = []
            calls = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
                elif isinstance(node, ast.Call):
                    calls.append(node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else "")
            joined = " ".join(imported).lower()
            self.assertFalse(any(term in joined for term in FORBIDDEN_IMPORT_TERMS), path)
            self.assertFalse(any(call in FORBIDDEN_CALLS for call in calls), path)


if __name__ == "__main__":
    unittest.main()
