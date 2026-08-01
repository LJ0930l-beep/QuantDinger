import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load():
    names = [
        "app",
        "app.domain",
        "app.domain.order_contracts",
        "app.domain.g4b_readonly_contracts",
        "app.domain.readonly_quant_state_contracts",
        "app.domain.readonly_quant_api_contracts",
    ]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        paths = {
            names[2]: ROOT / "app/domain/order_contracts.py",
            names[3]: ROOT / "app/domain/g4b_readonly_contracts.py",
            names[4]: ROOT / "app/domain/readonly_quant_state_contracts.py",
            names[5]: ROOT / "app/domain/readonly_quant_api_contracts.py",
        }
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[5]], sys.modules[names[4]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, STATE = load()


class ReadonlyQuantApiContractTests(unittest.TestCase):
    def test_rejects_untyped_view_and_preserves_no_secret_surface(self):
        with self.assertRaises(M.ReadonlyQuantApiContractError):
            M.serialize_readonly_quant_state({"status": "READY"})
        response = M.serialize_readonly_quant_state(STATE.ReadonlyQuantStateView(STATE.ReadonlyViewStatus.UNAUTHORIZED))
        self.assertEqual(response.http_status, 401)
        self.assertEqual(response.body["status"], "UNAUTHORIZED")
        self.assertNotIn("credential", str(response.body).lower())

    def test_unavailable_is_503_and_has_no_facts(self):
        response = M.serialize_readonly_quant_state(STATE.ReadonlyQuantStateView(STATE.ReadonlyViewStatus.UNAVAILABLE))
        self.assertEqual(response.http_status, 503)
        self.assertEqual(set(response.body), {"contract_version", "status", "api_contract_version"})

    def test_ready_and_stale_statuses_are_successful_read_responses(self):
        self.assertEqual({"READY", "STALE"}, {item.value for item in (STATE.ReadonlyViewStatus.READY, STATE.ReadonlyViewStatus.STALE)})

    def test_response_is_immutable_and_contract_versioned(self):
        response = M.serialize_readonly_quant_state(STATE.ReadonlyQuantStateView(STATE.ReadonlyViewStatus.UNAUTHORIZED))
        with self.assertRaises((AttributeError, TypeError)):
            response.http_status = 200
        self.assertEqual(response.contract_version, "readonly-quant-api-v1")


if __name__ == "__main__":
    unittest.main()
