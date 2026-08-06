"""Authenticated, read-only Gate account snapshot response boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.domain.gate_read_snapshot_contracts import GateReadSnapshot


class ReadonlyGateAccountServiceError(RuntimeError):
    """The Gate account evidence provider is unavailable or unsafe."""


GateAccountProvider = Callable[[int, int, str, str, str, datetime], Optional[GateReadSnapshot]]


@dataclass(frozen=True, slots=True)
class ReadonlyGateAccountService:
    provider: Optional[GateAccountProvider] = None

    def read_response(
        self, *, user_id: int, credential_id: int, market_type: str,
        account_scope: str, instrument_id: str, as_of: datetime,
        authorized: bool = True,
    ) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyGateAccountServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyGateAccountServiceError("as_of must use zero-offset UTC")
        try:
            snapshot = self.provider(user_id, credential_id, market_type, account_scope, instrument_id, as_of.astimezone(timezone.utc))
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
            raise ReadonlyGateAccountServiceError("Gate account provider failed") from exc
        if snapshot is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(snapshot, GateReadSnapshot):
            raise ReadonlyGateAccountServiceError("provider returned invalid Gate read snapshot")
        body = snapshot.to_public_dict()
        body["status"] = "READY"
        body["live_enabled"] = False
        return 200, body


def service_from_app(app) -> ReadonlyGateAccountService:
    return ReadonlyGateAccountService(app.extensions.get("readonly_gate_account_provider"))


__all__ = ["GateAccountProvider", "ReadonlyGateAccountService", "ReadonlyGateAccountServiceError", "service_from_app"]
