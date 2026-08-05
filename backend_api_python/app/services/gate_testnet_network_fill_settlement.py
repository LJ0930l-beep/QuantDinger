"""Caller-owned settlement of normalized Gate TestNet fills.

Order submission and private fill reads are separate venue facts.  This
module composes them only after a caller supplies the durable economic-order
and asset/valuation scopes, then delegates the complete fill bundle to the
immutable-ledger repository on the same connection.  It never commits,
rolls back, opens a connection, or enables Live trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping
from typing import Any, Protocol

from app.domain.gate_read_formatters import normalize_gate_fills
from app.domain.gate_testnet_ledger_contracts import (
    GateTestnetLedgerScope,
    build_gate_fill_ledger_input,
)
from app.domain.gate_vertical_read_contracts import GateFillFact
from app.domain.decimal_values import Price
from app.domain.immutable_fill_ledger import InstrumentAssetScope
from app.domain.multi_asset_capability_contracts import AssetMarketType
from app.services.gate_testnet_ledger_persistence_service import (
    GateTestnetLedgerPersistenceResult,
)
from app.services.gate_testnet_order_client import GateTestnetOrderReceipt
from app.services.immutable_fill_ledger_repository import (
    FillLedgerPersistenceScope,
    ImmutableFillLedgerRepository,
)


class GateTestnetNetworkSettlementError(RuntimeError):
    """A normalized network fill cannot be safely settled."""


@dataclass(frozen=True, slots=True)
class GateTestnetSettlementScopes:
    """Explicit facts required to attach a network fill to the ledger.

    The venue response does not contain our economic-order, asset, valuation,
    or persistence identity.  Those facts therefore remain caller-owned and
    are required here instead of being inferred from a symbol or current
    configuration.
    """

    observed_at: datetime
    ledger_scope: GateTestnetLedgerScope
    persistence_scope: FillLedgerPersistenceScope


def _payload_decimal(payload: Mapping[str, Any], name: str) -> Decimal | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, (bool, float)):
        raise GateTestnetNetworkSettlementError(f"{name} must use Decimal-compatible text")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateTestnetNetworkSettlementError(f"{name} must be a decimal") from exc
    if not parsed.is_finite():
        raise GateTestnetNetworkSettlementError(f"{name} must be finite")
    return parsed


def _payload_utc(payload: Mapping[str, Any], name: str) -> datetime:
    value = payload.get(name)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GateTestnetNetworkSettlementError(f"{name} must be an ISO timestamp") from exc
    return _strict_utc(value)  # type: ignore[arg-type]


def build_gate_testnet_settlement_scopes(
    payload: Mapping[str, Any],
    *,
    tenant_id: int,
    credential_id: int,
) -> GateTestnetSettlementScopes:
    """Parse explicit settlement facts without reading configuration or time.

    This helper is intentionally pure.  A route may resolve an encrypted
    credential and open a connection around the returned scopes, but it must
    not replace missing facts with ``NOW()``, symbol parsing, or current
    instrument rules.
    """

    if not isinstance(payload, Mapping):
        raise GateTestnetNetworkSettlementError("settlement payload is required")
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id < 1:
        raise GateTestnetNetworkSettlementError("tenant_id is invalid")
    if isinstance(credential_id, bool) or not isinstance(credential_id, int) or credential_id < 1:
        raise GateTestnetNetworkSettlementError("credential_id is invalid")

    market_raw = str(payload.get("market_type") or "").strip().lower()
    market_type = AssetMarketType.SPOT if market_raw == "spot" else AssetMarketType.PERPETUAL if market_raw in {"perpetual", "perp", "swap", "futures", "future"} else None
    if market_type is None:
        raise GateTestnetNetworkSettlementError("market_type must be spot or perpetual")

    def text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or value != value.strip() or not value or not value.isascii() or any(ch.isspace() for ch in value):
            raise GateTestnetNetworkSettlementError(f"{name} must be canonical ASCII text")
        return value

    instrument_id = text("instrument_id")
    account_scope = text("account_scope")
    intent_raw = payload.get("intent_id")
    command_raw = payload.get("durable_entry_command_id")
    if (intent_raw is None) == (command_raw is None):
        raise GateTestnetNetworkSettlementError(
            "exactly one of intent_id or durable_entry_command_id is required"
        )
    intent_id = text("intent_id") if intent_raw is not None else None
    durable_entry_command_id = (
        text("durable_entry_command_id") if command_raw is not None else None
    )
    economic_order_id = text("economic_order_id")
    source = text("source").upper()
    normalizer_version = text("normalizer_version")
    instrument_rule_version = text("instrument_rule_version")
    base_asset = text("base_asset").upper()
    quote_asset = text("quote_asset").upper()
    valuation_ccy = text("valuation_ccy").upper()

    try:
        assets = InstrumentAssetScope(instrument_id, base_asset, quote_asset)
        quote_price_value = _payload_decimal(payload, "quote_valuation_price")
        quote_price = None if quote_price_value is None else Price(quote_price_value)
        fee_prices_raw = payload.get("fee_valuation_prices") or {}
        if not isinstance(fee_prices_raw, Mapping):
            raise GateTestnetNetworkSettlementError("fee_valuation_prices must be an object")
        fee_prices: dict[str, Price] = {}
        for asset, raw_price in fee_prices_raw.items():
            if not isinstance(asset, str) or not asset.strip() or not asset.isascii():
                raise GateTestnetNetworkSettlementError("fee valuation asset is invalid")
            parsed = raw_price if isinstance(raw_price, Decimal) else _payload_decimal({"price": raw_price}, "price")
            if parsed is None:
                raise GateTestnetNetworkSettlementError("fee valuation price is required")
            fee_prices[asset.upper()] = Price(parsed)
        ledger_scope = GateTestnetLedgerScope(
            economic_order_id=economic_order_id,
            assets=assets,
            valuation_ccy=valuation_ccy,
            quote_valuation_price=quote_price,
            fee_valuation_prices=fee_prices,
        )
        persistence_scope = FillLedgerPersistenceScope(
            tenant_id=tenant_id,
            credential_id=credential_id,
            intent_id=intent_id,
            economic_order_id=economic_order_id,
            source=source,
            exchange_event_at=_payload_utc(payload, "exchange_event_at"),
            received_at=_payload_utc(payload, "received_at"),
            normalizer_version=normalizer_version,
            instrument_rule_version=instrument_rule_version,
            durable_entry_command_id=durable_entry_command_id,
        )
        return GateTestnetSettlementScopes(
            observed_at=_payload_utc(payload, "observed_at"),
            ledger_scope=ledger_scope,
            persistence_scope=persistence_scope,
        )
    except GateTestnetNetworkSettlementError:
        raise
    except Exception as exc:
        raise GateTestnetNetworkSettlementError("settlement scope facts are invalid") from exc


class GateFillReadPort(Protocol):
    def read_spot_fills(self, *, currency_pair: str) -> Any: ...
    def read_futures_fills(self, *, contract: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class GateTestnetNetworkSettlementResult:
    order: GateTestnetOrderReceipt
    fills: tuple[GateFillFact, ...]
    ledger: GateTestnetLedgerPersistenceResult

    @property
    def disposition(self) -> str:
        return self.ledger.disposition


def _strict_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateTestnetNetworkSettlementError("observed_at must be UTC")
    return value.astimezone(timezone.utc)


def _persist_fills(
    connection: object,
    receipt: GateTestnetOrderReceipt,
    fills: tuple[GateFillFact, ...],
    *,
    ledger_scope: GateTestnetLedgerScope,
    persistence_scope: FillLedgerPersistenceScope,
    repository: object | None,
) -> GateTestnetLedgerPersistenceResult:
    if persistence_scope.economic_order_id != ledger_scope.economic_order_id:
        raise GateTestnetNetworkSettlementError("ledger and persistence order scope mismatch")
    repo = repository or ImmutableFillLedgerRepository()
    results = []
    for fill in fills:
        if (
            fill.market_type is not receipt.market_type
            or fill.account_scope != receipt.account_scope
            or fill.instrument_id != receipt.instrument_id
            or fill.exchange_order_id != receipt.exchange_order_id
        ):
            raise GateTestnetNetworkSettlementError("network fill scope conflicts with order receipt")
        try:
            input_fact = build_gate_fill_ledger_input(
                fill,
                economic_order_id=ledger_scope.economic_order_id,
                scope=ledger_scope,
            )
            results.append(repo.persist_fill_bundle_caller_owned(
                connection,
                scope=persistence_scope,
                fill=input_fact,
            ))
        except GateTestnetNetworkSettlementError:
            raise
        except Exception as exc:
            raise GateTestnetNetworkSettlementError("Gate TestNet fill persistence failed") from exc
    return GateTestnetLedgerPersistenceResult(receipt.response_fingerprint, tuple(results), False)


def settle_gate_testnet_order_fills_caller_owned(
    connection: object,
    receipt: GateTestnetOrderReceipt,
    *,
    ledger_scope: GateTestnetLedgerScope,
    persistence_scope: FillLedgerPersistenceScope,
    repository: object | None = None,
) -> GateTestnetNetworkSettlementResult:
    """Persist already-normalized network fill facts without transaction control."""

    if not isinstance(receipt, GateTestnetOrderReceipt):
        raise GateTestnetNetworkSettlementError("typed Gate TestNet order receipt is required")
    if not isinstance(ledger_scope, GateTestnetLedgerScope) or not isinstance(persistence_scope, FillLedgerPersistenceScope):
        raise GateTestnetNetworkSettlementError("typed ledger scopes are required")
    fills = tuple(receipt.fills)
    ledger = _persist_fills(
        connection,
        receipt,
        fills,
        ledger_scope=ledger_scope,
        persistence_scope=persistence_scope,
        repository=repository,
    )
    return GateTestnetNetworkSettlementResult(receipt, fills, ledger)


def read_and_settle_gate_testnet_order_fills_caller_owned(
    connection: object,
    receipt: GateTestnetOrderReceipt,
    *,
    read_client: GateFillReadPort,
    observed_at: datetime,
    ledger_scope: GateTestnetLedgerScope,
    persistence_scope: FillLedgerPersistenceScope,
    repository: object | None = None,
) -> GateTestnetNetworkSettlementResult:
    """Read Gate fills by market, normalize them, and settle matching order fills."""

    if not isinstance(receipt, GateTestnetOrderReceipt):
        raise GateTestnetNetworkSettlementError("typed Gate TestNet order receipt is required")
    # Protocols are not runtime-checkable; use a structural check so an
    # untyped client fails before any network call.
    if not all(callable(getattr(read_client, name, None)) for name in ("read_spot_fills", "read_futures_fills")):
        raise GateTestnetNetworkSettlementError("typed Gate fill read client is required")
    seen_at = _strict_utc(observed_at)
    payload = (
        read_client.read_spot_fills(currency_pair=receipt.instrument_id)
        if receipt.market_type is AssetMarketType.SPOT
        else read_client.read_futures_fills(contract=receipt.instrument_id)
    )
    try:
        normalized = normalize_gate_fills(
            payload,
            market_type=receipt.market_type,
            account_scope=receipt.account_scope,
            observed_at=seen_at,
            source_event_prefix=f"gate:{receipt.exchange_order_id}",
        )
    except Exception as exc:
        raise GateTestnetNetworkSettlementError("Gate fill payload is invalid") from exc
    matching = tuple(fill for fill in normalized if fill.exchange_order_id == receipt.exchange_order_id)
    try:
        enriched = GateTestnetOrderReceipt(
            receipt.market_type,
            receipt.account_scope,
            receipt.instrument_id,
            receipt.client_order_id,
            receipt.exchange_order_id,
            receipt.raw_state,
            receipt.status_code,
            receipt.response_fingerprint,
            fills=matching,
            fee_amount=receipt.fee_amount,
            execution_receipt=receipt.execution_receipt,
            network_access=receipt.network_access,
            writes_enabled=receipt.writes_enabled,
            live_enabled=receipt.live_enabled,
        )
    except Exception as exc:
        raise GateTestnetNetworkSettlementError("normalized fill scope conflicts with order receipt") from exc
    return settle_gate_testnet_order_fills_caller_owned(
        connection,
        enriched,
        ledger_scope=ledger_scope,
        persistence_scope=persistence_scope,
        repository=repository,
    )


__all__ = [
    "GateFillReadPort",
    "GateTestnetNetworkSettlementError",
    "GateTestnetNetworkSettlementResult",
    "GateTestnetSettlementScopes",
    "build_gate_testnet_settlement_scopes",
    "read_and_settle_gate_testnet_order_fills_caller_owned",
    "settle_gate_testnet_order_fills_caller_owned",
]
