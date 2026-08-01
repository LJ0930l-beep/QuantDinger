"""Pure Gate TestNet/read-only contracts.

This module deliberately contains no HTTP client, credential value, or runtime
integration.  It provides the immutable boundary that a future Gate adapter
must satisfy before it is allowed to read account or market facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from .decimal_values import DecimalInputTypeError, DecimalValueError


class GateReadonlyContractError(ValueError):
    """Base error for invalid Gate read-only/testnet facts."""


class GateUnsupportedEnvironment(GateReadonlyContractError):
    """Raised when a profile is not explicitly bound to Gate TestNet."""


class GateMarketType(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"


class GateEnvironment(str, Enum):
    TESTNET = "testnet"


GATE_TESTNET_REST_BASE_URL = "https://api-testnet.gateapi.io"
GATE_TESTNET_API_PREFIX = "/api/v4"


def _canonical_base_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise GateReadonlyContractError("Gate TestNet endpoint must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GateReadonlyContractError("Gate endpoint must not contain credentials or query data")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def canonical_gate_testnet_base_url(value: str = GATE_TESTNET_REST_BASE_URL) -> str:
    """Return the only endpoint accepted by the read-only Gate TestNet profile."""

    normalized = _canonical_base_url(value)
    expected = _canonical_base_url(GATE_TESTNET_REST_BASE_URL)
    if normalized != expected:
        raise GateUnsupportedEnvironment("Gate TestNet profile rejects non-TestNet endpoint")
    return normalized


@dataclass(frozen=True)
class GateReadCapabilityProfile:
    """Capability evidence for one Gate market profile.

    ``credential_ref`` is an opaque reference only; this model intentionally
    cannot hold API keys or secrets.  A future adapter must obtain values from
    a protected credential provider and must never place them in this object.
    """

    environment: GateEnvironment
    market_type: GateMarketType
    base_url: str = GATE_TESTNET_REST_BASE_URL
    credential_ref: str = ""
    supports_public_market_data: bool = True
    supports_account_reads: bool = False
    supports_order_reads: bool = False
    supports_fill_reads: bool = False
    writes_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.environment, GateEnvironment):
            raise GateReadonlyContractError("Gate environment must be TESTNET")
        if not isinstance(self.market_type, GateMarketType):
            raise GateReadonlyContractError("Gate market_type is invalid")
        canonical_gate_testnet_base_url(self.base_url)
        ref = str(self.credential_ref or "").strip()
        if not ref:
            raise GateReadonlyContractError("credential_ref is required but must not contain secret material")
        if any(ch in ref for ch in ("\n", "\r", " ")):
            raise GateReadonlyContractError("credential_ref must be opaque text")
        if self.writes_enabled:
            raise GateReadonlyContractError("Gate read-only profile cannot enable writes")


def validate_gate_readonly_profile(profile: GateReadCapabilityProfile) -> GateReadCapabilityProfile:
    """Fail closed unless the profile is explicit TestNet and write-disabled."""

    if not isinstance(profile, GateReadCapabilityProfile):
        raise GateReadonlyContractError("Gate capability profile is required")
    canonical_gate_testnet_base_url(profile.base_url)
    if profile.writes_enabled:
        raise GateReadonlyContractError("Gate write capability is not permitted by this contract")
    return profile


def gate_testnet_api_url(path: str) -> str:
    """Build a TestNet API URL from a relative API-v4 path, without network I/O."""

    value = str(path or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        raise GateReadonlyContractError("Gate API path must be absolute and relative to /api/v4")
    if value.startswith(f"{GATE_TESTNET_API_PREFIX}/"):
        suffix = value
    else:
        suffix = f"{GATE_TESTNET_API_PREFIX}{value}"
    return f"{GATE_TESTNET_REST_BASE_URL}{suffix}"


@dataclass(frozen=True)
class GateOhlcvBar:
    """Decimal-safe candle fact suitable for deterministic backtests."""

    timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ms, bool) or not isinstance(self.timestamp_ms, int) or self.timestamp_ms < 0:
            raise GateReadonlyContractError("timestamp_ms must be a non-negative integer")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(isinstance(v, float) for v in values):
            raise DecimalInputTypeError("Gate OHLCV values reject float inputs")
        if any(not isinstance(v, Decimal) for v in values):
            raise GateReadonlyContractError("Gate OHLCV values must use Decimal")
        if any(not v.is_finite() for v in values) or self.volume < 0:
            raise DecimalValueError("Gate OHLCV values must be finite and volume non-negative")
        if self.low > self.high or self.open < self.low or self.open > self.high or self.close < self.low or self.close > self.high:
            raise GateReadonlyContractError("Gate OHLCV price bounds are inconsistent")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, float):
        raise DecimalInputTypeError("Gate OHLCV import rejects float inputs")
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise GateReadonlyContractError("invalid Gate OHLCV decimal") from exc
    if not out.is_finite():
        raise DecimalValueError("Gate OHLCV decimal must be finite")
    return out


def normalize_gate_ohlcv(rows: Iterable[Sequence[Any]]) -> Tuple[GateOhlcvBar, ...]:
    """Normalize offline rows ``[timestamp_ms, O, H, L, C, volume]``.

    Rows must be strictly ordered and unique by timestamp.  This function is
    intentionally offline: it accepts supplied data only and never contacts a
    venue or reads credentials.
    """

    bars = []
    previous: Optional[int] = None
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 6:
            raise GateReadonlyContractError("Gate OHLCV row must contain six fields")
        ts = row[0]
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise GateReadonlyContractError("Gate OHLCV timestamp must be an integer")
        if previous is not None and ts <= previous:
            raise GateReadonlyContractError("Gate OHLCV timestamps must be strictly increasing")
        bars.append(GateOhlcvBar(ts, _decimal(row[1]), _decimal(row[2]), _decimal(row[3]), _decimal(row[4]), _decimal(row[5])))
        previous = ts
    return tuple(bars)


__all__ = [
    "GATE_TESTNET_API_PREFIX",
    "GATE_TESTNET_REST_BASE_URL",
    "GateEnvironment",
    "GateMarketType",
    "GateOhlcvBar",
    "GateReadCapabilityProfile",
    "GateReadonlyContractError",
    "GateUnsupportedEnvironment",
    "canonical_gate_testnet_base_url",
    "gate_testnet_api_url",
    "normalize_gate_ohlcv",
    "validate_gate_readonly_profile",
]
