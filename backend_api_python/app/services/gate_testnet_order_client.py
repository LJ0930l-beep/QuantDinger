"""Explicit Gate TestNet order boundary.

This adapter is the only network-capable order surface introduced by the
product integration work.  It accepts an injected transport, requires a
typed TestNet credential, and never accepts LIVE.  The explicit TestNet
worker may call it only after admission, risk, durable Submission Attempt,
and caller-owned exchange-order persistence have been prepared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode

from app.domain.gate_testnet_execution_contracts import (
    GateExecutionKind,
    GateTestnetExecutionReceipt,
    GateTestnetExecutionRequest,
)
from app.domain.gate_vertical_read_contracts import GateFillFact, GateOrderSide
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.services.gate_private_read_client import GatePrivateCredential


class GateTestnetOrderError(RuntimeError):
    """Base typed failure for the Gate TestNet order boundary."""


class GateTestnetOrderAuthError(GateTestnetOrderError):
    pass


class GateTestnetOrderTemporaryError(GateTestnetOrderError):
    pass


class GateTestnetOrderInvalidResponse(GateTestnetOrderError):
    pass


class GateTestnetOrderCapabilityError(GateTestnetOrderError):
    pass


class GateTestnetOrderTransport(Protocol):
    def request(self, method: str, path: str, query: str, body: str, headers: Mapping[str, str]) -> tuple[int, Any]: ...


class DisabledGateTestnetOrderTransport:
    def request(self, method: str, path: str, query: str, body: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        raise GateTestnetOrderCapabilityError("Gate TestNet order transport is disabled")


@dataclass(frozen=True, slots=True, repr=False)
class GateTestnetOrderReceipt:
    market_type: AssetMarketType
    account_scope: str
    instrument_id: str
    client_order_id: str
    exchange_order_id: str
    raw_state: str
    status_code: int
    response_fingerprint: str
    # A submit response normally has no fill evidence.  A caller may attach
    # already-normalized fill facts after a separate read/query step (or a
    # deterministic fixture) so the execution seam can hand one complete
    # receipt to the immutable-ledger bridge without inventing a second
    # receipt type.  These fields are facts, never a permission to submit.
    fills: tuple[GateFillFact, ...] = ()
    fee_amount: Decimal = Decimal("0")
    execution_receipt: GateTestnetExecutionReceipt | None = None
    network_access: bool = True
    writes_enabled: bool = True
    live_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateTestnetOrderError("receipt market_type is invalid")
        if not all(isinstance(value, str) and value.strip() == value and value for value in (self.account_scope, self.instrument_id, self.client_order_id, self.exchange_order_id, self.raw_state)):
            raise GateTestnetOrderError("receipt identity is incomplete")
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int) or not 200 <= self.status_code <= 299:
            raise GateTestnetOrderError("receipt status_code is invalid")
        if not isinstance(self.network_access, bool) or not isinstance(self.writes_enabled, bool) or not isinstance(self.live_enabled, bool):
            raise GateTestnetOrderError("TestNet receipt flags are invalid")
        if not self.network_access or self.live_enabled:
            raise GateTestnetOrderError("TestNet receipt flags are inconsistent")
        if not isinstance(self.fills, tuple) or any(not isinstance(item, GateFillFact) for item in self.fills):
            raise GateTestnetOrderError("receipt fills must use typed Gate fill facts")
        if self.execution_receipt is not None and not isinstance(self.execution_receipt, GateTestnetExecutionReceipt):
            raise GateTestnetOrderError("execution_receipt must use the typed TestNet lifecycle contract")
        try:
            fee = self.fee_amount if isinstance(self.fee_amount, Decimal) else Decimal(str(self.fee_amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise GateTestnetOrderError("receipt fee_amount must be Decimal-compatible") from exc
        if not fee.is_finite() or fee < 0:
            raise GateTestnetOrderError("receipt fee_amount must be finite and non-negative")
        object.__setattr__(self, "fee_amount", fee)
        for fill in self.fills:
            if (
                fill.market_type is not self.market_type
                or fill.account_scope != self.account_scope
                or fill.instrument_id != self.instrument_id
                or fill.exchange_order_id != self.exchange_order_id
            ):
                raise GateTestnetOrderError("receipt fill scope conflicts with order scope")


ClientOrderIdValidator = Callable[[str], str]


def _decimal_text(value: Decimal) -> str:
    if isinstance(value, float) or not isinstance(value, Decimal) or not value.is_finite():
        raise GateTestnetOrderError("Gate TestNet quantities and prices must be finite Decimal")
    return format(value.normalize(), "f")


def _json_body(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _validate_gate_client_order_id(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value.isascii() or not value.startswith("t-"):
        raise GateTestnetOrderCapabilityError("Gate client order ID must use the t- prefix")
    content = value[2:]
    if not 1 <= len(content.encode("ascii")) <= 28 or any(ch not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-." for ch in content):
        raise GateTestnetOrderCapabilityError("Gate client order ID is outside the venue contract")
    return value


@dataclass(slots=True)
class GateTestnetOrderClient:
    credential: GatePrivateCredential
    transport: GateTestnetOrderTransport
    timestamp_provider: Callable[[], int]
    market_type: AssetMarketType
    account_scope: str
    client_order_id_validator: ClientOrderIdValidator
    settle: str = "usdt"

    def __post_init__(self) -> None:
        if not isinstance(self.credential, GatePrivateCredential) or self.credential.environment is not CapabilityEnvironment.TESTNET:
            raise GateTestnetOrderCapabilityError("Gate order client requires a TestNet credential")
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateTestnetOrderCapabilityError("Gate order market_type must be spot or perpetual")
        if not isinstance(self.account_scope, str) or not self.account_scope or self.account_scope != self.account_scope.strip() or not self.account_scope.isascii():
            raise GateTestnetOrderCapabilityError("Gate order account_scope must be canonical ASCII text")
        if not callable(self.timestamp_provider) or not callable(self.client_order_id_validator):
            raise GateTestnetOrderCapabilityError("typed timestamp and client ID validators are required")
        if self.settle not in {"usdt", "btc"}:
            raise GateTestnetOrderCapabilityError("unsupported Gate settlement currency")

    @classmethod
    def disabled(cls, credential: GatePrivateCredential, *, market_type: AssetMarketType, account_scope: str, timestamp_provider=None, client_order_id_validator=None):
        return cls(
            credential=credential,
            transport=DisabledGateTestnetOrderTransport(),
            timestamp_provider=timestamp_provider or (lambda: 0),
            market_type=market_type,
            account_scope=account_scope,
            client_order_id_validator=client_order_id_validator or (lambda value: value),
        )

    def submit(self, request: GateTestnetExecutionRequest) -> GateTestnetOrderReceipt:
        if not isinstance(request, GateTestnetExecutionRequest) or request.environment is not CapabilityEnvironment.TESTNET:
            raise GateTestnetOrderCapabilityError("typed TestNet execution request is required")
        if request.execution_kind in (GateExecutionKind.STOP_MARKET, GateExecutionKind.STOP_LIMIT):
            # Gate price-triggered orders use a separate ``price_orders``
            # endpoint with a different request/response contract.  Never
            # silently encode a STOP request as an ordinary LIMIT order.
            raise GateTestnetOrderCapabilityError(
                "price-triggered TestNet submission requires the dedicated trigger worker"
            )
        if request.market_type is not self.market_type:
            raise GateTestnetOrderCapabilityError("Gate order market scope mismatch")
        if request.account_scope != self.account_scope:
            raise GateTestnetOrderCapabilityError("Gate order account scope mismatch")
        try:
            client_order_id = self.client_order_id_validator(request.client_order_id)
        except GateTestnetOrderError:
            raise
        except Exception as exc:
            raise GateTestnetOrderCapabilityError("client order ID failed venue validation") from exc
        if not isinstance(client_order_id, str) or not client_order_id or not client_order_id.isascii() or client_order_id != client_order_id.strip():
            raise GateTestnetOrderCapabilityError("client order ID validator returned invalid text")
        if request is not None:
            _validate_gate_client_order_id(client_order_id)
        if request.market_type is AssetMarketType.SPOT:
            path = "/api/v4/spot/orders"
            payload: dict[str, Any] = {
                "currency_pair": request.instrument_id,
                "type": "market" if request.execution_kind is GateExecutionKind.MARKET else "limit",
                "account": "spot",
                "side": request.side.value,
                # Gate Spot interprets a market-buy ``amount`` as quote
                # currency, while market-sell and all limit orders use base
                # quantity.  The typed request quantity remains the desired
                # base amount; use its deterministic reference notional for
                # the venue's buy payload and never silently change the
                # request's domain quantity.
                "amount": _decimal_text(
                    request.quantity * request.reference_price
                    if request.execution_kind is GateExecutionKind.MARKET and request.side is GateOrderSide.BUY
                    else request.quantity
                ),
                "text": client_order_id,
            }
            if request.execution_kind is GateExecutionKind.LIMIT:
                payload.update({"price": _decimal_text(request.limit_price), "time_in_force": "gtc"})
            else:
                # Gate Spot market orders must be IOC; the venue rejects the
                # default GTC interpretation for market order payloads.
                payload["time_in_force"] = "ioc"
        else:
            path = f"/api/v4/futures/{self.settle}/orders"
            payload = {
                "contract": request.instrument_id,
                "size": _decimal_text(request.quantity),
                "price": "0" if request.execution_kind is GateExecutionKind.MARKET else _decimal_text(request.limit_price),
                "tif": "ioc" if request.execution_kind is GateExecutionKind.MARKET else "gtc",
                "reduce_only": request.reduce_only,
                "text": client_order_id,
            }
            if request.side is GateOrderSide.SELL:
                payload["size"] = "-" + payload["size"]
        return self._request("POST", path, payload, client_order_id=client_order_id, request=request)

    def cancel(self, *, instrument_id: str, exchange_order_id: str) -> GateTestnetOrderReceipt:
        if not isinstance(instrument_id, str) or not instrument_id.strip() or not isinstance(exchange_order_id, str) or not exchange_order_id.strip():
            raise GateTestnetOrderError("cancel requires instrument and exchange order identity")
        if self.market_type is AssetMarketType.SPOT:
            path = f"/api/v4/spot/orders/{exchange_order_id}"
            query = urlencode({"currency_pair": instrument_id})
        else:
            path = f"/api/v4/futures/{self.settle}/orders/{exchange_order_id}"
            query = ""
        return self._request("DELETE", path, {}, query=query, client_order_id="cancel", instrument_id=instrument_id)

    def cancel_and_confirm(
        self,
        *,
        instrument_id: str,
        exchange_order_id: str,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] | None = None,
    ) -> GateTestnetOrderReceipt:
        """Cancel once and query until the venue reports a terminal state.

        A DELETE acknowledgement is not sufficient evidence that the order
        is no longer open.  This helper never resubmits or creates a
        replacement order while confirmation is unknown.
        """
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise GateTestnetOrderError("max_attempts must be between 1 and 10")
        cancel_receipt = self.cancel(instrument_id=instrument_id, exchange_order_id=exchange_order_id)
        wait = sleeper or (lambda _seconds: None)
        terminal = {"cancelled", "canceled", "closed", "finished", "expired", "rejected"}
        last = cancel_receipt
        for attempt in range(max_attempts):
            try:
                confirmed = self.query(instrument_id=instrument_id, exchange_order_id=exchange_order_id)
            except GateTestnetOrderError as exc:
                if attempt == max_attempts - 1:
                    raise GateTestnetOrderTemporaryError("Gate TestNet cancel confirmation unavailable") from exc
                wait(0.2)
                continue
            if str(confirmed.raw_state).lower() in terminal:
                return confirmed
            last = confirmed
            if attempt < max_attempts - 1:
                wait(0.2)
        raise GateTestnetOrderTemporaryError("Gate TestNet cancel did not reach a confirmed terminal state")

    def query(self, *, instrument_id: str, exchange_order_id: str) -> GateTestnetOrderReceipt:
        if not isinstance(instrument_id, str) or not instrument_id.strip() or not isinstance(exchange_order_id, str) or not exchange_order_id.strip():
            raise GateTestnetOrderError("query requires instrument and exchange order identity")
        if self.market_type is AssetMarketType.SPOT:
            path = f"/api/v4/spot/orders/{exchange_order_id}"
            query = urlencode({"currency_pair": instrument_id})
        else:
            path = f"/api/v4/futures/{self.settle}/orders/{exchange_order_id}"
            query = ""
        return self._request("GET", path, {}, query=query, client_order_id="query", instrument_id=instrument_id)

    def _request(self, method: str, path: str, payload: Mapping[str, Any], *, query: str = "", client_order_id: str, request: GateTestnetExecutionRequest | None = None, instrument_id: str | None = None) -> GateTestnetOrderReceipt:
        body = _json_body(payload) if payload else ""
        timestamp = str(int(self.timestamp_provider()))
        body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
        material = "\n".join((method.upper(), path, query, body_hash, timestamp))
        signature = hmac.new(self.credential.api_secret.encode("ascii"), material.encode("utf-8"), hashlib.sha512).hexdigest()
        headers = {"KEY": self.credential.api_key, "SIGN": signature, "Timestamp": timestamp, "Content-Type": "application/json"}
        try:
            status, payload_out = self.transport.request(method.upper(), path, query, body, headers)
        except GateTestnetOrderError:
            raise
        except Exception as exc:
            raise GateTestnetOrderTemporaryError("Gate TestNet transport failed") from exc
        if status in (401, 403):
            raise GateTestnetOrderAuthError("Gate TestNet authorization failed")
        if status == 429 or status >= 500:
            raise GateTestnetOrderTemporaryError("Gate TestNet is temporarily unavailable")
        if status < 200 or status >= 300 or not isinstance(payload_out, Mapping):
            raise GateTestnetOrderInvalidResponse("Gate TestNet response is invalid")
        order_id = str(payload_out.get("id") or payload_out.get("order_id") or "").strip()
        if not order_id:
            raise GateTestnetOrderInvalidResponse("Gate TestNet response omitted order identity")
        returned_client_id = str(payload_out.get("text") or payload_out.get("client_order_id") or client_order_id).strip()
        if request is not None and returned_client_id != client_order_id:
            raise GateTestnetOrderInvalidResponse("Gate TestNet response client order ID conflicts")
        raw_status = str(payload_out.get("status") or payload_out.get("state") or "UNKNOWN").strip().lower()
        finish_reason = str(payload_out.get("finish_as") or payload_out.get("finish_reason") or "").strip().lower()
        if raw_status in {"finished", "closed"}:
            if finish_reason in {"filled", "succeeded"}:
                raw_state = "filled"
            elif finish_reason in {
                "cancelled", "canceled", "liquidated", "liquidate_cancelled", "ioc", "poc", "fok", "stp",
                "small", "depth_not_enough", "trader_not_enough", "reduce_only", "position_closed", "reduce_out",
                "auto_deleveraged",
            }:
                raw_state = "cancelled"
            else:
                # Preserve the raw terminal state when the venue omitted its
                # finish reason; callers must not infer FILLED from it.
                raw_state = raw_status
        else:
            raw_state = raw_status
        fingerprint = hashlib.sha256(_json_body({"method": method.upper(), "path": path, "status": status, "payload": dict(payload_out)}).encode("utf-8")).hexdigest()
        resolved_instrument = instrument_id or (request.instrument_id if request is not None else "unknown")
        # The transport owns the write capability.  A GET-only transport must
        # not produce a receipt that claims a write-enabled boundary, even
        # though it still has network access to read the order state.
        writes_enabled = bool(getattr(self.transport, "allow_testnet_writes", True))
        return GateTestnetOrderReceipt(
            self.market_type,
            self.account_scope,
            resolved_instrument,
            returned_client_id,
            order_id,
            raw_state,
            status,
            fingerprint,
            writes_enabled=writes_enabled,
        )


__all__ = [
    "DisabledGateTestnetOrderTransport", "GateTestnetOrderAuthError", "GateTestnetOrderCapabilityError",
    "GateTestnetOrderClient", "GateTestnetOrderError", "GateTestnetOrderInvalidResponse",
    "GateTestnetOrderReceipt", "GateTestnetOrderTemporaryError", "GateTestnetOrderTransport",
]
