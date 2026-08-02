"""Run the deterministic strategy trace from a Gate read-only session.

This is the executable bridge from the public market-read boundary into the
existing backtest runner.  It stops at an immutable in-memory trace: no
accounts, credentials, persistence, exchange writes, or live authority are
available through this service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json

from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot
from app.domain.deterministic_backtest_contracts import BacktestExecutionKind, BacktestRunFacts
from app.domain.gate_backtest_dataset_contracts import build_gate_backtest_dataset
from app.domain.strategy_library_contracts import StrategyDefinition
from app.services.deterministic_backtest_service import (
    DeterministicBacktestService,
    DeterministicStrategyBacktest,
)
from app.services.gate_testnet_market_session_service import (
    GateTestnetMarketSessionReceipt,
)


GATE_DETERMINISTIC_BACKTEST_SERVICE_VERSION = "gate-deterministic-backtest-v1"


class GateDeterministicBacktestError(ValueError):
    """Gate session facts cannot form a deterministic trace."""


def _typed_fact(value: object, expected: type, required: tuple[str, ...]) -> bool:
    """Accept the canonical class and isolated-loader equivalents only.

    Some repository tests load the same immutable domain module in an isolated
    module namespace.  The resulting class identity differs, but the value is
    still required to have the exact canonical type name and immutable public
    contract.  This is deliberately structural and fail-closed; arbitrary
    duck-typed objects are not accepted.
    """
    return isinstance(value, expected) or (
        type(value).__name__ == expected.__name__
        and all(hasattr(value, name) for name in required)
    )


@dataclass(frozen=True, slots=True)
class GateDeterministicBacktestResult:
    session: GateTestnetMarketSessionReceipt
    dataset: BacktestDatasetSnapshot
    strategy_backtest: DeterministicStrategyBacktest
    result_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.session, GateTestnetMarketSessionReceipt):
            raise GateDeterministicBacktestError("session must be typed")
        if not _typed_fact(
            self.dataset,
            BacktestDatasetSnapshot,
            ("dataset_snapshot_id", "dataset_fingerprint", "bars", "instrument_id"),
        ) or not _typed_fact(
            self.strategy_backtest,
            DeterministicStrategyBacktest,
            ("dataset", "result_fingerprint", "to_public_dict"),
        ):
            raise GateDeterministicBacktestError("dataset and backtest must be typed")
        if self.dataset.dataset_snapshot_id != self.session.request.snapshot_id:
            raise GateDeterministicBacktestError("dataset/session snapshot mismatch")
        if self.strategy_backtest.dataset.dataset_snapshot_id != self.dataset.dataset_snapshot_id:
            raise GateDeterministicBacktestError("backtest dataset mismatch")
        encoded = json.dumps({
            "version": GATE_DETERMINISTIC_BACKTEST_SERVICE_VERSION,
            "session": self.session.session_fingerprint,
            "dataset": self.dataset.dataset_fingerprint,
            "backtest": self.strategy_backtest.result_fingerprint,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "result_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": GATE_DETERMINISTIC_BACKTEST_SERVICE_VERSION,
            "session_fingerprint": self.session.session_fingerprint,
            "dataset_fingerprint": self.dataset.dataset_fingerprint,
            "strategy_backtest": self.strategy_backtest.to_public_dict(),
            "result_fingerprint": self.result_fingerprint,
            "live_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class GateDeterministicBacktestService:
    backtest_service: DeterministicBacktestService = DeterministicBacktestService()

    def run(
        self,
        session: GateTestnetMarketSessionReceipt,
        run: BacktestRunFacts,
        strategy: StrategyDefinition,
        *,
        order_quantity: Decimal,
        execution_kind: BacktestExecutionKind = BacktestExecutionKind.MARKET,
    ) -> GateDeterministicBacktestResult:
        if not isinstance(session, GateTestnetMarketSessionReceipt):
            raise GateDeterministicBacktestError("session must be typed")
        if not isinstance(run, BacktestRunFacts) or not isinstance(strategy, StrategyDefinition):
            raise GateDeterministicBacktestError("run and strategy must be typed")
        if run.dataset_snapshot_id != session.request.snapshot_id:
            raise GateDeterministicBacktestError("run/session snapshot mismatch")
        try:
            dataset = build_gate_backtest_dataset(
                session.evidence.candles,
                dataset_snapshot_id=session.request.snapshot_id,
                as_of=session.request.observed_at,
            )
            result = self.backtest_service.run(
                run,
                dataset,
                strategy,
                order_quantity=order_quantity,
                execution_kind=execution_kind,
            )
            return GateDeterministicBacktestResult(session, dataset, result)
        except GateDeterministicBacktestError:
            raise
        except Exception as exc:
            raise GateDeterministicBacktestError("Gate deterministic backtest failed closed") from exc


__all__ = [
    "GATE_DETERMINISTIC_BACKTEST_SERVICE_VERSION",
    "GateDeterministicBacktestError",
    "GateDeterministicBacktestResult",
    "GateDeterministicBacktestService",
]
