"""Explicit HTTPS transport for Gate TestNet order requests.

The transport is opt-in and only accepts the canonical Gate TestNet host.
Construction does not open a socket; callers still need an explicit
``enabled=True`` gate and a typed TestNet credential before a request can be
made.  Tests inject an opener, while production deployments must configure
the gate deliberately.  No Live endpoint or credential value is logged.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.domain.gate_readonly_contracts import (
    GATE_TESTNET_API_PREFIX,
    GATE_TESTNET_REST_BASE_URL,
    GateMarketType,
    canonical_gate_testnet_base_url,
)
from app.services.gate_testnet_order_client import (
    GateTestnetOrderCapabilityError,
    GateTestnetOrderInvalidResponse,
    GateTestnetOrderTemporaryError,
)


GateTestnetOpener = Callable[..., Any]
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GateTestnetOrderHttpTransportError(RuntimeError):
    """Gate TestNet HTTP boundary failure without sensitive details."""


def _json_payload(raw: bytes) -> Any:
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise GateTestnetOrderHttpTransportError("Gate TestNet response body is too large or invalid")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateTestnetOrderHttpTransportError("Gate TestNet response is not valid JSON") from exc
    if not isinstance(payload, (dict, list)):
        raise GateTestnetOrderHttpTransportError("Gate TestNet response JSON must be an object or array")
    return payload


def _status(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        value = getattr(response, "code", None)
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise GateTestnetOrderHttpTransportError("Gate TestNet response status is invalid")
    return value


@dataclass(frozen=True, slots=True)
class GateTestnetOrderHttpTransport:
    """Opt-in transport restricted to Gate's official TestNet endpoint."""

    base_url: str = GATE_TESTNET_REST_BASE_URL
    market_type: GateMarketType = GateMarketType.SPOT
    timeout_seconds: int = 10
    opener: GateTestnetOpener | None = None
    allow_testnet_writes: bool = False
    user_agent: str = "QuantDinger-Gate-TestNet-Order/1"

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, GateMarketType):
            raise GateTestnetOrderHttpTransportError("Gate TestNet market_type is invalid")
        canonical_gate_testnet_base_url(self.base_url, self.market_type)
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= 60:
            raise GateTestnetOrderHttpTransportError("timeout_seconds must be between 1 and 60")
        if not isinstance(self.allow_testnet_writes, bool):
            raise GateTestnetOrderHttpTransportError("allow_testnet_writes must be bool")
        if not isinstance(self.user_agent, str) or not self.user_agent.isascii() or self.user_agent.strip() != self.user_agent:
            raise GateTestnetOrderHttpTransportError("user_agent must be canonical ASCII text")
        if self.opener is not None and not callable(self.opener):
            raise GateTestnetOrderHttpTransportError("opener must be callable")

    def _url(self, path: str, query: str) -> str:
        value = str(path or "")
        if not value.startswith("/") or value.startswith("//"):
            raise GateTestnetOrderHttpTransportError("Gate path must be absolute")
        if not value.startswith(f"{GATE_TESTNET_API_PREFIX}/"):
            value = f"{GATE_TESTNET_API_PREFIX}{value}"
        url = f"{canonical_gate_testnet_base_url(self.base_url)}{value}"
        if query:
            url += f"?{query}"
        return url

    def request(self, method: str, path: str, query: str, body: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        verb = str(method or "").upper()
        if verb not in {"GET", "POST", "DELETE"}:
            raise GateTestnetOrderHttpTransportError("Gate TestNet method is unsupported")
        if verb != "GET" and not self.allow_testnet_writes:
            raise GateTestnetOrderCapabilityError("Gate TestNet writes require explicit opt-in")
        if not isinstance(headers, Mapping):
            raise GateTestnetOrderHttpTransportError("signed Gate TestNet headers are required")
        url = self._url(path, query)
        raw_body = body.encode("utf-8") if isinstance(body, str) else b""
        request = Request(url, data=raw_body or None, method=verb, headers={str(k): str(v) for k, v in headers.items()})
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", self.user_agent)
        opener = self.opener or urlopen
        response = None
        try:
            response = opener(request, timeout=self.timeout_seconds)
            with response:
                status = _status(response)
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            return status, _json_payload(raw)
        except HTTPError as exc:
            try:
                raw = exc.read(_MAX_RESPONSE_BYTES + 1)
                payload = _json_payload(raw)
            except Exception:
                payload = None
            return _status(exc), payload
        except (URLError, TimeoutError, OSError) as exc:
            raise GateTestnetOrderTemporaryError("Gate TestNet order network failed") from exc
        except GateTestnetOrderHttpTransportError as exc:
            raise GateTestnetOrderInvalidResponse(str(exc)) from exc
        except Exception as exc:
            raise GateTestnetOrderTemporaryError("Gate TestNet order transport failed") from exc


__all__ = ["GateTestnetOrderHttpTransport", "GateTestnetOrderHttpTransportError"]
