"""Transport-neutral Gate read request/response contracts.

The contract deliberately stops before HTTP.  A future adapter may provide a
transport implementation, but it must receive only these immutable GET
requests and return sanitized payloads; no API key or secret is represented.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Tuple

from app.domain.gate_readonly_contracts import GateMarketType, GateReadonlyContractError, GateReadCapabilityProfile, validate_gate_readonly_profile
from app.domain.gate_read_formatters import GateReadErrorKind, classify_gate_response_error


GATE_READ_TRANSPORT_CONTRACT_VERSION = "gate-read-transport-v1"


class GateReadTransportError(GateReadonlyContractError):
    """A read request or response cannot cross the transport boundary."""


class GatePublicReadEndpoint(str, Enum):
    TICKERS = "tickers"
    CANDLESTICKS = "candlesticks"
    ORDER_BOOK = "order_book"
    TRADES = "trades"
    INSTRUMENTS = "instruments"


_PUBLIC_PATHS = {
    GateMarketType.SPOT: {
        GatePublicReadEndpoint.TICKERS: "/spot/tickers",
        GatePublicReadEndpoint.CANDLESTICKS: "/spot/candlesticks",
        GatePublicReadEndpoint.ORDER_BOOK: "/spot/order_book",
        GatePublicReadEndpoint.TRADES: "/spot/trades",
        GatePublicReadEndpoint.INSTRUMENTS: "/spot/currency_pairs",
    },
    GateMarketType.PERPETUAL: {
        GatePublicReadEndpoint.TICKERS: "/futures/usdt/tickers",
        GatePublicReadEndpoint.CANDLESTICKS: "/futures/usdt/candlesticks",
        GatePublicReadEndpoint.ORDER_BOOK: "/futures/usdt/order_book",
        GatePublicReadEndpoint.TRADES: "/futures/usdt/trades",
        GatePublicReadEndpoint.INSTRUMENTS: "/futures/usdt/contracts",
    },
}


def _text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise GateReadTransportError(f"{field} must be canonical ASCII text")
    return value


@dataclass(frozen=True, slots=True)
class GateReadRequest:
    market_type: GateMarketType
    endpoint: GatePublicReadEndpoint
    instrument_id: str | None = None
    query: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, GateMarketType) or not isinstance(self.endpoint, GatePublicReadEndpoint):
            raise GateReadTransportError("typed Gate read request fields are required")
        _text(self.instrument_id, "instrument_id", optional=True)
        if not isinstance(self.query, tuple):
            raise GateReadTransportError("query must be an explicit tuple")
        keys = []
        for pair in self.query:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise GateReadTransportError("query entries must be key/value tuples")
            key, value = _text(pair[0], "query key"), _text(pair[1], "query value")
            keys.append(key)
        if keys != sorted(set(keys)):
            raise GateReadTransportError("query keys must be unique and sorted")
        if self.endpoint in {GatePublicReadEndpoint.TICKERS, GatePublicReadEndpoint.CANDLESTICKS, GatePublicReadEndpoint.ORDER_BOOK, GatePublicReadEndpoint.TRADES} and not self.instrument_id:
            raise GateReadTransportError("market endpoint requires instrument_id")

    @property
    def path(self) -> str:
        return _PUBLIC_PATHS[self.market_type][self.endpoint]

    @property
    def params(self) -> Mapping[str, str]:
        values = dict(self.query)
        if self.instrument_id is not None:
            values.setdefault("currency_pair", self.instrument_id)
        return values


@dataclass(frozen=True, slots=True)
class GateReadResponse:
    status_code: int
    payload: Mapping[str, Any] | list[Any] | None
    error_kind: GateReadErrorKind | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise GateReadTransportError("status_code must be a valid integer status")
        if self.status_code == 200 and self.error_kind is not None:
            raise GateReadTransportError("successful response cannot carry error_kind")
        if self.status_code != 200:
            expected = classify_gate_response_error(self.status_code, self.payload if isinstance(self.payload, Mapping) else None)
            if self.error_kind is not expected:
                raise GateReadTransportError("error_kind does not match status classification")


def validate_gate_read_request(request: GateReadRequest, profile: GateReadCapabilityProfile) -> GateReadRequest:
    if not isinstance(request, GateReadRequest):
        raise GateReadTransportError("request must be typed")
    validate_gate_readonly_profile(profile)
    if profile.market_type.value != request.market_type.value:
        raise GateReadTransportError("request market_type does not match capability profile")
    return request


__all__ = ["GATE_READ_TRANSPORT_CONTRACT_VERSION", "GatePublicReadEndpoint", "GateReadRequest", "GateReadResponse", "GateReadTransportError", "validate_gate_read_request"]
