"""Focused tests for the authenticated, admission-only HTTP composition."""

from __future__ import annotations

import unittest
from datetime import timezone
from unittest.mock import patch

from app.domain.canonical_entry_contracts import EntryMode, EntrySource
from app.domain.runtime_entry_admission_contracts import (
    RuntimeEntryAdmissionDisposition,
    RuntimeEntryAdmissionResult,
)
from app.domain.runtime_entry_ingress_contracts import RuntimeIngressPrincipal
from app.services.runtime_entry_admission_http_service import (
    RuntimeEntryAdmissionApiError,
    admit_runtime_entry_payload,
    build_runtime_ingress,
    result_to_public_dict,
)


class RuntimeEntryAdmissionHttpServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.principal = RuntimeIngressPrincipal(1, "1", EntrySource.REST)
        self.body = {
            "source": "REST",
            "mode": "PAPER",
            "credential_id": 2,
            "instrument_id": "BTC_USDT",
            "market_type": "perpetual",
            "action": "OPEN",
            "side": "BUY",
            "quantity": "1",
            "quantity_semantics": "ABSOLUTE",
            "execution_kind": "MARKET",
            "position_side": "NET",
            "reduce_only": False,
            "close_all": False,
            "idempotency_key": "http-admission-1",
            "correlation_id": "http-correlation-1",
            "occurred_at": "2026-08-02T00:00:00+00:00",
        }

    def test_builds_explicit_typed_paper_ingress(self):
        ingress, mode, correlation, occurred_at = build_runtime_ingress(self.body, principal=self.principal)
        self.assertEqual(mode, EntryMode.PAPER)
        self.assertEqual(correlation, "http-correlation-1")
        self.assertEqual(ingress.instrument_id, "BTC_USDT")
        self.assertEqual(occurred_at.tzinfo, timezone.utc)

    def test_rejects_live_mode_and_restricted_source(self):
        with self.assertRaises(RuntimeEntryAdmissionApiError):
            build_runtime_ingress({**self.body, "mode": "LIVE"}, principal=self.principal)
        with self.assertRaises(RuntimeEntryAdmissionApiError):
            build_runtime_ingress({**self.body, "source": "AGENT"}, principal=self.principal)

    def test_requires_explicit_audit_facts(self):
        with self.assertRaises(RuntimeEntryAdmissionApiError):
            build_runtime_ingress({key: value for key, value in self.body.items() if key != "correlation_id"}, principal=self.principal)
        with self.assertRaises(RuntimeEntryAdmissionApiError):
            build_runtime_ingress({key: value for key, value in self.body.items() if key != "occurred_at"}, principal=self.principal)

    def test_disabled_result_is_readable_and_live_off(self):
        result = RuntimeEntryAdmissionResult(RuntimeEntryAdmissionDisposition.DISABLED, None, None)
        self.assertEqual(
            result_to_public_dict(result),
            {
                "status": "DISABLED",
                "mode": "DISABLED",
                "live_enabled": False,
                "network_access": False,
                "writes_enabled": False,
            },
        )

    def test_disabled_http_admission_does_not_acquire_database_connection(self):
        body = {**self.body, "mode": "DISABLED"}
        with patch("app.services.runtime_entry_admission_http_service.get_db_connection", side_effect=AssertionError("disabled must not open DB")):
            result = admit_runtime_entry_payload(body, tenant_id=1, actor_id="1")
        self.assertIs(result.disposition, RuntimeEntryAdmissionDisposition.DISABLED)


if __name__ == "__main__":
    unittest.main()
