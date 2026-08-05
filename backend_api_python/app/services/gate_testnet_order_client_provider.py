"""Explicit Gate TestNet order-client factory.

The factory is intentionally not imported by application startup or a route.
It is a construction boundary for controlled TestNet runs: credentials come
from a caller-supplied environment mapping, writes require an explicit opt-in
flag, and the returned client still requires a typed admission result before
the execution worker can submit anything.
"""

from __future__ import annotations

import os
from typing import Callable, Mapping, Any

from app.domain.gate_readonly_contracts import (
    GATE_TESTNET_REST_BASE_URL,
    GateMarketType,
    gate_testnet_base_url_for_market,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType
from app.services.gate_private_read_client import GatePrivateCredential
from app.services.gate_testnet_credential_provider import (
    GateTestnetCredentialProviderError,
    credential_from_environment,
)
from app.services.gate_private_read_client import GatePrivateCredential
from app.domain.multi_asset_capability_contracts import CapabilityEnvironment
from app.services.gate_testnet_order_client import (
    ClientOrderIdValidator,
    GateTestnetOrderCapabilityError,
    GateTestnetOrderClient,
)
from app.services.gate_testnet_order_http_transport import GateTestnetOrderHttpTransport


class GateTestnetOrderClientProviderError(RuntimeError):
    """The explicit TestNet client could not be constructed safely."""


def _values(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _flag(values: Mapping[str, str], name: str) -> bool:
    return str(values.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def build_gate_testnet_order_client(
    *,
    market_type: AssetMarketType,
    account_scope: str,
    timestamp_provider: Callable[[], int],
    client_order_id_validator: ClientOrderIdValidator,
    environ: Mapping[str, str] | None = None,
    opener: Any | None = None,
    allow_writes: bool = False,
    base_url: str | None = None,
) -> GateTestnetOrderClient:
    """Build a Gate TestNet client without touching application state.

    ``allow_writes`` is deliberately false by default.  A caller requesting
    POST/DELETE must also set ``GATE_TESTNET_WRITE_ENABLED=1`` in the supplied
    environment mapping.  The factory never accepts a Live environment and
    never logs or persists credential values.
    """

    values = _values(environ)
    if not isinstance(market_type, AssetMarketType) or market_type not in {
        AssetMarketType.SPOT,
        AssetMarketType.PERPETUAL,
    }:
        raise GateTestnetOrderClientProviderError("Gate TestNet order market_type is invalid")
    if not isinstance(account_scope, str) or not account_scope or account_scope != account_scope.strip() or not account_scope.isascii():
        raise GateTestnetOrderClientProviderError("Gate TestNet account_scope must be canonical ASCII text")
    if not callable(timestamp_provider) or not callable(client_order_id_validator):
        raise GateTestnetOrderClientProviderError("typed timestamp and client ID validator are required")
    if allow_writes and not _flag(values, "GATE_TESTNET_WRITE_ENABLED"):
        raise GateTestnetOrderCapabilityError("Gate TestNet writes require explicit opt-in")
    try:
        credential: GatePrivateCredential = credential_from_environment(environ=dict(values))
        gate_market = GateMarketType.SPOT if market_type is AssetMarketType.SPOT else GateMarketType.PERPETUAL
        transport = GateTestnetOrderHttpTransport(
            base_url=base_url or gate_testnet_base_url_for_market(gate_market),
            market_type=gate_market,
            opener=opener,
            allow_testnet_writes=bool(allow_writes),
        )
        return GateTestnetOrderClient(
            credential=credential,
            transport=transport,
            timestamp_provider=timestamp_provider,
            market_type=market_type,
            account_scope=account_scope,
            client_order_id_validator=client_order_id_validator,
        )
    except GateTestnetOrderCapabilityError:
        raise
    except GateTestnetCredentialProviderError as exc:
        raise GateTestnetOrderClientProviderError("Gate TestNet credential source is unavailable") from exc
    except Exception as exc:
        raise GateTestnetOrderClientProviderError("Gate TestNet order client is unavailable") from exc


def build_gate_testnet_order_client_from_config(
    config: Mapping[str, Any],
    *,
    market_type: AssetMarketType,
    account_scope: str,
    timestamp_provider: Callable[[], int],
    client_order_id_validator: ClientOrderIdValidator,
    environ: Mapping[str, str] | None = None,
    opener: Any | None = None,
    allow_writes: bool = False,
    base_url: str | None = None,
) -> GateTestnetOrderClient:
    """Build a guarded client from an already-resolved encrypted credential.

    ``resolve_exchange_config`` is expected to have decrypted the record for
    the authenticated owner.  This helper validates the exchange and
    environment before constructing the TestNet-only transport; it never
    persists, logs, or returns credential values.
    """

    if not isinstance(config, Mapping):
        raise GateTestnetOrderClientProviderError("Gate credential config is invalid")
    exchange_id = str(config.get("exchange_id") or config.get("exchangeId") or "").strip().lower()
    environment = str(config.get("environment") or config.get("network") or config.get("env") or "").strip().lower()
    if exchange_id != "gate" or environment not in {"testnet", "sandbox", "test"}:
        raise GateTestnetOrderCapabilityError("Gate order writes require an explicit TestNet credential")
    values = _values(environ)
    if allow_writes and not _flag(values, "GATE_TESTNET_WRITE_ENABLED"):
        raise GateTestnetOrderCapabilityError("Gate TestNet writes require explicit opt-in")
    try:
        api_key = str(config.get("api_key") or config.get("apiKey") or "").strip()
        api_secret = str(config.get("secret_key") or config.get("secret") or "").strip()
        credential = GatePrivateCredential(api_key, api_secret, CapabilityEnvironment.TESTNET)
        gate_market = GateMarketType.SPOT if market_type is AssetMarketType.SPOT else GateMarketType.PERPETUAL
        transport = GateTestnetOrderHttpTransport(
            base_url=base_url or gate_testnet_base_url_for_market(gate_market),
            market_type=gate_market,
            opener=opener,
            allow_testnet_writes=bool(allow_writes),
        )
        return GateTestnetOrderClient(
            credential=credential,
            transport=transport,
            timestamp_provider=timestamp_provider,
            market_type=market_type,
            account_scope=account_scope,
            client_order_id_validator=client_order_id_validator,
        )
    except GateTestnetOrderCapabilityError:
        raise
    except Exception as exc:
        raise GateTestnetOrderClientProviderError("Gate TestNet credential config is unavailable") from exc


__all__ = [
    "GateTestnetOrderClientProviderError",
    "build_gate_testnet_order_client",
    "build_gate_testnet_order_client_from_config",
]
