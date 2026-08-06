"""Runtime-safe read-only quant state service.

This service is the seam between the validated G4-B chain contracts and an
HTTP read surface.  It deliberately accepts an injected receipt provider;
there is no database connection, exchange client, credential lookup,
background worker, or write operation in this module.  Deployments can wire a
projection/reconciliation provider later without changing the API contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.g4b_readonly_contracts import G4BReadonlyChainReceipt
from app.domain.readonly_quant_api_contracts import (
    ReadonlyQuantApiResponse,
    serialize_readonly_quant_state,
)
from app.domain.readonly_quant_state_contracts import (
    ReadonlyQuantStateView,
    ReadonlyViewStatus,
    build_readonly_quant_state_view,
)


class ReadonlyQuantStateServiceError(RuntimeError):
    """A provider cannot supply a typed, safe read-only state."""


ReceiptProvider = Callable[[], Optional[G4BReadonlyChainReceipt]]


@dataclass(frozen=True, slots=True)
class ReadonlyQuantStateService:
    """Build a credential-free response from an injected receipt provider."""

    receipt_provider: Optional[ReceiptProvider] = None

    def __post_init__(self) -> None:
        if self.receipt_provider is not None and not callable(self.receipt_provider):
            raise ReadonlyQuantStateServiceError("receipt_provider must be callable")

    def read_view(self, *, authorized: bool = True) -> ReadonlyQuantStateView:
        if not isinstance(authorized, bool):
            raise ReadonlyQuantStateServiceError("authorized must be boolean")
        if not authorized:
            return ReadonlyQuantStateView(ReadonlyViewStatus.UNAUTHORIZED)
        if self.receipt_provider is None:
            return ReadonlyQuantStateView(ReadonlyViewStatus.UNAVAILABLE)
        try:
            receipt = self.receipt_provider()
        except Exception as exc:
            raise ReadonlyQuantStateServiceError("read-only state provider failed") from exc
        if receipt is None:
            return ReadonlyQuantStateView(ReadonlyViewStatus.UNAVAILABLE)
        try:
            return build_readonly_quant_state_view(receipt, authorized=True)
        except Exception as exc:
            raise ReadonlyQuantStateServiceError("provider returned invalid read-only facts") from exc

    def read_response(self, *, authorized: bool = True) -> ReadonlyQuantApiResponse:
        """Return the stable API envelope; never exposes provider exceptions."""

        return serialize_readonly_quant_state(self.read_view(authorized=authorized))


def service_from_app(app) -> ReadonlyQuantStateService:
    """Resolve an explicitly injected provider from Flask extensions.

    Missing providers are intentionally represented as ``UNAVAILABLE`` rather
    than guessed market/account data.  The extension value is a callable only;
    credentials and transport objects never cross this boundary.
    """

    provider = app.extensions.get("readonly_quant_receipt_provider")
    if provider is not None and not callable(provider):
        raise ReadonlyQuantStateServiceError("readonly provider extension must be callable")
    return ReadonlyQuantStateService(provider)


__all__ = [
    "ReceiptProvider",
    "ReadonlyQuantStateService",
    "ReadonlyQuantStateServiceError",
    "service_from_app",
]
