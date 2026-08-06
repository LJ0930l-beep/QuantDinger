"""Safe read-only result contracts for deterministic backtests.

The result surface intentionally accepts an already validated
``BacktestReportSnapshot``.  It does not load a run, infer missing metrics, or
perform any persistence or execution.  A later repository/API adapter can
provide the snapshot without changing the public shape or its fail-closed
semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .backtest_report_contracts import BacktestReportSnapshot
from .deterministic_backtest_contracts import backtest_fingerprint


BACKTEST_RESULT_CONTRACT_VERSION = "backtest-result-v1"


class BacktestResultError(ValueError):
    """Invalid or incomplete read-only backtest result facts."""


class BacktestResultStatus(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"


@dataclass(frozen=True, slots=True)
class BacktestResultView:
    """Credential-free, JSON-ready view over one immutable report."""

    status: BacktestResultStatus
    report: BacktestReportSnapshot | None = None
    view_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, BacktestResultStatus):
            raise BacktestResultError("status must use BacktestResultStatus")
        if self.status is BacktestResultStatus.READY:
            if not isinstance(self.report, BacktestReportSnapshot):
                raise BacktestResultError("READY result requires a typed report")
        elif self.report is not None:
            raise BacktestResultError("unavailable result cannot carry report facts")
        object.__setattr__(self, "view_fingerprint", backtest_fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "contract_version": BACKTEST_RESULT_CONTRACT_VERSION,
            "status": self.status.value,
            "report_fingerprint": None if self.report is None else self.report.report_fingerprint,
        }

    def to_public_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contract_version": BACKTEST_RESULT_CONTRACT_VERSION,
            "status": self.status.value,
            "view_fingerprint": self.view_fingerprint,
        }
        if self.report is not None:
            body["report"] = self.report.to_public_dict()
        return body


@dataclass(frozen=True, slots=True)
class BacktestResultResponse:
    """HTTP-neutral response envelope for the read-only adapter."""

    http_status: int
    body: Mapping[str, Any]
    contract_version: str = BACKTEST_RESULT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.http_status, bool) or not isinstance(self.http_status, int) or not 100 <= self.http_status <= 599:
            raise BacktestResultError("http_status must be a valid integer status")
        if not isinstance(self.body, Mapping):
            raise BacktestResultError("body must be a mapping")
        if self.contract_version != BACKTEST_RESULT_CONTRACT_VERSION:
            raise BacktestResultError("contract_version is not canonical")


def serialize_backtest_result(view: BacktestResultView) -> BacktestResultResponse:
    """Map a typed view to a stable status/body pair."""

    if not isinstance(view, BacktestResultView):
        raise BacktestResultError("view must be a typed backtest result")
    if view.status is BacktestResultStatus.READY:
        status = 200
    elif view.status is BacktestResultStatus.UNAUTHORIZED:
        status = 401
    elif view.status is BacktestResultStatus.UNAVAILABLE:
        status = 503
    else:  # pragma: no cover - Enum validation makes this unreachable.
        raise BacktestResultError("unknown backtest result status")
    return BacktestResultResponse(status, view.to_public_dict())


__all__ = [
    "BACKTEST_RESULT_CONTRACT_VERSION",
    "BacktestResultError",
    "BacktestResultResponse",
    "BacktestResultStatus",
    "BacktestResultView",
    "serialize_backtest_result",
]
