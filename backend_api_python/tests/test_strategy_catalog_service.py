"""Read-only strategy catalog contract tests."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load():
    names = ["app", "app.domain", "app.domain.strategy_library_contracts", "app.domain.strategy_catalog_contracts", "app.services", "app.services.strategy_catalog_service"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        paths = {
            names[2]: ROOT / "app/domain/strategy_library_contracts.py",
            names[3]: ROOT / "app/domain/strategy_catalog_contracts.py",
            names[5]: ROOT / "app/services/strategy_catalog_service.py",
        }
        for name in (names[2], names[3], names[5]):
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[5]], sys.modules[names[2]], sys.modules[names[3]]
    finally:
        for name, original in old.items():
            if original is None: sys.modules.pop(name, None)
            else: sys.modules[name] = original


M, LIB, CONTRACT = load()


def strategy(strategy_id="smc-1"):
    return LIB.StrategyDefinition(
        strategy_id, "v1", LIB.StrategyFamily.SMC, "schema-1", "snapshot-1",
        (LIB.StrategyParameterFact("lookback", "3"),),
    )


class StrategyCatalogServiceTests(unittest.TestCase):
    def test_typed_catalog_is_deterministic_and_public(self):
        first = M.StrategyCatalogService(lambda: (strategy(),)).read_view()
        second = M.StrategyCatalogService(lambda: (strategy(),)).read_view()
        self.assertEqual(first.catalog_fingerprint, second.catalog_fingerprint)
        self.assertEqual(first.to_public_dict()["strategies"][0]["family"], "smc")

    def test_missing_provider_is_unavailable(self):
        status, body = M.StrategyCatalogService().read_response()
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "UNAVAILABLE")

    def test_provider_cannot_return_untyped_or_duplicate_facts(self):
        with self.assertRaises(M.StrategyCatalogServiceError):
            M.StrategyCatalogService(lambda: ("smc",)).read_view()
        with self.assertRaises(CONTRACT.StrategyCatalogError):
            CONTRACT.StrategyCatalogView(CONTRACT.StrategyCatalogStatus.READY, (strategy(), strategy()))


if __name__ == "__main__":
    unittest.main()
