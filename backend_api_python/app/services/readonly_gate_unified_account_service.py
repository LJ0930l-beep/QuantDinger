"""Authenticated response boundary for a unified Gate account read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.domain.gate_unified_read_snapshot_contracts import GateUnifiedReadSnapshot


class ReadonlyGateUnifiedAccountServiceError(RuntimeError):
    """The unified Gate provider is unavailable or returned unsafe data."""


GateUnifiedAccountProvider = Callable[[int, int, str, str, datetime], Optional[GateUnifiedReadSnapshot]]


@dataclass(frozen=True, slots=True)
class ReadonlyGateUnifiedAccountService:
    provider: Optional[GateUnifiedAccountProvider] = None

    def read_response(
        self,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        instrument_id: str,
        as_of: datetime,
        authorized: bool = True,
    ) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyGateUnifiedAccountServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyGateUnifiedAccountServiceError("as_of must use zero-offset UTC")
        try:
            snapshot = self.provider(
                int(user_id), int(credential_id), str(account_scope), str(instrument_id), as_of.astimezone(timezone.utc)
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            failed_markets = getattr(exc, "failed_markets", ())
            if isinstance(code, str) and code and isinstance(failed_markets, (tuple, list)):
                status = 403 if code == "GATE_TESTNET_PERMISSION_OR_IP_REJECTED" else (
                    400 if code == "GATE_TESTNET_AUTH_REJECTED" else 503
                )
                return status, {
                    "status": "UNAVAILABLE",
                    "msg": code,
                    "code": code,
                    "data": {"failed_markets": list(failed_markets)},
                    "live_enabled": False,
                }
            raise ReadonlyGateUnifiedAccountServiceError("Gate unified account provider failed") from exc
        if snapshot is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(snapshot, GateUnifiedReadSnapshot):
            raise ReadonlyGateUnifiedAccountServiceError("provider returned invalid Gate unified snapshot")
        body = snapshot.to_public_dict()
        read_health = body.get("read_health")
        if not isinstance(read_health, dict) or read_health.get("account_facts_verified") is not True:
            # Do not expose an incomplete aggregate as a successful account
            # read.  In particular, an empty balance row must never become a
            # synthetic zero available/total value in the UI.
            return 503, {
                "status": "UNAVAILABLE",
                "code": "GATE_ACCOUNT_FACTS_INCOMPLETE",
                "data": {"read_health": read_health or {}},
                "live_enabled": False,
            }
        return 200, body

    def read_health_response(
        self,
        *,
        user_id: int,
        credential_id: int,
        account_scope: str,
        instrument_id: str,
        as_of: datetime,
        authorized: bool = True,
    ) -> tuple[int, dict]:
        """Return only the sanitized read-health receipt for API consumers.

        The provider is still called through the same all-or-nothing boundary;
        this method never derives reconciliation or market health from an
        account snapshot and never exposes credentials or raw venue payloads.
        """

        status, body = self.read_response(
            user_id=user_id,
            credential_id=credential_id,
            account_scope=account_scope,
            instrument_id=instrument_id,
            as_of=as_of,
            authorized=authorized,
        )
        if status != 200:
            return status, body
        health = body.get("read_health")
        if not isinstance(health, dict):
            raise ReadonlyGateUnifiedAccountServiceError("unified account read-health receipt is missing")
        return status, {
            "contract_version": body.get("contract_version"),
            "status": health.get("status", "UNAVAILABLE"),
            "venue_id": body.get("venue_id"),
            "account_scope": body.get("account_scope"),
            "environment": body.get("environment"),
            "observed_at": body.get("observed_at"),
            "market_types": body.get("market_types", []),
            "snapshot_fingerprint": body.get("snapshot_fingerprint"),
            "read_health": health,
            "live_enabled": False,
        }


def service_from_app(app) -> ReadonlyGateUnifiedAccountService:
    return ReadonlyGateUnifiedAccountService(app.extensions.get("readonly_gate_unified_account_provider"))


__all__ = [
    "GateUnifiedAccountProvider",
    "ReadonlyGateUnifiedAccountService",
    "ReadonlyGateUnifiedAccountServiceError",
    "service_from_app",
]
