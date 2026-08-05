"""Authenticated response boundary for a unified Gate market read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.domain.gate_unified_market_snapshot_contracts import GateUnifiedMarketSnapshot


class ReadonlyGateUnifiedMarketServiceError(RuntimeError):
    """The unified market provider is unavailable or returned unsafe data."""


GateUnifiedMarketProvider = Callable[[str, str, int, int, datetime], Optional[GateUnifiedMarketSnapshot]]


@dataclass(frozen=True, slots=True)
class ReadonlyGateUnifiedMarketService:
    provider: Optional[GateUnifiedMarketProvider] = None

    def read_response(
        self,
        *,
        instrument_id: str,
        interval: str = "1m",
        candle_limit: int = 100,
        depth_limit: int = 20,
        observed_at: datetime,
        authorized: bool = True,
    ) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyGateUnifiedMarketServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "reason": "unified_market_read_disabled", "live_enabled": False}
        if not isinstance(instrument_id, str) or not instrument_id or instrument_id.strip() != instrument_id or not instrument_id.isascii() or any(ch.isspace() for ch in instrument_id):
            raise ReadonlyGateUnifiedMarketServiceError("instrument_id must be canonical ASCII text")
        if not isinstance(interval, str) or interval not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
            raise ReadonlyGateUnifiedMarketServiceError("interval is not supported")
        for name, value in (("candle_limit", candle_limit), ("depth_limit", depth_limit)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
                raise ReadonlyGateUnifiedMarketServiceError(f"{name} must be between 1 and 1000")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() != timezone.utc.utcoffset(observed_at):
            raise ReadonlyGateUnifiedMarketServiceError("observed_at must use zero-offset UTC")
        try:
            snapshot = self.provider(instrument_id, interval, candle_limit, depth_limit, observed_at.astimezone(timezone.utc))
        except Exception as exc:
            code = getattr(exc, "code", None)
            failed_markets = getattr(exc, "failed_markets", ())
            if isinstance(code, str) and code and isinstance(failed_markets, (tuple, list)):
                return 503, {"status": "UNAVAILABLE", "code": code, "data": {"failed_markets": list(failed_markets)}, "live_enabled": False}
            raise ReadonlyGateUnifiedMarketServiceError("Gate unified market provider failed") from exc
        if not isinstance(snapshot, GateUnifiedMarketSnapshot):
            raise ReadonlyGateUnifiedMarketServiceError("provider returned invalid unified market snapshot")
        return 200, snapshot.to_public_dict()


def service_from_app(app) -> ReadonlyGateUnifiedMarketService:
    return ReadonlyGateUnifiedMarketService(app.extensions.get("readonly_gate_unified_market_provider"))


__all__ = [
    "GateUnifiedMarketProvider",
    "ReadonlyGateUnifiedMarketService",
    "ReadonlyGateUnifiedMarketServiceError",
    "service_from_app",
]
