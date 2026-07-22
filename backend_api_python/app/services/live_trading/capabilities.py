"""Canonical live-trading venue capability matrix.

Keep exchange support decisions here instead of repeating raw string lists in
routes, policy checks, smoke tests, and execution helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, FrozenSet, Iterable, Mapping, Set, Tuple


class VenueCapabilityValidationError(ValueError):
    """Raised when a venue is not explicitly safe for automatic live use."""


_COMMON_AUTO_LIVE_REQUIREMENTS = (
    "supports_client_order_id",
    "supports_query_by_client_order_id",
    "supports_exchange_fill_id",
    "supports_order_history",
    "supports_cancel_status_query",
)


@dataclass(frozen=True)
class VenueCapabilityProfile:
    """Safety capabilities for one exact exchange and market type."""

    exchange_id: str
    market_type: str
    supports_client_order_id: bool = False
    supports_query_by_client_order_id: bool = False
    supports_exchange_fill_id: bool = False
    supports_order_history: bool = False
    supports_cancel_status_query: bool = False
    supports_reduce_only: bool = False
    # PR-04 separates three facts that must never be inferred from one another:
    # whether the venue accepts an external ID, whether our rules can safely
    # generate one, and whether an existing ID can be used for a read-only lookup.
    accepts_external_client_order_id: bool = False
    can_generate_safe_client_order_id: bool = False
    query_by_exchange_order_id: bool = False
    query_by_client_order_id: bool = False
    list_order_fills: bool = False
    stable_fill_id: bool = False
    client_id_max_length: int | None = None
    client_id_pattern: str | None = None
    contract_tested: bool = False
    auto_live_eligible: bool = False

    def missing_auto_live_requirements(self) -> Tuple[str, ...]:
        market_type = str(self.market_type or "").strip().lower()
        if market_type not in {"spot", "swap"}:
            return ("market_type",)
        required = list(_COMMON_AUTO_LIVE_REQUIREMENTS)
        if market_type == "swap":
            required.append("supports_reduce_only")
        return tuple(name for name in required if not getattr(self, name))

    def validate_for_auto_live(self) -> None:
        """Fail closed unless this exact profile is tested and approved."""

        missing = self.missing_auto_live_requirements()
        reasons = list(missing)
        if not str(self.exchange_id or "").strip():
            reasons.append("exchange_id")
        if not self.contract_tested:
            reasons.append("contract_tested")
        if not self.auto_live_eligible:
            reasons.append("auto_live_eligible")
        if reasons:
            venue = self.exchange_id or "<unknown>"
            market = self.market_type or "<unknown>"
            raise VenueCapabilityValidationError(
                f"venue profile {venue}/{market} is not auto-live eligible: "
                + ", ".join(reasons)
            )


@dataclass(frozen=True)
class VenueCapability:
    """Legacy exchange catalog entry retained for routing compatibility.

    Automatic-live decisions must resolve an exact ``VenueCapabilityProfile``
    by exchange and market type. Aggregate flags are retained so this PR does
    not break existing imports or constructors, but they never authorize live
    trading by themselves.
    """

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

    def missing_auto_live_requirements(
        self, market_type: str | None = None
    ) -> Tuple[str, ...]:
        profile = get_venue_capability_profile(self.exchange_id, market_type)
        if profile is None:
            return ("venue_capability_profile",)
        return profile.missing_auto_live_requirements()

    def validate_for_auto_live(self, market_type: str | None = None) -> None:
        validate_for_auto_live(self, market_type)


def validate_for_auto_live(
    capability: VenueCapability | VenueCapabilityProfile,
    market_type: str | None = None,
) -> None:
    """Validate an exact venue profile while preserving the legacy API shape."""

    if isinstance(capability, VenueCapabilityProfile):
        if (
            market_type is not None
            and normalize_market_type(market_type)
            != normalize_market_type(capability.market_type)
        ):
            raise VenueCapabilityValidationError("venue profile market_type mismatch")
        capability.validate_for_auto_live()
        return
    if isinstance(capability, VenueCapability):
        profile = get_venue_capability_profile(capability.exchange_id, market_type)
        if profile is None:
            raise VenueCapabilityValidationError(
                "exact exchange_id + market_type profile is required"
            )
        profile.validate_for_auto_live()
        return
    raise VenueCapabilityValidationError("unknown venue capability")


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


_DEFAULT_VENUE_CAPABILITY_PROFILES = {
    (exchange_id, market_type): VenueCapabilityProfile(exchange_id, market_type)
    for exchange_id, capability in CRYPTO_VENUE_CAPABILITIES.items()
    for market_type in capability.market_types
}

# These read-only facts are intentionally profile-scoped.  In particular,
# Binance Spot accepts an externally supplied client ID but its safe-generation
# rule remains unknown: no Futures length or regex is inherited here.
_DEFAULT_VENUE_CAPABILITY_PROFILES.update(
    {
        ("binance", "swap"): VenueCapabilityProfile(
            "binance",
            "swap",
            accepts_external_client_order_id=True,
            can_generate_safe_client_order_id=True,
            query_by_exchange_order_id=True,
            query_by_client_order_id=True,
            list_order_fills=True,
            stable_fill_id=True,
            client_id_max_length=36,
            client_id_pattern=r"^[\.A-Z\:/a-z0-9_-]{1,36}$",
        ),
        ("binance", "spot"): VenueCapabilityProfile(
            "binance",
            "spot",
            accepts_external_client_order_id=True,
            can_generate_safe_client_order_id=False,
            query_by_exchange_order_id=True,
            query_by_client_order_id=True,
            list_order_fills=True,
            stable_fill_id=True,
            client_id_max_length=None,
            client_id_pattern=None,
        ),
    }
)

VENUE_CAPABILITY_PROFILES: Mapping[
    Tuple[str, str], VenueCapabilityProfile
] = MappingProxyType(_DEFAULT_VENUE_CAPABILITY_PROFILES)


def get_venue_capability_profile(
    exchange_id: str,
    market_type: str | None,
) -> VenueCapabilityProfile | None:
    """Resolve one exact profile; ambiguous or unknown inputs fail closed."""

    if market_type is None:
        return None
    key = (canonical_exchange_id(exchange_id), normalize_market_type(market_type))
    return VENUE_CAPABILITY_PROFILES.get(key)


def assert_supported_crypto_exchange_ids(exchange_ids: Iterable[str]) -> None:
    """Fail fast when a copied list drifts away from this matrix."""
    expected = supported_crypto_exchange_ids()
    actual = {canonical_exchange_id(v) for v in exchange_ids}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise AssertionError(f"crypto exchange list drift: missing={missing} extra={extra}")
