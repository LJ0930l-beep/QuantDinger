"""Read-only Gate TestNet market evidence service.

This is the first concrete market-data edge for the product.  It only uses
the existing GET-only Gate adapter and returns immutable, sanitized evidence;
there is no credential provider, order method, persistence, or live authority.
The provider is optional and is installed only when an operator explicitly
enables public TestNet reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from app.domain.gate_readonly_contracts import GateMarketType
from app.services.gate_market_research_service import GateMarketEvidenceBundle


GATE_PUBLIC_MARKET_SERVICE_VERSION = "gate-public-market-read-v1"


class GatePublicMarketServiceError(RuntimeError):
    """A public Gate market read is unavailable or not typed."""


MarketProvider = Callable[[str, GateMarketType, str, int, int, datetime], GateMarketEvidenceBundle]


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GatePublicMarketServiceError("observed_at must use a zero-offset UTC datetime")
    return value.astimezone(timezone.utc)


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _bundle_public_dict(bundle: GateMarketEvidenceBundle) -> dict[str, object]:
    """Serialize only canonical market facts; never include transport details."""
    return {
        "contract_version": GATE_PUBLIC_MARKET_SERVICE_VERSION,
        "environment": "TESTNET",
        "market_type": bundle.market_type.value,
        "instrument_id": bundle.instrument_id,
        "interval": bundle.interval,
        "snapshot_id": bundle.snapshot_id,
        "rule_version": bundle.rule_version,
        "observed_at": bundle.observed_at.isoformat(),
        "bundle_fingerprint": bundle.bundle_fingerprint,
        "candles": [
            {
                "open_time": item.open_time.isoformat(),
                "close_time": item.close_time.isoformat(),
                "open": _decimal(item.open_price),
                "high": _decimal(item.high_price),
                "low": _decimal(item.low_price),
                "close": _decimal(item.close_price),
                "volume": _decimal(item.volume),
                "sequence": item.sequence,
                "evidence_hash": item.evidence_hash,
            }
            for item in bundle.candles
        ],
        "order_book": {
            "occurred_at": bundle.order_book.occurred_at.isoformat(),
            "observed_at": bundle.order_book.observed_at.isoformat(),
            "sequence": bundle.order_book.sequence,
            "bids": [[_decimal(item.price), _decimal(item.quantity)] for item in bundle.order_book.bids],
            "asks": [[_decimal(item.price), _decimal(item.quantity)] for item in bundle.order_book.asks],
            "evidence_hash": bundle.order_book.evidence_hash,
        },
        "network_access": True,
        "live_enabled": False,
    }


@dataclass(frozen=True, slots=True)
class GatePublicMarketReadService:
    provider: Optional[MarketProvider] = None

    def read_response(
        self,
        *,
        instrument_id: str,
        market_type: GateMarketType,
        interval: str = "1m",
        candle_limit: int = 100,
        depth_limit: int = 20,
        observed_at: datetime,
        authorized: bool = True,
    ) -> tuple[int, dict[str, object]]:
        if not isinstance(authorized, bool):
            raise GatePublicMarketServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "reason": "public_market_read_disabled", "live_enabled": False}
        if not isinstance(market_type, GateMarketType):
            raise GatePublicMarketServiceError("market_type must be typed")
        if not isinstance(instrument_id, str) or not instrument_id or instrument_id.strip() != instrument_id or not instrument_id.isascii() or any(ch.isspace() for ch in instrument_id):
            raise GatePublicMarketServiceError("instrument_id must be canonical ASCII text")
        if not isinstance(interval, str) or interval not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
            raise GatePublicMarketServiceError("interval is not supported")
        for name, value, lower, upper in (("candle_limit", candle_limit, 1, 1000), ("depth_limit", depth_limit, 1, 1000)):
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise GatePublicMarketServiceError(f"{name} must be between {lower} and {upper}")
        observed = _utc(observed_at)
        try:
            bundle = self.provider(instrument_id, market_type, interval, candle_limit, depth_limit, observed)
        except Exception as exc:
            raise GatePublicMarketServiceError("Gate public market read failed") from exc
        if not isinstance(bundle, GateMarketEvidenceBundle):
            raise GatePublicMarketServiceError("provider returned invalid market evidence")
        return 200, _bundle_public_dict(bundle)


def service_from_app(app) -> GatePublicMarketReadService:
    return GatePublicMarketReadService(app.extensions.get("readonly_gate_public_market_provider"))


__all__ = [
    "GATE_PUBLIC_MARKET_SERVICE_VERSION",
    "GatePublicMarketReadService",
    "GatePublicMarketServiceError",
    "service_from_app",
]
