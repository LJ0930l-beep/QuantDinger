"""Unit coverage for the runtime-safe read-only quant state seam."""

from __future__ import annotations

import unittest
import importlib.util
import sys
import types
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
if "app" not in sys.modules:
    package = types.ModuleType("app")
    package.__path__ = [str(_ROOT / "app")]
    sys.modules["app"] = package
if "app.domain" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "app.domain", _ROOT / "app" / "domain" / "__init__.py",
        submodule_search_locations=[str(_ROOT / "app" / "domain")],
    )
    domain = importlib.util.module_from_spec(spec)
    sys.modules["app.domain"] = domain
    spec.loader.exec_module(domain)

from app.domain.readonly_quant_state_contracts import ReadonlyViewStatus
from app.services.readonly_quant_state_service import (
    ReadonlyQuantStateService,
    ReadonlyQuantStateServiceError,
)


class ReadonlyQuantStateServiceTests(unittest.TestCase):
    def test_missing_provider_is_explicitly_unavailable(self):
        service = ReadonlyQuantStateService()
        view = service.read_view()
        self.assertIs(view.status, ReadonlyViewStatus.UNAVAILABLE)
        response = service.read_response()
        self.assertEqual(response.http_status, 503)
        self.assertEqual(response.body["status"], "UNAVAILABLE")

    def test_unauthorized_view_contains_no_facts(self):
        service = ReadonlyQuantStateService(lambda: None)
        view = service.read_view(authorized=False)
        self.assertIs(view.status, ReadonlyViewStatus.UNAUTHORIZED)
        response = service.read_response(authorized=False)
        self.assertEqual(response.http_status, 401)
        self.assertEqual(response.body, {
            "contract_version": "readonly-quant-state-v1",
            "status": "UNAUTHORIZED",
            "api_contract_version": "readonly-quant-api-v1",
        })

    def test_provider_failure_is_typed_and_does_not_leak_error(self):
        def fail():
            raise ValueError("credential payload must never cross boundary")

        with self.assertRaises(ReadonlyQuantStateServiceError) as ctx:
            ReadonlyQuantStateService(fail).read_view()
        self.assertNotIn("credential", str(ctx.exception).lower())

    def test_non_callable_provider_is_rejected_by_constructor_boundary(self):
        with self.assertRaises(ReadonlyQuantStateServiceError):
            ReadonlyQuantStateService(123).read_view()


if __name__ == "__main__":
    unittest.main()
