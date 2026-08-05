"""Sequential Gate TestNet read-only rehearsal orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.gate_testnet_rehearsal_contracts import (
    GateTestnetRehearsalResult,
    GateTestnetRehearsalSnapshot,
    GateTestnetRehearsalStatus,
)
from app.services.gate_testnet_market_session_service import (
    GateTestnetMarketSessionRequest,
    GateTestnetMarketSessionService,
)


class GateTestnetRehearsalServiceError(RuntimeError):
    """The read-only rehearsal failed closed."""


@dataclass(frozen=True, slots=True)
class GateTestnetRehearsalService:
    session_service: GateTestnetMarketSessionService

    def __post_init__(self) -> None:
        if not isinstance(self.session_service, GateTestnetMarketSessionService):
            raise GateTestnetRehearsalServiceError("typed Gate session service is required")

    def run(self, requests: tuple[GateTestnetMarketSessionRequest, ...]) -> GateTestnetRehearsalResult:
        if not isinstance(requests, tuple) or not requests or any(not isinstance(item, GateTestnetMarketSessionRequest) for item in requests):
            raise GateTestnetRehearsalServiceError("rehearsal requires a non-empty typed request tuple")
        seen: set[str] = set()
        snapshots: list[GateTestnetRehearsalSnapshot] = []
        previous_observed = None
        try:
            for request in requests:
                if request.snapshot_id in seen:
                    raise GateTestnetRehearsalServiceError("rehearsal snapshot identity repeated")
                if previous_observed is not None and request.observed_at <= previous_observed:
                    raise GateTestnetRehearsalServiceError("rehearsal observations must be strictly increasing")
                receipt = self.session_service.read(request)
                snapshots.append(GateTestnetRehearsalSnapshot(
                    request.snapshot_id,
                    receipt.session_fingerprint,
                    request.instrument_id,
                    request.observed_at,
                    receipt.evidence.bundle_fingerprint,
                ))
                seen.add(request.snapshot_id)
                previous_observed = request.observed_at
        except GateTestnetRehearsalServiceError:
            raise
        except Exception as exc:
            raise GateTestnetRehearsalServiceError("Gate TestNet rehearsal failed") from exc
        return GateTestnetRehearsalResult(GateTestnetRehearsalStatus.READY, tuple(snapshots), "read_only_rehearsal_complete")


__all__ = ["GateTestnetRehearsalService", "GateTestnetRehearsalServiceError"]
