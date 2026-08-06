"""Opt-in Gate TestNet private-read provider for the authenticated read API.

This provider is deliberately not installed by default.  When enabled by the
application, it resolves an encrypted credential reference, validates that the
record is explicitly Gate TestNet, and then uses the GET-only private adapter.
It never exposes credential values, never authorizes writes, and fails closed
for live/demo/unknown environments.
"""

from __future__ import annotations

from datetime import datetime
import os
import threading
from typing import Any, Callable, Mapping

from app.domain.gate_readonly_contracts import (
    GateEnvironment,
    GateMarketType,
    GateReadCapabilityProfile,
    gate_testnet_base_url_for_market,
)
from app.domain.gate_rate_limit_contracts import GateCircuitSnapshot, GateCircuitState
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.domain.gate_unified_read_snapshot_contracts import build_gate_unified_read_snapshot
from app.services.exchange_execution import resolve_exchange_config
from app.services.gate_private_read_client import (
    GatePrivateCredential,
    GatePrivateReadAuthError,
    GatePrivateReadInvalidResponse,
    GatePrivateReadPermissionError,
    GatePrivateReadTemporaryError,
)
from app.services.gate_private_read_http_transport import build_gate_private_read_client
from app.services.gate_private_read_account_service import GatePrivateReadAccountService


class GatePrivateReadProviderError(RuntimeError):
    """The opt-in Gate provider is unavailable or failed closed.

    ``code`` is a small, safe diagnostic intended for the API boundary.  It
    never contains provider payloads, credentials, or exception text.
    """

    def __init__(self, message: str, *, code: str = "GATE_TESTNET_READ_FAILED", failed_markets=()):
        super().__init__(message)
        self.code = str(code)
        self.failed_markets = tuple(
            {"market_type": str(item.get("market_type") or "unknown"), "code": str(item.get("code") or self.code)}
            for item in (failed_markets or ())
            if isinstance(item, dict)
        )


