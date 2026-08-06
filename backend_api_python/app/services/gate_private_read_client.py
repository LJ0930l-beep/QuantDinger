"""Gate private REST read adapter with injectable transport.

The default transport is disabled.  Tests inject a fake transport and verify
signatures; deployments must explicitly opt into a read-only TestNet/Paper
profile before a network transport can be supplied.  No order-writing method
is exposed here.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode

from app.domain.multi_asset_capability_contracts import CapabilityEnvironment


class GatePrivateReadError(RuntimeError):
    """A typed private-read failure; credentials never appear in the message."""


class GatePrivateReadAuthError(GatePrivateReadError):
    pass


class GatePrivateReadPermissionError(GatePrivateReadError):
    """The key reached Gate but lacks the requested market/IP permission."""


class GatePrivateReadTemporaryError(GatePrivateReadError):
    pass


class GatePrivateReadInvalidResponse(GatePrivateReadError):
    pass


class GatePrivateReadTransport(Protocol):
    def request(self, method: str, path: str, query: str, body: str, headers: Mapping[str, str]) -> tuple[int, Any]: ...


class DisabledGatePrivateReadTransport:
    def request(self, method: str, path: str, query: str, body: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        raise GatePrivateReadError("Gate private read transport is disabled")


@dataclass(frozen=True, slots=True, repr=False)
class GatePrivateCredential:
    api_key: str
    api_secret: str
    environment: CapabilityEnvironment

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip() or not self.api_key.isascii() or any(ch.isspace() for ch in self.api_key):
            raise GatePrivateReadAuthError("Gate API key is invalid")
        if not isinstance(self.api_secret, str) or not self.api_secret.strip() or not self.api_secret.isascii() or any(ch.isspace() for ch in self.api_secret):
            raise GatePrivateReadAuthError("Gate API secret is invalid")
        if self.environment not in {CapabilityEnvironment.PAPER, CapabilityEnvironment.TESTNET}:
            raise GatePrivateReadAuthError("Gate private read requires an explicit environment")

    def __repr__(self) -> str:
        return f"GatePrivateCredential(api_key='***', api_secret='***', environment={self.environment.value!r})"


@dataclass(frozen=True, slots=True)
class GatePrivateReadClient:
    credential: GatePrivateCredential
    transport: GatePrivateReadTransport
    timestamp_provider: Any
    settle: str = "usdt"

    def __post_init__(self) -> None:
        if not isinstance(self.credential, GatePrivateCredential):
            raise GatePrivateReadAuthError("typed Gate credential is required")
        if not callable(self.timestamp_provider):
            raise GatePrivateReadError("timestamp provider is required")
        if self.settle not in {"usdt", "btc"}:
            raise GatePrivateReadError("unsupported Gate settlement currency")

    @classmethod
    def disabled(cls, credential: GatePrivateCredential, *, timestamp_provider=None) -> "GatePrivateReadClient":
        return cls(credential, DisabledGatePrivateReadTransport(), timestamp_provider or (lambda: 0))

    def read_spot_accounts(self) -> Any:
        return self._request("GET", "/api/v4/spot/accounts")

    def read_spot_instruments(self) -> Any:
        """Read Gate Spot currency-pair rules through the GET-only client."""
        return self._request("GET", "/api/v4/spot/currency_pairs")

    def read_futures_accounts(self) -> Any:
        return self._request("GET", f"/api/v4/futures/{self.settle}/accounts")

    def read_futures_instruments(self) -> Any:
        """Read Gate USDT perpetual contract rules through the GET-only client."""
        return self._request("GET", f"/api/v4/futures/{self.settle}/contracts")

    def read_spot_orders(self, *, currency_pair: str) -> Any:
        return self._request("GET", "/api/v4/spot/open_orders", {"currency_pair": currency_pair})

    def read_spot_order_history(self, *, currency_pair: str, status: str = "finished") -> Any:
        """Read historical Spot orders, including canceled/finished orders."""
        if status not in {"open", "finished"}:
            raise GatePrivateReadInvalidResponse("unsupported Spot order history status")
        return self._request("GET", "/api/v4/spot/orders", {"currency_pair": currency_pair, "status": status})

    def read_futures_orders(self, *, contract: str) -> Any:
        return self._request("GET", f"/api/v4/futures/{self.settle}/orders", {"contract": contract, "status": "open"})

    def read_futures_order_history(self, *, contract: str, status: str = "finished") -> Any:
        """Read historical Futures orders, including canceled/finished orders."""
        if status not in {"open", "finished"}:
            raise GatePrivateReadInvalidResponse("unsupported Futures order history status")
        return self._request("GET", f"/api/v4/futures/{self.settle}/orders", {"contract": contract, "status": status})

    def read_spot_fills(self, *, currency_pair: str) -> Any:
        return self._request("GET", "/api/v4/spot/my_trades", {"currency_pair": currency_pair})

    def read_futures_fills(self, *, contract: str) -> Any:
        return self._request("GET", f"/api/v4/futures/{self.settle}/my_trades", {"contract": contract})

    def read_futures_account_book(
        self,
        *,
        contract: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Any:
        """Read Gate's immutable futures balance-change evidence.

        The endpoint is read-only and exposes typed categories such as
        realized PnL, trading fee, and funding fee.  No write capability is
        added to this client.
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise GatePrivateReadError("Gate account-book limit is invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise GatePrivateReadError("Gate account-book offset is invalid")
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if contract:
            params["contract"] = str(contract)
        return self._request("GET", f"/api/v4/futures/{self.settle}/account_book", params)

    def read_futures_positions(self) -> Any:
        return self._request("GET", f"/api/v4/futures/{self.settle}/positions")

    def _request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        query = urlencode(sorted((params or {}).items()))
        body = ""
        timestamp = str(int(self.timestamp_provider()))
        body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
        signing_material = "\n".join((method.upper(), path, query, body_hash, timestamp))
        signature = hmac.new(self.credential.api_secret.encode("ascii"), signing_material.encode("utf-8"), hashlib.sha512).hexdigest()
        headers = {"KEY": self.credential.api_key, "SIGN": signature, "Timestamp": timestamp, "Content-Type": "application/json"}
        try:
            status, payload = self.transport.request(method.upper(), path, query, body, headers)
        except GatePrivateReadError:
            raise
        except Exception as exc:
            raise GatePrivateReadTemporaryError("Gate private read transport failed") from exc
        if status == 401:
            raise GatePrivateReadAuthError("Gate private read authorization failed")
        if status == 403:
            raise GatePrivateReadPermissionError("Gate private read permission denied")
        if status == 429 or status >= 500:
            raise GatePrivateReadTemporaryError("Gate private read is temporarily unavailable")
        if status < 200 or status >= 300:
            raise GatePrivateReadInvalidResponse("Gate private read returned an invalid status")
        if payload is None:
            raise GatePrivateReadInvalidResponse("Gate private read returned no payload")
        return payload


__all__ = [
    "DisabledGatePrivateReadTransport", "GatePrivateCredential", "GatePrivateReadAuthError",
    "GatePrivateReadClient", "GatePrivateReadError", "GatePrivateReadInvalidResponse",
    "GatePrivateReadPermissionError", "GatePrivateReadTemporaryError", "GatePrivateReadTransport",
]
