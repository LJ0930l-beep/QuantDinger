import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = ("app", "app.domain", "app.services")
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        return importlib.import_module("app.services.non_live_product_rehearsal_service")
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


S = _load()


class NonLiveProductRehearsalTests(unittest.TestCase):
    def test_complete_fixture_chain_is_deterministic_and_non_live(self):
        first = S.build_offline_product_rehearsal()
        second = S.build_offline_product_rehearsal()
        self.assertEqual(first, second)
        self.assertFalse(first["live_enabled"])
        self.assertFalse(first["network_access"])
        self.assertEqual(first["execution_boundary"], "READ_ONLY_FIXTURE")
        self.assertEqual(first["environment"]["LIVE"], False)
        self.assertEqual(first["deterministic_backtest"]["decisions"][0]["decision"], "executed")
        self.assertEqual(first["paper_account"]["filled_count"], 1)
        self.assertEqual(len(first["strategy_catalog"]), 6)

    def test_rehearsal_does_not_require_credentials_or_database(self):
        result = S.build_offline_product_rehearsal()
        serialized = repr(result)
        self.assertNotIn("secret", serialized.lower())
        self.assertNotIn("api_key", serialized.lower())


if __name__ == "__main__":
    unittest.main()
