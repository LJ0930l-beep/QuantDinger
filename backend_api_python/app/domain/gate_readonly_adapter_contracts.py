"""Injected-transport Gate read-only adapter boundary.

The adapter owns no HTTP client and no credentials.  A caller supplies a
transport that accepts an immutable ``GateReadRequest`` and returns a typed
``GateReadResponse``.  Any transport failure is converted to a typed error;
the adapter never turns an unavailable response into a market fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.domain.gate_read_transport_contracts import (
    GatePublicReadEndpoint,
    GateReadRequest,
    GateReadResponse,
    GateReadTransportError,
    validate_gate_read_request,
)
from app.domain.gate_readonly_contracts import GateReadCapabilityProfile, validate_gate_readonly_profile


GATE_READONLY_ADAPTER_CONTRACT_VERSION = "gate-readonly-adapter-v1"
GateReadTransport = Callable[[GateReadRequest], GateReadResponse]


class GateReadonlyAdapterError(GateReadTransportError):
    """A read-only adapter call cannot return a typed response."""


@dataclass(frozen=True, slots=True)
class GateReadonlyAdapter:
    """Profile-scoped, GET-only Gate adapter using caller-owned transport."""

    profile: GateReadCapabilityProfile
    transport: GateReadTransport

    def __post_init__(self) -> None:
        validate_gate_readonly_profile(self.profile)
        if not callable(self.transport):
            raise GateReadonlyAdapterError("a caller-owned read transport is required")
        if not self.profile.supports_public_market_data:
            raise GateReadonlyAdapterError("profile does not support public market reads")

    def request(self, request: GateReadRequest) -> GateReadResponse:
        validate_gate_read_request(request, self.profile)
        try:
            response = self.transport(request)
        except Exception as exc:
            # Do not leak transport exceptions or payloads across the domain
            # boundary.  The caller can retry at a higher policy layer.
            raise GateReadonlyAdapterError("Gate read transport failed") from exc
        if not isinstance(response, GateReadResponse):
            raise GateReadonlyAdapterError("transport must return GateReadResponse")
        return response

    def market_read(self, endpoint: GatePublicReadEndpoint, instrument_id: str, *, query: tuple[tuple[str, str], ...] = ()) -> GateReadResponse:
        request = GateReadRequest(self.profile.market_type, endpoint, instrument_id, query)
        return self.request(request)

    def candles(self, instrument_id: str, *, interval: str = "1m", limit: int = 100) -> GateReadResponse:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise GateReadonlyAdapterError("limit must be between 1 and 1000")
        return self.market_read(GatePublicReadEndpoint.CANDLESTICKS, instrument_id, query=(("interval", interval), ("limit", str(limit))))

    def order_book(self, instrument_id: str, *, limit: int = 20) -> GateReadResponse:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise GateReadonlyAdapterError("limit must be between 1 and 1000")
        return self.market_read(GatePublicReadEndpoint.ORDER_BOOK, instrument_id, query=(("limit", str(limit)),))


__all__ = ["GATE_READONLY_ADAPTER_CONTRACT_VERSION", "GateReadTransport", "GateReadonlyAdapter", "GateReadonlyAdapterError"]
