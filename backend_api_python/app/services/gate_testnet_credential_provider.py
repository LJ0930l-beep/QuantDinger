"""Explicit local Gate TestNet credential source.

This helper is for controlled TestNet rehearsal only.  It reads credentials
from process environment, never persists or logs them, and refuses any
environment other than TestNet.  Production account ownership still uses the
encrypted credential repository; this module is intentionally not installed
by application startup.
"""

from __future__ import annotations

import os

from app.domain.multi_asset_capability_contracts import CapabilityEnvironment
from app.services.gate_private_read_client import GatePrivateCredential


class GateTestnetCredentialProviderError(RuntimeError):
    """The explicit local TestNet credential source is unavailable."""


def credential_from_environment(*, environ: dict[str, str] | None = None) -> GatePrivateCredential:
    """Build a typed TestNet credential without exposing its values."""

    values = os.environ if environ is None else environ
    if str(values.get("AGENT_LIVE_TRADING_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}:
        raise GateTestnetCredentialProviderError("live trading must remain disabled")
    api_key = str(values.get("GATE_TESTNET_API_KEY", "")).strip()
    api_secret = str(values.get("GATE_TESTNET_API_SECRET", "")).strip()
    if not api_key or not api_secret:
        raise GateTestnetCredentialProviderError("explicit Gate TestNet environment credentials are required")
    try:
        return GatePrivateCredential(api_key, api_secret, CapabilityEnvironment.TESTNET)
    except Exception as exc:
        raise GateTestnetCredentialProviderError("Gate TestNet credentials are invalid") from exc


__all__ = ["GateTestnetCredentialProviderError", "credential_from_environment"]
