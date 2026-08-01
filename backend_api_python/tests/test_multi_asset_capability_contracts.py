import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "app" / "domain" / "multi_asset_capability_contracts.py"
SPEC = importlib.util.spec_from_file_location("multi_asset_capability_contracts", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MultiAssetCapabilityContractTests(unittest.TestCase):
    def test_gate_testnet_profiles_are_exact_and_read_only(self):
        matrix = MODULE.gate_testnet_capability_matrix()
        spot = matrix.resolve(
            "gate", MODULE.AssetProduct.SPOT, MODULE.AssetMarketType.SPOT, MODULE.CapabilityEnvironment.TESTNET
        )
        perpetual = matrix.resolve(
            "gate", MODULE.AssetProduct.PERPETUAL, MODULE.AssetMarketType.PERPETUAL, MODULE.CapabilityEnvironment.TESTNET
        )
        self.assertTrue(spot.supports_public_market_data)
        self.assertTrue(perpetual.supports_public_market_data)
        for profile in (spot, perpetual):
            self.assertFalse(profile.supports_write)
            self.assertFalse(profile.auto_live_eligible)
            self.assertFalse(profile.supports_account_reads)
            self.assertFalse(profile.supports_order_reads)
            self.assertFalse(profile.supports_fill_reads)
            self.assertEqual(profile.supported_order_kinds, ())

    def test_spot_and_perpetual_do_not_inherit_capabilities(self):
        matrix = MODULE.gate_testnet_capability_matrix()
        with self.assertRaises(MODULE.UnsupportedCapability):
            matrix.resolve("gate", MODULE.AssetProduct.SPOT, MODULE.AssetMarketType.PERPETUAL, MODULE.CapabilityEnvironment.TESTNET)
        with self.assertRaises(MODULE.UnsupportedCapability):
            matrix.resolve("gate", MODULE.AssetProduct.PERPETUAL, MODULE.AssetMarketType.SPOT, MODULE.CapabilityEnvironment.TESTNET)

    def test_unknown_profile_fails_closed(self):
        matrix = MODULE.gate_testnet_capability_matrix()
        with self.assertRaises(MODULE.UnsupportedCapability):
            matrix.resolve("unknown", MODULE.AssetProduct.SPOT, MODULE.AssetMarketType.SPOT, MODULE.CapabilityEnvironment.TESTNET)
        with self.assertRaises(MODULE.UnsupportedCapability):
            matrix.resolve("gate", MODULE.AssetProduct.OPTIONS, MODULE.AssetMarketType.OPTIONS, MODULE.CapabilityEnvironment.TESTNET)

    def test_no_live_environment_and_write_profiles_are_rejected(self):
        self.assertFalse(hasattr(MODULE.CapabilityEnvironment, "LIVE"))
        with self.assertRaises(MODULE.MultiAssetCapabilityError):
            MODULE.MultiAssetVenueCapability(
                venue_id="gate",
                product=MODULE.AssetProduct.SPOT,
                market_type=MODULE.AssetMarketType.SPOT,
                environment=MODULE.CapabilityEnvironment.TESTNET,
                evidence_version="test-v1",
                evidence_reference="test",
                supports_write=True,
            )
        with self.assertRaises(MODULE.MultiAssetCapabilityError):
            MODULE.MultiAssetVenueCapability(
                venue_id="gate",
                product=MODULE.AssetProduct.SPOT,
                market_type=MODULE.AssetMarketType.SPOT,
                environment=MODULE.CapabilityEnvironment.TESTNET,
                evidence_version="test-v1",
                evidence_reference="test",
                auto_live_eligible=True,
            )

    def test_disabled_profile_cannot_claim_reads(self):
        with self.assertRaises(MODULE.MultiAssetCapabilityError):
            MODULE.MultiAssetVenueCapability(
                venue_id="gate",
                product=MODULE.AssetProduct.SPOT,
                market_type=MODULE.AssetMarketType.SPOT,
                environment=MODULE.CapabilityEnvironment.DISABLED,
                evidence_version="test-v1",
                evidence_reference="test",
                supports_public_market_data=True,
            )

    def test_profiles_and_matrix_are_immutable(self):
        profile = MODULE.MultiAssetVenueCapability(
            venue_id="gate",
            product=MODULE.AssetProduct.SPOT,
            market_type=MODULE.AssetMarketType.SPOT,
            environment=MODULE.CapabilityEnvironment.PAPER,
            evidence_version="test-v1",
            evidence_reference="test",
        )
        with self.assertRaises((AttributeError, TypeError)):
            profile.venue_id = "other"
        matrix = MODULE.MultiAssetCapabilityMatrix((profile,))
        with self.assertRaises((AttributeError, TypeError)):
            matrix.profiles = ()


if __name__ == "__main__":
    unittest.main()
