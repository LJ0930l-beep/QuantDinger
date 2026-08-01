"""HTTP-boundary contracts for the read-only quant state view.

This module is deliberately an adapter contract, not a Flask route. It maps
the already validated ``ReadonlyQuantStateView`` to a stable status/body pair
without opening a connection, reading credentials, or inferring facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.domain.readonly_quant_state_contracts import (
    ReadonlyQuantStateContractError,
    ReadonlyQuantStateView,
    ReadonlyViewStatus,
)


READONLY_QUANT_API_CONTRACT_VERSION = "readonly-quant-api-v1"


class ReadonlyQuantApiContractError(ReadonlyQuantStateContractError):
    """The read-only HTTP response boundary received invalid facts."""


@dataclass(frozen=True, slots=True)
class ReadonlyQuantApiResponse:
    """Credential-free response envelope for a future HTTP adapter."""

    http_status: int
    body: Mapping[str, Any]
    contract_version: str = READONLY_QUANT_API_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.http_status, bool) or not isinstance(self.http_status, int) or not 100 <= self.http_status <= 599:
            raise ReadonlyQuantApiContractError("http_status must be a valid integer status")
        if not isinstance(self.body, Mapping):
            raise ReadonlyQuantApiContractError("body must be a mapping")
        if not isinstance(self.contract_version, str) or self.contract_version != READONLY_QUANT_API_CONTRACT_VERSION:
            raise ReadonlyQuantApiContractError("contract_version is not canonical")


def serialize_readonly_quant_state(view: ReadonlyQuantStateView) -> ReadonlyQuantApiResponse:
    """Serialize a typed view with fail-closed status semantics."""

    if not isinstance(view, ReadonlyQuantStateView):
        raise ReadonlyQuantApiContractError("view must be a typed read-only state")
    public = dict(view.to_public_dict())
    public["api_contract_version"] = READONLY_QUANT_API_CONTRACT_VERSION
    if view.status is ReadonlyViewStatus.UNAUTHORIZED:
        return ReadonlyQuantApiResponse(401, public)
    if view.status is ReadonlyViewStatus.UNAVAILABLE:
        return ReadonlyQuantApiResponse(503, public)
    if view.status in {ReadonlyViewStatus.READY, ReadonlyViewStatus.STALE}:
        return ReadonlyQuantApiResponse(200, public)
    raise ReadonlyQuantApiContractError("unknown read-only view status")


__all__ = [
    "READONLY_QUANT_API_CONTRACT_VERSION",
    "ReadonlyQuantApiContractError",
    "ReadonlyQuantApiResponse",
    "serialize_readonly_quant_state",
]
