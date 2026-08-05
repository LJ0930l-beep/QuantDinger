"""Explicit environment-backed Gate TestNet account read.

This is a temporary operator-facing bridge for a controlled TestNet session.
It is GET-only, requires an explicit enable flag, and never persists or
returns the credential values. Production account ownership should continue to
use the encrypted credential repository provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Callable, Mapping

from app.domain.gate_readonly_contracts import (
    GateEnvironment,
    GateMarketType,
    GateReadCapabilityProfile,
    gate_testnet_base_url_for_market,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType
from app.services.gate_private_read_account_service import GatePrivateReadAccountService
from app.services.gate_private_read_http_transport import build_gate_private_read_client
from app.services.gate_testnet_credential_provider import (
    GateTestnetCredentialProviderError,
    credential_from_environment,
)


class GateTestnetEnvReadError(RuntimeError):
    """The explicit Gate TestNet environment read was unavailable."""


def _market(value: str) -> tuple[AssetMarketType, GateMarketType]:
    raw = str(value or "").strip().lower()
    if raw == "spot":
        return AssetMarketType.SPOT, GateMarketType.SPOT
    if raw in {"perpetual", "perp", "swap", "futures", "future"}:
        return AssetMarketType.PERPETUAL, GateMarketType.PERPETUAL
    raise GateTestnetEnvReadError("market_type must be spot or perpetual")


def read_gate_testnet_environment_snapshot(
    *,
    market_type: str,
    account_scope: str,
    instrument_id: str | None = None,
    order_history: bool = False,
    environ: Mapping[str, str] | None = None,
    timestamp_provider: Callable[[], int] | None = None,
    opener: Any | None = None,
):
    """Read one real Gate TestNet account snapshot through the typed adapter."""

    values = os.environ if environ is None else environ
    if str(values.get("QUANT_GATE_TESTNET_ENV_READ_ENABLED", "")).strip() != "1":
        raise GateTestnetEnvReadError("Gate TestNet environment read is disabled")
    if not isinstance(account_scope, str) or not account_scope or account_scope != account_scope.strip() or not account_scope.isascii():
        raise GateTestnetEnvReadError("account_scope must be canonical ASCII text")
    asset_market, gate_market = _market(market_type)
    try:
        credential = credential_from_environment(environ=dict(values))
        now_provider = timestamp_provider or (lambda: int(datetime.now(timezone.utc).timestamp()))
        profile = GateReadCapabilityProfile(
            environment=GateEnvironment.TESTNET,
            market_type=gate_market,
            base_url=gate_testnet_base_url_for_market(gate_market),
            credential_ref="environment-testnet",
            supports_account_reads=True,
            supports_order_reads=True,
            supports_fill_reads=True,
        )
        client = build_gate_private_read_client(
            credential=credential,
            profile=profile,
            timestamp_provider=now_provider,
            opener=opener,
        )
        return GatePrivateReadAccountService().read_snapshot(
            client,
            market_type=asset_market,
            account_scope=account_scope,
            credential_ref=profile.credential_ref,
            instrument_id=instrument_id or None,
            order_history=order_history,
        )
    except GateTestnetEnvReadError:
        raise
    except GateTestnetCredentialProviderError as exc:
        raise GateTestnetEnvReadError("Gate TestNet credential source is unavailable") from exc
    except Exception as exc:
        raise GateTestnetEnvReadError("Gate TestNet account read failed") from exc


__all__ = ["GateTestnetEnvReadError", "read_gate_testnet_environment_snapshot"]
