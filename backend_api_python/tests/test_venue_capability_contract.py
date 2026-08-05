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
VenueCapabilityProfile = capabilities.VenueCapabilityProfile
VenueCapabilityValidationError = capabilities.VenueCapabilityValidationError


def _fully_capable_profile(market_type="swap", **overrides):
    values = {
        "supports_client_order_id": True,
        "supports_query_by_client_order_id": True,
        "supports_exchange_fill_id": True,
        "supports_order_history": True,
        "supports_cancel_status_query": True,
        "supports_reduce_only": True,
        "contract_tested": True,
        "auto_live_eligible": True,
    }
    values.update(overrides)
    return VenueCapabilityProfile("fixture", market_type, **values)


class VenueCapabilityContractTests(unittest.TestCase):
    def test_defaults_fail_closed(self):
        profile = VenueCapabilityProfile("unknown", "spot")
        self.assertFalse(profile.auto_live_eligible)
        self.assertFalse(profile.contract_tested)
        self.assertEqual(
            set(profile.missing_auto_live_requirements()),
            {
                "supports_client_order_id",
                "supports_query_by_client_order_id",
                "supports_exchange_fill_id",
                "supports_order_history",
                "supports_cancel_status_query",
            },
        )
        with self.assertRaises(VenueCapabilityValidationError):
            profile.validate_for_auto_live()

    def test_each_missing_recovery_or_fill_capability_rejects_auto_live(self):
        common_required = (
            "supports_client_order_id",
            "supports_query_by_client_order_id",
            "supports_exchange_fill_id",
            "supports_order_history",
            "supports_cancel_status_query",
        )
        for market_type in ("spot", "swap"):
            for field in common_required:
                with self.subTest(market_type=market_type, field=field):
                    profile = _fully_capable_profile(
                        market_type, **{field: False}
                    )
                    with self.assertRaises(VenueCapabilityValidationError):
                        profile.validate_for_auto_live()

    def test_spot_does_not_require_reduce_only(self):
        profile = _fully_capable_profile("spot", supports_reduce_only=False)
        self.assertNotIn(
            "supports_reduce_only", profile.missing_auto_live_requirements()
        )
        profile.validate_for_auto_live()

    def test_swap_requires_reduce_only(self):
        profile = _fully_capable_profile("swap", supports_reduce_only=False)
        self.assertIn("supports_reduce_only", profile.missing_auto_live_requirements())
        with self.assertRaises(VenueCapabilityValidationError):
            profile.validate_for_auto_live()

    def test_capabilities_require_contract_test_and_explicit_approval(self):
        with self.assertRaises(VenueCapabilityValidationError):
            _fully_capable_profile(contract_tested=False).validate_for_auto_live()
        with self.assertRaises(VenueCapabilityValidationError):
            _fully_capable_profile(auto_live_eligible=False).validate_for_auto_live()

    def test_complete_and_explicitly_approved_capability_passes(self):
        for market_type in ("spot", "swap"):
            profile = _fully_capable_profile(market_type)
            profile.validate_for_auto_live()
            capabilities.validate_for_auto_live(profile)

    def test_model_is_immutable(self):
        profile = _fully_capable_profile()
        with self.assertRaises(FrozenInstanceError):
            profile.auto_live_eligible = False

    def test_spot_and_swap_profiles_are_independent_and_unverified(self):
        for exchange_id in capabilities.CRYPTO_VENUE_CAPABILITIES:
            spot = capabilities.get_venue_capability_profile(exchange_id, "spot")
            swap = capabilities.get_venue_capability_profile(exchange_id, "swap")
            self.assertIsNotNone(spot)
            self.assertIsNotNone(swap)
            self.assertIsNot(spot, swap)
            self.assertEqual(spot.market_type, "spot")
            self.assertEqual(swap.market_type, "swap")
            for profile in (spot, swap):
                self.assertFalse(profile.contract_tested)
                self.assertFalse(profile.auto_live_eligible)
                with self.assertRaises(VenueCapabilityValidationError):
                    profile.validate_for_auto_live()

    def test_binance_spot_does_not_inherit_futures_client_id_rule(self):
        futures = capabilities.get_venue_capability_profile("binance", "swap")
        spot = capabilities.get_venue_capability_profile("binance", "spot")
        self.assertTrue(futures.accepts_external_client_order_id)
        self.assertTrue(futures.can_generate_safe_client_order_id)
        self.assertEqual(futures.client_id_max_length, 36)
        self.assertIsNotNone(futures.client_id_pattern)
        self.assertTrue(spot.accepts_external_client_order_id)
        self.assertFalse(spot.can_generate_safe_client_order_id)
        self.assertIsNone(spot.client_id_max_length)
        self.assertIsNone(spot.client_id_pattern)
        # Independent read-only evidence does not enable automatic live trading.
        self.assertTrue(spot.query_by_exchange_order_id)
        self.assertTrue(spot.query_by_client_order_id)
        self.assertFalse(spot.auto_live_eligible)

    def test_legacy_catalog_requires_exact_unverified_profile(self):
        capability = VenueCapability("binance", frozenset({"spot", "swap"}))
        with self.assertRaises(VenueCapabilityValidationError):
            capability.validate_for_auto_live()
        with self.assertRaises(VenueCapabilityValidationError):
            capability.validate_for_auto_live("spot")

    def test_unknown_or_mismatched_profile_fails_closed(self):
        with self.assertRaises(VenueCapabilityValidationError):
            capabilities.validate_for_auto_live(object())
        with self.assertRaises(VenueCapabilityValidationError):
            VenueCapabilityProfile("fixture", "").validate_for_auto_live()
        with self.assertRaises(VenueCapabilityValidationError):
            capabilities.validate_for_auto_live(
                _fully_capable_profile("spot"), "swap"
            )
        self.assertIsNone(
            capabilities.get_venue_capability_profile("binance", "unknown")
        )


if __name__ == "__main__":
    unittest.main()
