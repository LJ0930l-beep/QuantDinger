"""HTTPS-only Gate TestNet public-read transport.

This module is the concrete, read-only edge for the Gate market-data
contracts.  It accepts only a typed :class:`GateReadRequest`, builds a GET
request against the validated TestNet base URL, and returns a typed
:class:`GateReadResponse`.  It has no credential provider, account endpoint,
order endpoint, retry side effect, or write method.

The opener is injectable so unit tests can prove URL/method/error behaviour
without opening a socket.  The default opener is the standard-library
``urlopen`` and is used only when an explicitly constructed caller chooses to
run a non-live public market read.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from app.domain.gate_read_transport_contracts import (
    GateReadRequest,
    GateReadResponse,
    GateReadTransportError,
    validate_gate_read_request,
)
from app.domain.gate_readonly_contracts import (
    GATE_TESTNET_API_PREFIX,
    GateReadCapabilityProfile,
    canonical_gate_testnet_base_url,
)
from app.domain.gate_read_formatters import classify_gate_response_error


GATE_READ_HTTP_TRANSPORT_CONTRACT_VERSION = "gate-read-http-transport-v1"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GateReadHttpTransportError(GateReadTransportError):
    """A public Gate GET cannot be completed or parsed safely."""


HttpOpener = Callable[..., Any]


def _json_payload(raw: bytes) -> Mapping[str, Any] | list[Any] | None:
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise GateReadHttpTransportError("Gate response body is too large or invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateReadHttpTransportError("Gate response is not valid JSON") from exc
    if value is not None and not isinstance(value, (dict, list)):
        raise GateReadHttpTransportError("Gate response JSON must be an object or array")
    return value


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = getattr(response, "code", None)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise GateReadHttpTransportError("Gate response status is invalid")
    return status


def _read_body(response: Any) -> bytes:
    body = response.read(_MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE_BYTES:
        raise GateReadHttpTransportError("Gate response body is too large or invalid")
    return body


@dataclass(frozen=True, slots=True)
class GateReadHttpTransport:
    """Concrete GET-only transport for a validated Gate TestNet profile."""

    profile: GateReadCapabilityProfile
    timeout_seconds: int = 10
    opener: HttpOpener | None = None
    user_agent: str = "QuantDinger-Readonly-Gate/1"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, GateReadCapabilityProfile):
            raise GateReadHttpTransportError("typed Gate capability profile is required")
        if not self.profile.supports_public_market_data:
            raise GateReadHttpTransportError("Gate profile does not allow public market reads")
        canonical_gate_testnet_base_url(self.profile.base_url)
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= 60:
            raise GateReadHttpTransportError("timeout_seconds must be between 1 and 60")
        if not isinstance(self.user_agent, str) or not self.user_agent or not self.user_agent.isascii() or self.user_agent.strip() != self.user_agent:
            raise GateReadHttpTransportError("user_agent must be canonical ASCII text")
        if self.opener is not None and not callable(self.opener):
            raise GateReadHttpTransportError("opener must be callable")

    def _url(self, request: GateReadRequest) -> str:
        validate_gate_read_request(request, self.profile)
        base = canonical_gate_testnet_base_url(self.profile.base_url)
        path = request.path
        if not path.startswith("/"):
            raise GateReadHttpTransportError("Gate path must be absolute")
        if not path.startswith(f"{GATE_TESTNET_API_PREFIX}/"):
            path = f"{GATE_TESTNET_API_PREFIX}{path}"
        query = urlencode(tuple(request.params.items()))
        return f"{base}{path}" + (f"?{query}" if query else "")

    def __call__(self, request: GateReadRequest) -> GateReadResponse:
        """Perform one public GET and return a typed response."""

        url = self._url(request)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise GateReadHttpTransportError("Gate transport requires HTTPS")
        http_request = Request(
            url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": self.user_agent},
        )
        opener = self.opener or urlopen
        try:
            response = opener(http_request, timeout=self.timeout_seconds)
            with response:
                status = _response_status(response)
                payload = _json_payload(_read_body(response))
        except HTTPError as exc:
            # Error bodies are parsed only as sanitized payload facts; the
            # exception itself never crosses the domain boundary.
            try:
                payload = _json_payload(exc.read(_MAX_RESPONSE_BYTES + 1))
            except GateReadHttpTransportError:
                payload = None
            status = _response_status(exc)
        except (URLError, TimeoutError, OSError) as exc:
            raise GateReadHttpTransportError("Gate public GET failed") from exc
        except GateReadHttpTransportError:
            raise
        except Exception as exc:
            raise GateReadHttpTransportError("Gate public GET failed") from exc

        if status == 200:
            if payload is None:
                raise GateReadHttpTransportError("successful Gate response is empty")
            return GateReadResponse(status, payload)
        error_payload = payload if isinstance(payload, Mapping) else None
        return GateReadResponse(status, payload, classify_gate_response_error(status, error_payload))


__all__ = [
    "GATE_READ_HTTP_TRANSPORT_CONTRACT_VERSION",
    "GateReadHttpTransport",
    "GateReadHttpTransportError",
]
