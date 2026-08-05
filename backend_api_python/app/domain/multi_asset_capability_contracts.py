"""Immutable multi-asset venue capability contracts.

This module is deliberately a pure boundary.  A profile is evidence for one
exact venue/product/market/environment tuple; capabilities are never inherited
from another tuple and no profile can enable writes or live trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Tuple


MULTI_ASSET_CAPABILITY_CONTRACT_VERSION = "multi-asset-capability-v1"


class MultiAssetCapabilityError(ValueError):
    """Base error for invalid or insufficient capability evidence."""


class UnsupportedCapability(MultiAssetCapabilityError):
    """Raised when a capability is not explicitly evidenced."""


class AssetProduct(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    DELIVERY = "delivery"
    OPTIONS = "options"
    STOCKS = "stocks"
    ETF = "etf"


class AssetMarketType(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    DELIVERY = "delivery"
    OPTIONS = "options"
    STOCKS = "stocks"
    ETF = "etf"


class CapabilityEnvironment(str, Enum):
    DISABLED = "disabled"
    PAPER = "paper"
    SHADOW = "shadow"
    TESTNET = "testnet"


class CapabilityOrderKind(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


def _canonical_text(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise MultiAssetCapabilityError(f"{field} must be text")
    normalized = value.strip().lower()
    if not normalized or normalized != value or any(ord(ch) > 127 for ch in normalized):
        raise MultiAssetCapabilityError(f"{field} must be canonical ASCII text")
    if any(ch.isspace() for ch in normalized):
        raise MultiAssetCapabilityError(f"{field} must not contain whitespace")
    return normalized


@dataclass(frozen=True)
class MultiAssetVenueCapability:
    """Evidence-backed capability for one exact product scope."""

    venue_id: str
    product: AssetProduct
    market_type: AssetMarketType
    environment: CapabilityEnvironment
    evidence_version: str
    evidence_reference: str
    supports_public_market_data: bool = False
    supports_account_reads: bool = False
    supports_order_reads: bool = False
    supports_fill_reads: bool = False
    supported_order_kinds: Tuple[CapabilityOrderKind, ...] = ()
    supports_short: bool = False
    supports_leverage: bool = False
    supports_margin: bool = False
    supports_funding: bool = False
    supports_write: bool = False
    auto_live_eligible: bool = False

    def __post_init__(self) -> None:
        venue = _canonical_text(self.venue_id, "venue_id")
        object.__setattr__(self, "venue_id", venue)
        for field, expected in (
            ("product", AssetProduct),
            ("market_type", AssetMarketType),
            ("environment", CapabilityEnvironment),
        ):
            if not isinstance(getattr(self, field), expected):
                raise MultiAssetCapabilityError(f"{field} must use its typed enum")
        if self.product.value != self.market_type.value:
            raise MultiAssetCapabilityError("product and market_type must identify the same product")
        if not isinstance(self.evidence_version, str) or not self.evidence_version.strip():
            raise MultiAssetCapabilityError("evidence_version is required")
        if not isinstance(self.evidence_reference, str) or not self.evidence_reference.strip():
            raise MultiAssetCapabilityError("evidence_reference is required")
        if any(not isinstance(kind, CapabilityOrderKind) for kind in self.supported_order_kinds):
            raise MultiAssetCapabilityError("supported_order_kinds must use typed values")
        if len(set(self.supported_order_kinds)) != len(self.supported_order_kinds):
            raise MultiAssetCapabilityError("supported_order_kinds must be unique")
        if self.supports_write or self.auto_live_eligible:
            raise MultiAssetCapabilityError("multi-asset capability profiles are never write/live eligible")
        if self.environment is CapabilityEnvironment.DISABLED and any(
            (self.supports_public_market_data, self.supports_account_reads,
             self.supports_order_reads, self.supports_fill_reads)
        ):
            raise MultiAssetCapabilityError("DISABLED profile cannot claim read capabilities")

    @property
    def identity(self) -> tuple[str, AssetProduct, AssetMarketType, CapabilityEnvironment]:
        return (self.venue_id, self.product, self.market_type, self.environment)

    def supports_order_kind(self, order_kind: CapabilityOrderKind) -> bool:
        if not isinstance(order_kind, CapabilityOrderKind):
            raise MultiAssetCapabilityError("order_kind must use its typed enum")
        return order_kind in self.supported_order_kinds


@dataclass(frozen=True)
class MultiAssetCapabilityMatrix:
    """Exact lookup matrix with fail-closed resolution and no fallback."""

    profiles: Tuple[MultiAssetVenueCapability, ...]
    contract_version: str = MULTI_ASSET_CAPABILITY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != MULTI_ASSET_CAPABILITY_CONTRACT_VERSION:
            raise MultiAssetCapabilityError("unsupported capability contract version")
        if any(not isinstance(profile, MultiAssetVenueCapability) for profile in self.profiles):
            raise MultiAssetCapabilityError("profiles must be typed capability values")
        identities = [profile.identity for profile in self.profiles]
        if len(identities) != len(set(identities)):
            raise MultiAssetCapabilityError("capability identity must be unique")

    def resolve(
        self,
        venue_id: str,
        product: AssetProduct,
        market_type: AssetMarketType,
        environment: CapabilityEnvironment,
    ) -> MultiAssetVenueCapability:
        if not isinstance(product, AssetProduct) or not isinstance(market_type, AssetMarketType):
            raise MultiAssetCapabilityError("product and market_type must use typed enums")
        if not isinstance(environment, CapabilityEnvironment):
            raise MultiAssetCapabilityError("environment must use its typed enum")
        key = (_canonical_text(venue_id, "venue_id"), product, market_type, environment)
        for profile in self.profiles:
            if profile.identity == key:
                return profile
        raise UnsupportedCapability("no exact capability profile exists")


def gate_testnet_capability_matrix() -> MultiAssetCapabilityMatrix:
    """Return only public, read-only Gate TestNet profiles.

    Spot and perpetual products are separate entries.  No order, account, fill,
    leverage, funding, or write capability is guessed from the other product.
    """

    common = dict(
        venue_id="gate",
        environment=CapabilityEnvironment.TESTNET,
        evidence_version="gate-testnet-read-v1",
        evidence_reference="gate-testnet-public-read-contract",
        supports_public_market_data=True,
    )
    return MultiAssetCapabilityMatrix(
        profiles=(
            MultiAssetVenueCapability(product=AssetProduct.SPOT, market_type=AssetMarketType.SPOT, **common),
            MultiAssetVenueCapability(product=AssetProduct.PERPETUAL, market_type=AssetMarketType.PERPETUAL, **common),
        )
    )


def validate_capability_matrix(matrix: MultiAssetCapabilityMatrix) -> MultiAssetCapabilityMatrix:
    if not isinstance(matrix, MultiAssetCapabilityMatrix):
        raise MultiAssetCapabilityError("typed capability matrix is required")
    return matrix


__all__ = [
    "AssetMarketType",
    "AssetProduct",
    "CapabilityEnvironment",
    "CapabilityOrderKind",
    "MULTI_ASSET_CAPABILITY_CONTRACT_VERSION",
    "MultiAssetCapabilityError",
    "MultiAssetCapabilityMatrix",
    "MultiAssetVenueCapability",
    "UnsupportedCapability",
    "gate_testnet_capability_matrix",
    "validate_capability_matrix",
]
