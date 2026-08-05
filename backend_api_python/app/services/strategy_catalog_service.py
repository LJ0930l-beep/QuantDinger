"""Read-only strategy catalog adapter for the quant dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.strategy_catalog_contracts import StrategyCatalogStatus, StrategyCatalogView
from app.domain.strategy_library_contracts import StrategyDefinition


class StrategyCatalogServiceError(RuntimeError):
    """Injected catalog provider is unavailable or invalid."""


CatalogProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class StrategyCatalogService:
    provider: Optional[CatalogProvider] = None

    def __post_init__(self) -> None:
        if self.provider is not None and not callable(self.provider):
            raise StrategyCatalogServiceError("catalog provider must be callable")

    def read_view(self, *, authorized: bool = True) -> StrategyCatalogView:
        if not isinstance(authorized, bool):
            raise StrategyCatalogServiceError("authorized must be boolean")
        if not authorized or self.provider is None:
            return StrategyCatalogView(StrategyCatalogStatus.UNAVAILABLE)
        try:
            values = self.provider()
            if not isinstance(values, tuple) or any(not isinstance(item, StrategyDefinition) for item in values):
                raise StrategyCatalogServiceError("provider returned invalid strategy facts")
            return StrategyCatalogView(StrategyCatalogStatus.READY, values)
        except StrategyCatalogServiceError:
            raise
        except Exception as exc:
            raise StrategyCatalogServiceError("strategy catalog provider failed") from exc

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not authorized:
            return 401, {"contract_version": "strategy-catalog-v1", "status": "UNAVAILABLE", "strategies": []}
        view = self.read_view(authorized=authorized)
        return (200 if view.status is StrategyCatalogStatus.READY else 503), view.to_public_dict()


def service_from_app(app) -> StrategyCatalogService:
    provider = app.extensions.get("readonly_strategy_catalog_provider")
    return StrategyCatalogService(provider)


__all__ = ["StrategyCatalogService", "StrategyCatalogServiceError", "service_from_app"]
