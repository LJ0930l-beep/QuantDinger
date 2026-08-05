from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal

from tests.pr01_domain_loader import load_pr01_domain


domain = load_pr01_domain()


class DecimalValueTests(unittest.TestCase):
    def test_allowed_input_types_are_decimal_string_and_int(self):
        for value in (Decimal("1.25"), "1.25", 1):
            with self.subTest(value=value):
                self.assertIsInstance(domain.Quantity(value).value, Decimal)

    def test_binary_float_and_bool_are_rejected(self):
        for value_type in (
            domain.Quantity,
            domain.Price,
            domain.QuoteAmount,
            domain.FeeAmount,
            domain.PnLAmount,
        ):
            for value in (0.1, True):
                with self.subTest(value_type=value_type, value=value):
                    with self.assertRaises(domain.DecimalInputTypeError):
                        value_type(value)

    def test_nan_and_infinity_are_rejected(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(domain.DecimalValueError):
                    domain.PnLAmount(value)

    def test_numeric_38_18_boundaries_and_extremely_small_values(self):
        maximum = "99999999999999999999.999999999999999999"
        self.assertEqual(domain.QuoteAmount(maximum).to_string(), maximum)
        self.assertEqual(domain.Quantity("0.000000000000000001").to_string(), "0.000000000000000001")
        with self.assertRaises(domain.DecimalValueError):
            domain.Quantity("0.0000000000000000001")
        with self.assertRaises(domain.DecimalValueError):
            domain.QuoteAmount("100000000000000000000")

    def test_sign_and_zero_policies_are_explicit(self):
        self.assertEqual(domain.Quantity("0").to_string(), "0")
        self.assertEqual(domain.FeeAmount("0").to_string(), "0")
        self.assertEqual(domain.PnLAmount("-12.5").to_string(), "-12.5")
        self.assertEqual(domain.SignedQuantity("-2").to_string(), "-2")
        for value_type in (domain.Quantity, domain.QuoteAmount, domain.FeeAmount):
            with self.subTest(value_type=value_type):
                with self.assertRaises(domain.DecimalValueError):
                    value_type("-0.1")
        with self.assertRaises(domain.DecimalValueError):
            domain.Price("0")
        with self.assertRaises(domain.DecimalValueError):
            domain.Price("-1")

    def test_serialization_is_canonical_and_never_scientific(self):
        cases = {
            "1.230000": "1.23",
            "1E-18": "0.000000000000000001",
            "1E+3": "1000",
            "-0.000": "0",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(domain.PnLAmount(raw).to_string(), expected)
                self.assertNotIn("E", domain.PnLAmount(raw).to_string().upper())

    def test_values_are_immutable(self):
        value = domain.Quantity("1")
        with self.assertRaises(FrozenInstanceError):
            value.value = Decimal("2")

    def test_calculation_fit_uses_versioned_half_even_policy(self):
        fitted = domain.decimal_values.fit_calculated_decimal(
            Decimal("1.2345678901234567895")
        )
        self.assertEqual(domain.PnLAmount(fitted).to_string(), "1.23456789012345679")
        self.assertEqual(
            domain.CALCULATION_POLICY_VERSION,
            "numeric-38-18-half-even-v1",
        )


if __name__ == "__main__":
    unittest.main()
