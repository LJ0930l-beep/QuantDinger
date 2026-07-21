"""Canonical live-trading venue capability matrix.

Keep exchange support decisions here instead of repeating raw string lists in
routes, policy checks, smoke tests, and execution helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Set, Tuple


class VenueCapabilityValidationError(ValueError):
    """Raised when a venue is not explicitly safe for automatic live use."""


@dataclass(frozen=True)
class VenueCapability:
    exchange_id: str
    market_types: FrozenSet[str]
    aliases: FrozenSet[str] = frozenset()
    supports_client_order_id: bool = False
    supports_query_by_client_order_id: bool = False
    supports_exchange_fill_id: bool = False
    supports_order_history: bool = False
    supports_cancel_status_query: bool = False
    supports_reduce_only: bool = False
    auto_live_eligible: bool = False

    @property
    def supports_spot(self) -> bool:
        return "spot" in self.market_types

    @property
    def supports_swap(self) -> bool:
        return "swap" in self.market_types

    def missing_auto_live_requirements(self) -> Tuple[str, ...]:
        required = (
            "supports_client_order_id",
            "supports_query_by_client_order_id",
            "supports_exchange_fill_id",
            "supports_order_history",
            "supports_cancel_status_query",
            "supports_reduce_only",
        )
        return tuple(name for name in required if not getattr(self, name))

    def validate_for_auto_live(self) -> None:
        """Fail closed unless safety capabilities and approval are explicit."""

        missing = self.missing_auto_live_requirements()
        if not self.auto_live_eligible or missing:
            reasons = list(missing)
            if not self.auto_live_eligible:
                reasons.append("auto_live_eligible")
            raise VenueCapabilityValidationError(
                f"venue {self.exchange_id or '<unknown>'} is not auto-live eligible: "
                + ", ".join(reasons)
            )


def validate_for_auto_live(capability: VenueCapability) -> None:
    if not isinstance(capability, VenueCapability):
        raise VenueCapabilityValidationError("unknown venue capability")
    capability.validate_for_auto_live()


CRYPTO_VENUE_CAPABILITIES: Dict[str, VenueCapability] = {
    "binance": VenueCapability("binance", frozenset({"spot", "swap"})),
    "okx": VenueCapability("okx", frozenset({"spot", "swap"})),
    "bitget": VenueCapability("bitget", frozenset({"spot", "swap"})),
    "bybit": VenueCapability("bybit", frozenset({"spot", "swap"})),
    "gate": VenueCapability("gate", frozenset({"spot", "swap"})),
    "htx": VenueCapability("htx", frozenset({"spot", "swap"})),
}


def canonical_exchange_id(exchange_id: str) -> str:
    raw = str(exchange_id or "").strip().lower()
    if raw in CRYPTO_VENUE_CAPABILITIES:
        return raw
    for canonical, capability in CRYPTO_VENUE_CAPABILITIES.items():
        if raw in capability.aliases:
            return canonical
    return raw


def supported_crypto_exchange_ids(*, include_aliases: bool = False) -> Set[str]:
    ids: Set[str] = set(CRYPTO_VENUE_CAPABILITIES)
    if include_aliases:
        for capability in CRYPTO_VENUE_CAPABILITIES.values():
            ids.update(capability.aliases)
    return ids


def crypto_exchange_ids_for_market_type(market_type: str) -> Set[str]:
    mt = normalize_market_type(market_type)
    return {
        exchange_id
        for exchange_id, capability in CRYPTO_VENUE_CAPABILITIES.items()
        if mt in capability.market_types
    }


def normalize_market_type(market_type: str) -> str:
    mt = str(market_type or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        return "swap"
    if mt not in ("spot", "swap"):
        return mt
    return mt


def assert_supported_crypto_exchange_ids(exchange_ids: Iterable[str]) -> None:
    """Fail fast when a copied list drifts away from this matrix."""
    expected = supported_crypto_exchange_ids()
    actual = {canonical_exchange_id(v) for v in exchange_ids}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise AssertionError(f"crypto exchange list drift: missing={missing} extra={extra}")
