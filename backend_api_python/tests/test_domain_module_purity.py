from __future__ import annotations

import ast
import unittest
from pathlib import Path


DOMAIN_DIR = Path(__file__).resolve().parents[1] / "app" / "domain"
PR01_MODULES = (
    DOMAIN_DIR / "decimal_values.py",
    DOMAIN_DIR / "precision.py",
    DOMAIN_DIR / "reducers.py",
)
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "dataclasses",
    "decimal",
    "enum",
    "hashlib",
    "json",
    "typing",
}


class DomainModulePurityTests(unittest.TestCase):
    def test_new_modules_only_import_standard_library_or_sibling_domain_modules(self):
        for path in PR01_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                    self.assertTrue(
                        roots <= ALLOWED_IMPORT_ROOTS,
                        f"{path.name} imports forbidden modules: {sorted(roots - ALLOWED_IMPORT_ROOTS)}",
                    )
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    root = (node.module or "").split(".", 1)[0]
                    self.assertIn(root, ALLOWED_IMPORT_ROOTS, f"{path.name} imports {root}")

    def test_new_modules_contain_no_binary_float_literals(self):
        for path in PR01_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            float_literals = [
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, float)
            ]
            self.assertEqual(float_literals, [], f"{path.name} contains float literals")


if __name__ == "__main__":
    unittest.main()
