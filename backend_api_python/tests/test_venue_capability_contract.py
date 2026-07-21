from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


def _load_capabilities():
    path = Path(__file__).resolve().parents[1] / "app" / "services" / "live_trading" / "capabilities.py"
    spec = importlib.util.spec_from_file_location("pr00_venue_capabilities", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load venue capabilities")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


capabilities = _load_capabilities()
VenueCapability = capabilities.VenueCapability
VenueCapabilityValidationError = capabilities.VenueCapabilityValidationError


def _fully_capable(**overrides):
    values = {
        "supports_client_order_id": True,
        "supports_query_by_client_order_id": True,
        "supports_exchange_fill_id": True,
        "supports_order_history": True,
        "supports_cancel_status_query": True,
        "supports_reduce_only": True,
        "auto_live_eligible": True,
    }
    values.update(overrides)
    return VenueCapability("fixture", frozenset({"spot"}), **values)


class VenueCapabilityContractTests(unittest.TestCase):
    def test_defaults_fail_closed(self):
        capability = VenueCapability("unknown", frozenset())
        self.assertFalse(capability.auto_live_eligible)
        self.assertEqual(
            set(capability.missing_auto_live_requirements()),
            {
                "supports_client_order_id",
                "supports_query_by_client_order_id",
                "supports_exchange_fill_id",
                "supports_order_history",
                "supports_cancel_status_query",
                "supports_reduce_only",
            },
        )
        with self.assertRaises(VenueCapabilityValidationError):
            capability.validate_for_auto_live()

    def test_each_missing_recovery_or_fill_capability_rejects_auto_live(self):
        required = (
            "supports_client_order_id",
            "supports_query_by_client_order_id",
            "supports_exchange_fill_id",
            "supports_order_history",
            "supports_cancel_status_query",
            "supports_reduce_only",
        )
        for field in required:
            with self.subTest(field=field):
                capability = _fully_capable(**{field: False})
                with self.assertRaises(VenueCapabilityValidationError):
                    capability.validate_for_auto_live()

    def test_capabilities_alone_do_not_replace_explicit_approval(self):
        with self.assertRaises(VenueCapabilityValidationError):
            _fully_capable(auto_live_eligible=False).validate_for_auto_live()

    def test_complete_and_explicitly_approved_capability_passes(self):
        capability = _fully_capable()
        capability.validate_for_auto_live()
        capabilities.validate_for_auto_live(capability)

    def test_model_is_immutable(self):
        capability = _fully_capable()
        with self.assertRaises(FrozenInstanceError):
            capability.auto_live_eligible = False

    def test_unknown_model_and_existing_unverified_matrix_fail_closed(self):
        with self.assertRaises(VenueCapabilityValidationError):
            capabilities.validate_for_auto_live(object())
        for capability in capabilities.CRYPTO_VENUE_CAPABILITIES.values():
            self.assertFalse(capability.auto_live_eligible)
            with self.assertRaises(VenueCapabilityValidationError):
                capability.validate_for_auto_live()


if __name__ == "__main__":
    unittest.main()
