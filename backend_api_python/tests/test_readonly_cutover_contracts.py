"""Pure tests for the fail-closed read-surface cutover policy."""

from __future__ import annotations

from enum import Enum
import importlib.util
from pathlib import Path
from types import ModuleType
import sys
import unittest


class _Status(str, Enum):
    READY = "READY"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"


class _View:
    def __init__(self, status: _Status, fingerprint: str = "a" * 64) -> None:
        self.status = status
        self.view_fingerprint = fingerprint


def _load() -> ModuleType:
    app = ModuleType("app")
    app.__path__ = []
    domain = ModuleType("app.domain")
    domain.__path__ = []
    state = ModuleType("app.domain.readonly_quant_state_contracts")
    state.ReadonlyQuantStateView = _View
    state.ReadonlyViewStatus = _Status
    previous = {name: sys.modules.get(name) for name in (
        "app", "app.domain", "app.domain.readonly_quant_state_contracts",
    )}
    sys.modules.update({"app": app, "app.domain": domain, "app.domain.readonly_quant_state_contracts": state})
    try:
        path = Path(__file__).resolve().parents[1] / "app" / "domain" / "readonly_cutover_contracts.py"
        spec = importlib.util.spec_from_file_location("test_readonly_cutover_contracts_subject", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load cutover subject")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("test_readonly_cutover_contracts_subject", None)
        for name, original in previous.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


m = _load()


class ReadonlyCutoverContractTests(unittest.TestCase):
    def test_ready_candidate_is_selected_only_when_enabled(self):
        ready = _View(_Status.READY)
        self.assertEqual(
            m.select_read_surface(ready, m.ReadonlyCutoverPolicy(candidate_enabled=True)).surface,
            m.ReadSurface.CANDIDATE,
        )
        disabled = m.select_read_surface(ready, m.ReadonlyCutoverPolicy())
        self.assertEqual(disabled.surface, m.ReadSurface.LEGACY)

    def test_stale_view_requires_explicit_legacy_fallback(self):
        stale = _View(_Status.STALE, "b" * 64)
        unavailable = m.select_read_surface(stale, m.ReadonlyCutoverPolicy())
        self.assertEqual(unavailable.surface, m.ReadSurface.UNAVAILABLE)
        fallback = m.select_read_surface(stale, m.ReadonlyCutoverPolicy(allow_legacy_fallback=True))
        self.assertEqual(fallback.surface, m.ReadSurface.LEGACY)

    def test_unauthorized_and_unavailable_never_fallback(self):
        for status, surface in ((_Status.UNAUTHORIZED, m.ReadSurface.UNAUTHORIZED), (_Status.UNAVAILABLE, m.ReadSurface.UNAVAILABLE)):
            selected = m.select_read_surface(_View(status), m.ReadonlyCutoverPolicy(candidate_enabled=True, allow_legacy_fallback=True))
            self.assertEqual(selected.surface, surface)

    def test_policy_and_inputs_are_typed(self):
        with self.assertRaises(m.ReadonlyCutoverError):
            m.ReadonlyCutoverPolicy(candidate_enabled=1)
        with self.assertRaises(m.ReadonlyCutoverError):
            m.select_read_surface(object(), m.ReadonlyCutoverPolicy())
        with self.assertRaises(m.ReadonlyCutoverError):
            m.select_read_surface(_View(object()), m.ReadonlyCutoverPolicy())

    def test_policy_is_immutable_and_selection_preserves_fingerprint(self):
        policy = m.ReadonlyCutoverPolicy(candidate_enabled=True)
        with self.assertRaises((AttributeError, TypeError)):
            policy.candidate_enabled = False
        selected = m.select_read_surface(_View(_Status.READY, "c" * 64), policy)
        self.assertEqual(selected.view_fingerprint, "c" * 64)


if __name__ == "__main__":
    unittest.main()
