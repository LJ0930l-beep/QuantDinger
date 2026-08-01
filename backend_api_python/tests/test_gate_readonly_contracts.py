"""Offline contract tests for Gate TestNet/read-only integration."""

from __future__ import annotations

import dataclasses
import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path


def _load_contract_module():
    """Load pure domain source without importing Flask-backed ``app`` package."""

    root = Path(__file__).resolve().parents[1] / "app" / "domain"
    package_name = "_gate_readonly_contract_test_domain"
    package_spec = importlib.util.spec_from_loader(package_name, loader=None, is_package=True)
    package = importlib.util.module_from_spec(package_spec)
    package.__path__ = [str(root)]
    import sys

    sys.modules[package_name] = package
    decimal_spec = importlib.util.spec_from_file_location(f"{package_name}.decimal_values", root / "decimal_values.py")
    decimal_module = importlib.util.module_from_spec(decimal_spec)
    sys.modules[decimal_spec.name] = decimal_module
    decimal_spec.loader.exec_module(decimal_module)
    contract_spec = importlib.util.spec_from_file_location(f"{package_name}.gate_readonly_contracts", root / "gate_readonly_contracts.py")
    contract_module = importlib.util.module_from_spec(contract_spec)
    sys.modules[contract_spec.name] = contract_module
    contract_spec.loader.exec_module(contract_module)
    return contract_module


_contracts = _load_contract_module()
GATE_TESTNET_REST_BASE_URL = _contracts.GATE_TESTNET_REST_BASE_URL
GateEnvironment = _contracts.GateEnvironment
GateMarketType = _contracts.GateMarketType
GateReadCapabilityProfile = _contracts.GateReadCapabilityProfile
GateReadonlyContractError = _contracts.GateReadonlyContractError
GateUnsupportedEnvironment = _contracts.GateUnsupportedEnvironment
canonical_gate_testnet_base_url = _contracts.canonical_gate_testnet_base_url
gate_testnet_api_url = _contracts.gate_testnet_api_url
normalize_gate_ohlcv = _contracts.normalize_gate_ohlcv


class GateReadonlyContractTests(unittest.TestCase):
    def test_only_official_testnet_endpoint_is_accepted(self) -> None:
        self.assertEqual(GATE_TESTNET_REST_BASE_URL, canonical_gate_testnet_base_url(GATE_TESTNET_REST_BASE_URL + "/"))
        with self.assertRaises(GateUnsupportedEnvironment):
            canonical_gate_testnet_base_url("https://api.gateio.ws")
        with self.assertRaises(GateUnsupportedEnvironment):
            canonical_gate_testnet_base_url("https://fx-api.gateio.ws")

    def test_profile_is_testnet_scoped_and_write_disabled(self) -> None:
        profile = GateReadCapabilityProfile(
            environment=GateEnvironment.TESTNET,
            market_type=GateMarketType.PERPETUAL,
            credential_ref="testnet-ref",
            supports_account_reads=True,
            supports_order_reads=True,
            supports_fill_reads=True,
        )
        self.assertTrue(dataclasses.is_dataclass(profile))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            profile.base_url = "https://api.gateio.ws"  # type: ignore[misc]
        with self.assertRaises(GateReadonlyContractError):
            GateReadCapabilityProfile(
                environment=GateEnvironment.TESTNET,
                market_type=GateMarketType.SPOT,
                credential_ref="testnet-ref",
                writes_enabled=True,
            )

    def test_credential_reference_is_not_a_secret_value(self) -> None:
        with self.assertRaises(GateReadonlyContractError):
            GateReadCapabilityProfile(
                environment=GateEnvironment.TESTNET,
                market_type=GateMarketType.SPOT,
                credential_ref="test ref",
            )

    def test_api_paths_are_offline_and_api_v4_scoped(self) -> None:
        self.assertEqual(
            "https://api-testnet.gateapi.io/api/v4/spot/currency_pairs",
            gate_testnet_api_url("/spot/currency_pairs"),
        )
        self.assertEqual(
            "https://api-testnet.gateapi.io/api/v4/futures/usdt/contracts",
            gate_testnet_api_url("/api/v4/futures/usdt/contracts"),
        )
        with self.assertRaises(GateReadonlyContractError):
            gate_testnet_api_url("spot/currency_pairs")

    def test_offline_ohlcv_normalization_is_decimal_and_ordered(self) -> None:
        bars = normalize_gate_ohlcv(
            [
                [1000, Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10.5"), Decimal("2")],
                [2000, Decimal("10.5"), Decimal("12"), Decimal("10"), Decimal("11"), Decimal("3")],
            ]
        )
        self.assertEqual(2, len(bars))
        self.assertEqual(Decimal("10.5"), bars[0].close)
        with self.assertRaises(GateReadonlyContractError):
            normalize_gate_ohlcv([[2000, Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), Decimal("1")], [1000, Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), Decimal("1")]])

    def test_offline_ohlcv_rejects_float_and_invalid_bounds(self) -> None:
        with self.assertRaises(TypeError):
            normalize_gate_ohlcv([[1000, 10.0, Decimal("11"), Decimal("9"), Decimal("10"), Decimal("1")]])
        with self.assertRaises(GateReadonlyContractError):
            normalize_gate_ohlcv([[1000, Decimal("8"), Decimal("11"), Decimal("9"), Decimal("10"), Decimal("1")]])


if __name__ == "__main__":
    unittest.main()
