"""Concrete GET-only transport for Gate private TestNet reads.

The existing :class:`GatePrivateReadClient` owns Gate's HMAC signing and
passes a fully signed request to this transport.  This edge is intentionally
limited to GET requests and the validated Gate TestNet host.  It can be
constructed for an injected opener in tests; callers must explicitly opt in
to network access by constructing it.  No write method is exposed here.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from app.domain.gate_readonly_contracts import (
    GATE_TESTNET_API_PREFIX,
    GATE_TESTNET_FUTURES_REST_BASE_URL,
    GATE_TESTNET_REST_BASE_URL,
    GateReadCapabilityProfile,
    canonical_gate_testnet_base_url,
)
from app.domain.gate_rate_limit_contracts import (
    GateCircuitSnapshot,
    GateRateLimitPolicy,
    GateTransportError,
    classify_gate_transport,
    circuit_allows_request,
    enter_half_open,
    record_circuit_result,
    retry_delay_seconds,
    should_retry,
)
from app.services.gate_private_read_client import (
    GatePrivateReadClient,
    GatePrivateReadInvalidResponse,
    GatePrivateReadTemporaryError,
)


GatePrivateOpener = Callable[..., Any]
GateCircuitSnapshotProvider = Callable[[], GateCircuitSnapshot]
GateCircuitSnapshotSink = Callable[[GateCircuitSnapshot], None]
GateClock = Callable[[], int]
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GatePrivateReadHttpTransportError(RuntimeError):
    """The signed Gate private GET could not be completed safely."""


def _configured_proxy_url() -> str | None:
    """Resolve the explicit application egress proxy without exposing it."""

    value = (os.environ.get("PROXY_URL") or "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GatePrivateReadHttpTransportError("PROXY_URL must be an HTTP(S) proxy")
    return value


def _default_opener() -> GatePrivateOpener:
    proxy = _configured_proxy_url()
    if proxy is None:
        return urlopen
    return build_opener(ProxyHandler({"http": proxy, "https": proxy})).open


def _shared_testnet_fallback_url(url: str, market_type) -> str | None:
    """Return the shared TestNet URL after a futures gateway failure."""

    if getattr(market_type, "value", market_type) != "perpetual":
        return None
    parsed = urlsplit(url)
    if parsed.hostname != urlsplit(GATE_TESTNET_FUTURES_REST_BASE_URL).hostname:
        return None
    shared = urlsplit(GATE_TESTNET_REST_BASE_URL)
    return urlunsplit((shared.scheme, shared.netloc, parsed.path, parsed.query, ""))


def _json_payload(raw: bytes) -> Any:
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise GatePrivateReadHttpTransportError("Gate response body is too large or invalid")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatePrivateReadHttpTransportError("Gate response is not valid JSON") from exc
    if not isinstance(payload, (dict, list)):
        raise GatePrivateReadHttpTransportError("Gate response JSON must be an object or array")
    return payload


def _status(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        value = getattr(response, "code", None)
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise GatePrivateReadHttpTransportError("Gate response status is invalid")
    return value


@dataclass(frozen=True, slots=True)
class GatePrivateReadHttpTransport:
    """GET-only transport for an explicit Gate TestNet capability profile."""

    profile: GateReadCapabilityProfile
    timeout_seconds: int = 10
    opener: GatePrivateOpener | None = None
    user_agent: str = "QuantDinger-Private-Readonly-Gate/1"
    retry_policy: GateRateLimitPolicy = GateRateLimitPolicy()
    sleep: Callable[[float], None] = time.sleep
    circuit_snapshot_provider: GateCircuitSnapshotProvider | None = None
    circuit_update: GateCircuitSnapshotSink | None = None
    now_seconds: GateClock = lambda: int(time.time())

    def __post_init__(self) -> None:
        if not isinstance(self.profile, GateReadCapabilityProfile):
            raise GatePrivateReadHttpTransportError("typed Gate capability profile is required")
        canonical_gate_testnet_base_url(self.profile.base_url, self.profile.market_type)
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= 60:
            raise GatePrivateReadHttpTransportError("timeout_seconds must be between 1 and 60")
        if not isinstance(self.user_agent, str) or not self.user_agent.isascii() or not self.user_agent.strip() == self.user_agent:
            raise GatePrivateReadHttpTransportError("user_agent must be canonical ASCII text")
        if self.opener is not None and not callable(self.opener):
            raise GatePrivateReadHttpTransportError("opener must be callable")
        if not isinstance(self.retry_policy, GateRateLimitPolicy):
            raise GatePrivateReadHttpTransportError("retry_policy must be typed")
        if not callable(self.sleep):
            raise GatePrivateReadHttpTransportError("sleep must be callable")
        if (self.circuit_snapshot_provider is None) != (self.circuit_update is None):
            raise GatePrivateReadHttpTransportError(
                "circuit_snapshot_provider and circuit_update must be supplied together"
            )
        if self.circuit_snapshot_provider is not None and not callable(self.circuit_snapshot_provider):
            raise GatePrivateReadHttpTransportError("circuit_snapshot_provider must be callable")
        if self.circuit_update is not None and not callable(self.circuit_update):
            raise GatePrivateReadHttpTransportError("circuit_update must be callable")
        if not callable(self.now_seconds):
            raise GatePrivateReadHttpTransportError("now_seconds must be callable")

    def _circuit_before_request(self) -> None:
        provider = self.circuit_snapshot_provider
        update = self.circuit_update
        if provider is None or update is None:
            return
        snapshot = provider()
        if not isinstance(snapshot, GateCircuitSnapshot):
            raise GatePrivateReadHttpTransportError("circuit snapshot must be typed")
        try:
            now = self.now_seconds()
            if isinstance(now, bool) or not isinstance(now, int) or now < 0:
                raise ValueError
            if not circuit_allows_request(snapshot, now, self.retry_policy):
                raise GatePrivateReadTemporaryError("Gate private read circuit is open")
            next_snapshot = enter_half_open(snapshot, now_seconds=now, policy=self.retry_policy)
        except GatePrivateReadTemporaryError:
            raise
        except Exception as exc:
            raise GatePrivateReadHttpTransportError("circuit clock or policy state is invalid") from exc
        if next_snapshot != snapshot:
            update(next_snapshot)

    def _circuit_after_request(
        self,
        *,
        status_code: int | None = None,
        error: GateTransportError | None = None,
    ) -> None:
        provider = self.circuit_snapshot_provider
        update = self.circuit_update
        if provider is None or update is None:
            return
        snapshot = provider()
        if not isinstance(snapshot, GateCircuitSnapshot):
            raise GatePrivateReadHttpTransportError("circuit snapshot must be typed")
        try:
            now = self.now_seconds()
            if isinstance(now, bool) or not isinstance(now, int) or now < 0:
                raise ValueError
            if status_code is not None and 200 <= status_code <= 299:
                next_snapshot = record_circuit_result(
                    snapshot, now_seconds=now, policy=self.retry_policy, success=True
                )
            else:
                failure = classify_gate_transport(status_code=status_code, error=error)
                next_snapshot = record_circuit_result(
                    snapshot, now_seconds=now, policy=self.retry_policy, failure=failure
                )
        except Exception as exc:
            raise GatePrivateReadHttpTransportError("circuit result state is invalid") from exc
        if next_snapshot != snapshot:
            update(next_snapshot)

    def _url(self, path: str, query: str) -> str:
        value = str(path or "")
        if not value.startswith("/") or value.startswith("//"):
            raise GatePrivateReadHttpTransportError("Gate path must be absolute")
        if not value.startswith(f"{GATE_TESTNET_API_PREFIX}/"):
            value = f"{GATE_TESTNET_API_PREFIX}{value}"
        base = canonical_gate_testnet_base_url(self.profile.base_url, self.profile.market_type)
        url = f"{base}{value}"
        if query:
            url += f"?{query}"
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise GatePrivateReadHttpTransportError("Gate private transport requires HTTPS")
        return url

    def _fetch(self, url: str, opener: GatePrivateOpener, headers: Mapping[str, str]) -> tuple[int, Any]:
        request = Request(url, method="GET", headers={str(k): str(v) for k, v in headers.items()})
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", self.user_agent)
        try:
            response = opener(request, timeout=self.timeout_seconds)
            with response:
                status = _status(response)
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            payload = _json_payload(raw)
            return status, payload
        except HTTPError as exc:
            try:
                raw = exc.read(_MAX_RESPONSE_BYTES + 1)
                payload = _json_payload(raw)
            except Exception:
                payload = None
            return _status(exc), payload
        except (URLError, TimeoutError, OSError) as exc:
            raise GatePrivateReadTemporaryError("Gate private read network failed") from exc
        except GatePrivateReadHttpTransportError as exc:
            raise GatePrivateReadInvalidResponse(str(exc)) from exc
        except Exception as exc:
            raise GatePrivateReadTemporaryError("Gate private read transport failed") from exc

    def request(self, method: str, path: str, query: str, body: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        if str(method or "").upper() != "GET":
            raise GatePrivateReadHttpTransportError("Gate private transport is GET-only")
        if body:
            raise GatePrivateReadHttpTransportError("Gate private GET cannot carry a body")
        if not isinstance(headers, Mapping):
            raise GatePrivateReadHttpTransportError("signed headers are required")
        url = self._url(path, query)
        opener = self.opener or _default_opener()
        self._circuit_before_request()
        status = 599
        payload = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                status, payload = self._fetch(url, opener, headers)
            except GatePrivateReadTemporaryError:
                failure = classify_gate_transport(error=GateTransportError.TIMEOUT)
                if not should_retry(failure, attempt, self.retry_policy):
                    self._circuit_after_request(error=GateTransportError.TIMEOUT)
                    raise
                self.sleep(retry_delay_seconds(attempt, self.retry_policy))
                continue
            failure = classify_gate_transport(status_code=status)
            if not should_retry(failure, attempt, self.retry_policy):
                break
            self.sleep(retry_delay_seconds(attempt, self.retry_policy))
        fallback = _shared_testnet_fallback_url(url, self.profile.market_type)
        # Some unified APIv4 TestNet keys are rejected by the legacy futures
        # gateway with INVALID_KEY (401) even though the same credential is
        # accepted by the shared TestNet host.  Retry only the validated,
        # read-only fallback; the final status is still returned unchanged if
        # both hosts reject the request.
        if fallback and status in {401, 403, 502, 503, 504}:
            try:
                fallback_status, fallback_payload = self._fetch(fallback, opener, headers)
            except GatePrivateReadTemporaryError:
                pass
            else:
                status, payload = fallback_status, fallback_payload
        self._circuit_after_request(status_code=status)
        return status, payload


def build_gate_private_read_client(
    *,
    credential,
    profile: GateReadCapabilityProfile,
    timestamp_provider,
    opener: GatePrivateOpener | None = None,
    circuit_snapshot_provider: GateCircuitSnapshotProvider | None = None,
    circuit_update: GateCircuitSnapshotSink | None = None,
    now_seconds: GateClock = lambda: int(time.time()),
):
    """Build the signed client only for a validated TestNet read profile."""

    transport = GatePrivateReadHttpTransport(
        profile,
        opener=opener,
        circuit_snapshot_provider=circuit_snapshot_provider,
        circuit_update=circuit_update,
        now_seconds=now_seconds,
    )
    return GatePrivateReadClient(credential, transport, timestamp_provider=timestamp_provider)


__all__ = ["GatePrivateReadHttpTransport", "GatePrivateReadHttpTransportError", "build_gate_private_read_client"]
