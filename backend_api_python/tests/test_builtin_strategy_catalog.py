"""Pure tests for the deterministic read-only Strategy Factory catalog."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    app = types.ModuleType("app")
    app.__path__ = [str(ROOT / "app")]
    domain = types.ModuleType("app.domain")
    domain.__path__ = [str(ROOT / "app" / "domain")]
    services = types.ModuleType("app.services")
    services.__path__ = [str(ROOT / "app" / "services")]
    sys.modules.setdefault("app", app)
    sys.modules.setdefault("app.domain", domain)
    sys.modules.setdefault("app.services", services)


_bootstrap()
module_path = ROOT / "app" / "services" / "builtin_strategy_catalog.py"
spec = importlib.util.spec_from_file_location("app.services.builtin_strategy_catalog", module_path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class BuiltinStrategyCatalogTests(unittest.TestCase):
    def test_catalog_is_deterministic_and_factory_supported(self):
        first = module.builtin_strategy_catalog()
        second = module.builtin_strategy_catalog()
        self.assertEqual(first, second)
        self.assertEqual([item.family.value for item in first], ["smc", "ict"])
        self.assertEqual(len({item.strategy_id for item in first}), 2)

    def test_catalog_contains_no_execution_or_credential_authority(self):
        source = module_path.read_text(encoding="utf-8")
        for forbidden in ("exchange", "executor", "submit_order", "api_key", "secret", "commit(", "rollback("):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
