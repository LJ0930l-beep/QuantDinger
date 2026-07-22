from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from tests.pr01_domain_loader import load_pr01_domain


domain = load_pr01_domain()


def snapshot(**overrides):
    values = {
        "instrument_id": "BTC-USDT",
        "exchange_id": "fixture",
        "market_type": "spot",
        "tick_size": domain.Price("0.05"),
        "quantity_step": domain.Quantity("0.001"),
        "minimum_quantity": domain.Quantity("0.01"),
        "minimum_notional": domain.QuoteAmount("5"),
        "price_scale": 2,
        "quantity_scale": 3,
        "rounding_policy_version": "v1",
        "instrument_rule_version": "fixture-rules-2026-01",
    }
    values.update(overrides)
    return domain.InstrumentPrecisionSnapshot(**values)


class InstrumentPrecisionTests(unittest.TestCase):
    def test_price_rounding_requires_explicit_supported_policy(self):
        rules = snapshot()
        self.assertEqual(
            domain.quantize_price(
                rules, domain.Price("100.03"), policy=domain.RoundingPolicy.ROUND_DOWN
            ).to_string(),
            "100",
        )
        self.assertEqual(
            domain.quantize_price(
                rules, domain.Price("100.03"), policy=domain.RoundingPolicy.ROUND_UP
            ).to_string(),
            "100.05",
        )
        self.assertEqual(
            domain.quantize_price(
                rules,
                domain.Price("100.025"),
                policy=domain.RoundingPolicy.ROUND_HALF_EVEN,
            ).to_string(),
            "100",
        )
        with self.assertRaises(domain.PrecisionContractError):
            domain.quantize_price(rules, domain.Price("100.03"))

    def test_quantity_rounding_supports_all_three_policies(self):
        rules = snapshot()
        self.assertEqual(
            domain.quantize_quantity(
                rules, domain.Quantity("1.2345"), policy="ROUND_DOWN"
            ).to_string(),
            "1.234",
        )
        self.assertEqual(
            domain.quantize_quantity(
                rules, domain.Quantity("1.2345"), policy="ROUND_UP"
            ).to_string(),
            "1.235",
        )
        self.assertEqual(
            domain.quantize_quantity(
                rules, domain.Quantity("1.2345"), policy="ROUND_HALF_EVEN"
            ).to_string(),
            "1.234",
        )

    def test_minimum_quantity_and_notional_are_pure_checks(self):
        rules = snapshot()
        self.assertFalse(domain.validate_minimum_quantity(rules, domain.Quantity("0.009")))
        self.assertTrue(domain.validate_minimum_quantity(rules, domain.Quantity("0.01")))
        self.assertFalse(domain.validate_minimum_notional(rules, domain.QuoteAmount("4.99")))
        self.assertTrue(domain.validate_minimum_notional(rules, domain.QuoteAmount("5")))
        self.assertEqual(
            domain.calculate_notional(domain.Quantity("0.05"), domain.Price("100"))
            .to_string(),
            "5",
        )

    def test_unknown_rounding_policy_version_fails_closed(self):
        rules = snapshot(rounding_policy_version="future-v99")
        with self.assertRaises(domain.PrecisionContractError):
            domain.quantize_quantity(
                rules, domain.Quantity("1"), policy=domain.RoundingPolicy.ROUND_DOWN
            )
        with self.assertRaises(domain.PrecisionContractError):
            domain.validate_minimum_quantity(rules, domain.Quantity("1"))

    def test_snapshot_requires_valid_scales_versions_and_market_type(self):
        invalid_overrides = (
            {"price_scale": 1},
            {"quantity_scale": 2},
            {"rounding_policy_version": ""},
            {"instrument_rule_version": ""},
            {"market_type": "future"},
            {"quantity_step": domain.Quantity("0")},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(domain.PrecisionContractError):
                    snapshot(**overrides)

    def test_snapshot_is_immutable(self):
        rules = snapshot()
        with self.assertRaises(FrozenInstanceError):
            rules.tick_size = domain.Price("1")


if __name__ == "__main__":
    unittest.main()
