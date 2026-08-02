"""Credential-free strategy catalog facts for the research product."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any

from .strategy_library_contracts import StrategyDefinition, strategy_fingerprint


STRATEGY_CATALOG_CONTRACT_VERSION = "strategy-catalog-v1"


class StrategyCatalogError(ValueError):
    """Invalid or unsafe strategy catalog facts."""


class StrategyCatalogStatus(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class StrategyCatalogView:
    status: StrategyCatalogStatus
    strategies: tuple[StrategyDefinition, ...] = ()
    catalog_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, StrategyCatalogStatus):
            raise StrategyCatalogError("status must be typed")
        if not isinstance(self.strategies, tuple) or any(not isinstance(item, StrategyDefinition) for item in self.strategies):
            raise StrategyCatalogError("strategies must be typed")
        if len({item.strategy_id for item in self.strategies}) != len(self.strategies):
            raise StrategyCatalogError("strategy ids must be unique")
        if self.status is StrategyCatalogStatus.READY and not self.strategies:
            raise StrategyCatalogError("READY catalog requires strategies")
        if self.status is StrategyCatalogStatus.UNAVAILABLE and self.strategies:
            raise StrategyCatalogError("UNAVAILABLE catalog cannot expose strategies")
        material = {
            "version": STRATEGY_CATALOG_CONTRACT_VERSION,
            "status": self.status.value,
            "strategies": [strategy_fingerprint(item) for item in self.strategies],
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "catalog_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "contract_version": STRATEGY_CATALOG_CONTRACT_VERSION,
            "status": self.status.value,
            "catalog_fingerprint": self.catalog_fingerprint,
            "strategies": [
                {
                    "strategy_id": item.strategy_id,
                    "version": item.version,
                    "family": item.family.value,
                    "parameter_schema_fingerprint": item.parameter_schema_fingerprint,
                    "data_dependency_snapshot": item.data_dependency_snapshot,
                    "parameter_names": [parameter.name for parameter in item.parameters],
                }
                for item in self.strategies
            ],
        }


__all__ = ["STRATEGY_CATALOG_CONTRACT_VERSION", "StrategyCatalogError", "StrategyCatalogStatus", "StrategyCatalogView"]
