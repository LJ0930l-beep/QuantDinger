from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import uuid4
import importlib.util
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules():
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_vertical_read_contracts", "app.domain.venue_order_contracts",
        "app.domain.immutable_fill_ledger", "app.domain.gate_testnet_execution_contracts",
        "app.domain.gate_testnet_ledger_contracts", "app.services", "app.services.immutable_fill_ledger_repository",
        "app.services.gate_testnet_ledger_persistence_service",
    )
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        _load(names[2], ROOT / "app" / "domain" / "decimal_values.py")
        multi = _load(names[3], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        vertical = _load(names[4], ROOT / "app" / "domain" / "gate_vertical_read_contracts.py")
        _load(names[5], ROOT / "app" / "domain" / "venue_order_contracts.py")
        ledger = _load(names[6], ROOT / "app" / "domain" / "immutable_fill_ledger.py")
        execution = _load(names[7], ROOT / "app" / "domain" / "gate_testnet_execution_contracts.py")
        bridge = _load(names[8], ROOT / "app" / "domain" / "gate_testnet_ledger_contracts.py")
        repo = _load(names[10], ROOT / "app" / "services" / "immutable_fill_ledger_repository.py")
        service = _load(names[11], ROOT / "app" / "services" / "gate_testnet_ledger_persistence_service.py")
        return multi, vertical, ledger, execution, bridge, repo, service
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


MULTI, VERTICAL, LEDGER, EXECUTION, BRIDGE, REPO, SERVICE = _modules()


class _FakeLedgerRepository:
    def __init__(self): self.calls = []
    def persist_fill_bundle_caller_owned(self, connection, *, scope, fill):
        self.calls.append((connection, scope, fill))
        return REPO.FillLedgerCommitResult("fill", "trade", None, fill.fill_key, REPO.FillLedgerCommitDisposition.APPLIED)


class ServiceTests(unittest.TestCase):
    def test_receipt_persists_through_one_caller_owned_port(self):
        request = EXECUTION.GateTestnetExecutionRequest(
            instrument_id="BTC_USDT", market_type=MULTI.AssetMarketType.SPOT,
            account_scope="paper-gate-testnet", side=VERTICAL.GateOrderSide.BUY,
            quantity=Decimal("0.1"), reference_price=Decimal("65000"),
            client_order_id="case-persist-1",
        )
        receipt = EXECUTION.simulate_gate_testnet_execution(request)
        order_id = str(uuid4())
        ledger_scope = BRIDGE.GateTestnetLedgerScope(order_id, LEDGER.InstrumentAssetScope("BTC_USDT", "BTC", "USDT"), "USDT")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        persistence_scope = REPO.FillLedgerPersistenceScope(
            tenant_id=1, credential_id=2, intent_id=str(uuid4()), economic_order_id=order_id,
            source="REST", exchange_event_at=now, received_at=now,
            normalizer_version="gate-testnet-v1", instrument_rule_version="gate-rules-v1",
        )
        fake = _FakeLedgerRepository(); connection = object()
        result = SERVICE.persist_gate_testnet_receipt_caller_owned(
            connection, receipt, ledger_scope=ledger_scope, persistence_scope=persistence_scope, repository=fake
        )
        self.assertEqual(result.disposition, "APPLIED")
        self.assertEqual(len(fake.calls), 1)
        self.assertIs(fake.calls[0][0], connection)

    def test_scope_mismatch_fails_before_repository_call(self):
        request = EXECUTION.GateTestnetExecutionRequest(
            instrument_id="BTC_USDT", market_type=MULTI.AssetMarketType.SPOT,
            account_scope="paper-gate-testnet", side=VERTICAL.GateOrderSide.BUY,
            quantity=Decimal("0.1"), reference_price=Decimal("65000"),
        )
        receipt = EXECUTION.simulate_gate_testnet_execution(request)
        scope = BRIDGE.GateTestnetLedgerScope(str(uuid4()), LEDGER.InstrumentAssetScope("BTC_USDT", "BTC", "USDT"), "USDT")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        persistence = REPO.FillLedgerPersistenceScope(1, 2, str(uuid4()), str(uuid4()), "REST", now, now, "v1", "v1")
        fake = _FakeLedgerRepository()
        with self.assertRaises(SERVICE.GateTestnetLedgerPersistenceError):
            SERVICE.persist_gate_testnet_receipt_caller_owned(
                object(), receipt, ledger_scope=scope, persistence_scope=persistence, repository=fake
            )
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