class GatePrivateReadCircuitStore:
    """Small process-local circuit state store keyed by opaque read scope.

    The store contains only immutable typed circuit snapshots and an opaque
    credential/profile reference; it never stores keys, secrets, response
    payloads, or account facts.  The transport remains caller-owned and can
    be replaced with a durable/cluster store later without changing its
    pure circuit contract.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshots: dict[str, GateCircuitSnapshot] = {}

    def read(self, key: str) -> GateCircuitSnapshot:
        if (
            not isinstance(key, str)
            or not key
            or not key.isascii()
            or key != key.strip()
            or any(char.isspace() for char in key)
        ):
            raise GatePrivateReadProviderError("Gate circuit scope is invalid")
        with self._lock:
            return self._snapshots.get(key, GateCircuitSnapshot())

    def write(self, key: str, snapshot: GateCircuitSnapshot) -> None:
        if (
            not isinstance(key, str)
            or not key
            or not key.isascii()
            or key != key.strip()
            or any(char.isspace() for char in key)
        ):
            raise GatePrivateReadProviderError("Gate circuit scope is invalid")
        if not isinstance(snapshot, GateCircuitSnapshot):
            raise GatePrivateReadProviderError("Gate circuit snapshot is invalid")
        with self._lock:
            self._snapshots[key] = snapshot


CredentialResolver = Callable[[int, int], Mapping[str, Any]]


def _resolve_config(credential_id: int, user_id: int) -> Mapping[str, Any]:
    try:
        return resolve_exchange_config({"credential_id": int(credential_id)}, user_id=int(user_id))
    except Exception as exc:
        raise GatePrivateReadProviderError("Gate credential could not be resolved") from exc


def _market(value: str) -> tuple[AssetMarketType, GateMarketType]:
    raw = str(value or "").strip().lower()
    if raw == "spot":
        return AssetMarketType.SPOT, GateMarketType.SPOT
    if raw in {"perpetual", "perp", "swap", "futures", "future"}:
        return AssetMarketType.PERPETUAL, GateMarketType.PERPETUAL
    raise GatePrivateReadProviderError("Gate private read requires spot or perpetual market_type")


def _read_failure(exc: Exception) -> GatePrivateReadProviderError:
    if isinstance(exc, GatePrivateReadAuthError):
        code = "GATE_TESTNET_AUTH_REJECTED"
    elif isinstance(exc, GatePrivateReadPermissionError):
        code = "GATE_TESTNET_PERMISSION_OR_IP_REJECTED"
    elif isinstance(exc, GatePrivateReadTemporaryError):
        code = "GATE_TESTNET_NETWORK_UNAVAILABLE"
    elif isinstance(exc, GatePrivateReadInvalidResponse):
        code = "GATE_TESTNET_INVALID_RESPONSE"
    else:
        code = "GATE_TESTNET_READ_FAILED"
    return GatePrivateReadProviderError(
        "Gate private read failed",
        code=code,
        failed_markets=({"market_type": "unknown", "code": code},),
    )


def provider_from_database(
    *,
    credential_resolver: CredentialResolver = _resolve_config,
    client_builder=build_gate_private_read_client,
    circuit_store: GatePrivateReadCircuitStore | None = None,
):
    """Return a callback compatible with ``readonly_gate_account_provider``.

    The callback only becomes network-capable when
    ``QUANT_GATE_PRIVATE_READ_ENABLED=1`` is explicitly set.  No application
    startup path calls it automatically unless that flag is present.
    """

    store = circuit_store or GatePrivateReadCircuitStore()

    def provider(user_id: int, credential_id: int, market_type: str, account_scope: str, instrument_id: str, as_of: datetime):
        if os.getenv("QUANT_GATE_PRIVATE_READ_ENABLED", "0").strip() != "1":
            raise GatePrivateReadProviderError("Gate private read is disabled")
        try:
            config = dict(credential_resolver(int(credential_id), int(user_id)) or {})
        except GatePrivateReadProviderError:
            raise
        except Exception as exc:
            raise GatePrivateReadProviderError("Gate credential could not be resolved") from exc
        if str(config.get("exchange_id") or config.get("exchangeId") or "").strip().lower() != "gate":
            raise GatePrivateReadProviderError("credential is not a Gate credential")
        environment = str(config.get("environment") or config.get("network") or config.get("env") or "").strip().lower()
        if environment not in {"testnet", "sandbox", "test"}:
            raise GatePrivateReadProviderError("Gate private read requires explicit TestNet environment")
        api_key = str(config.get("api_key") or config.get("apiKey") or "").strip()
        api_secret = str(config.get("secret_key") or config.get("secret") or "").strip()
        asset_market, gate_market = _market(market_type)
        credential = GatePrivateCredential(api_key, api_secret, CapabilityEnvironment.TESTNET)
        profile = GateReadCapabilityProfile(
            environment=GateEnvironment.TESTNET,
            market_type=gate_market,
            base_url=gate_testnet_base_url_for_market(gate_market),
            credential_ref=f"credential-{int(credential_id)}",
            supports_account_reads=True,
            supports_order_reads=True,
            supports_fill_reads=True,
        )
        circuit_key = f"{profile.credential_ref}:{gate_market.value}"
        client = client_builder(
            credential=credential,
            profile=profile,
            timestamp_provider=lambda: int(as_of.timestamp()),
            circuit_snapshot_provider=lambda key=circuit_key: store.read(key),
            circuit_update=lambda snapshot, key=circuit_key: store.write(key, snapshot),
        )
        try:
            return GatePrivateReadAccountService().read_snapshot(
                client,
                market_type=asset_market,
                account_scope=str(account_scope),
                credential_ref=profile.credential_ref,
                instrument_id=instrument_id or None,
            )
        except GatePrivateReadProviderError:
            raise
        except Exception as exc:
            raise _read_failure(exc) from exc

    return provider


def unified_provider_from_database(*, credential_resolver: CredentialResolver = _resolve_config, client_builder=build_gate_private_read_client):
    """Build a same-credential Spot + Perpetual read-only provider.

    The two market books are fetched through the existing typed provider and
    remain separate in the aggregate.  A partial result is never returned as
    a complete portfolio snapshot; callers receive safe failed-market codes.
    """

    single = provider_from_database(credential_resolver=credential_resolver, client_builder=client_builder)

    def provider(user_id: int, credential_id: int, account_scope: str, instrument_id: str, as_of: datetime):
        snapshots = []
        failures = []
        for market_type in ("spot", "perpetual"):
            try:
                snapshots.append(single(user_id, credential_id, market_type, account_scope, instrument_id, as_of))
            except GatePrivateReadProviderError as exc:
                failures.append({"market_type": market_type, "code": exc.code})
        if failures:
            raise GatePrivateReadProviderError(
                "Gate unified private read failed",
                code=(failures[0]["code"] if len(failures) == 1 else "GATE_TESTNET_PARTIAL_READ"),
                failed_markets=failures,
            )
        try:
            return build_gate_unified_read_snapshot(tuple(snapshots), observed_at=as_of)
        except Exception as exc:
            raise GatePrivateReadProviderError("Gate unified snapshot failed", code="GATE_TESTNET_INVALID_RESPONSE") from exc

    return provider


__all__ = [
    "GatePrivateReadCircuitStore",
    "GatePrivateReadProviderError",
    "provider_from_database",
    "unified_provider_from_database",
]
