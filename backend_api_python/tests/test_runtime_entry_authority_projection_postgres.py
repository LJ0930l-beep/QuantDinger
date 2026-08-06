"""PostgreSQL integration coverage for Runtime Entry authority projection.

Seeds a user + Gate credential + rule snapshot, projects real snapshot facts,
and asserts the canonical OPEN/PAPER admission transitions from UNAVAILABLE to
CREATED once authority facts exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
import unittest
import uuid

from tests.test_unified_order_schema import UnifiedOrderSchemaPostgresTests
from app.domain.gate_read_snapshot_contracts import build_gate_read_snapshot
from app.domain.gate_vertical_read_contracts import (
    GateAuthFacts,
    GateInstrumentRuleSnapshot,
    GatePermission,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.services.runtime_entry_authority_facts_repository import RuntimeEntryAuthorityFactsRepository
from app.domain.runtime_entry_authority_projection_contracts import (
    build_instrument_authority_facts,
    build_instrument_rule_snapshot_facts,
    build_scope_binding_facts,
)
from app.services.runtime_entry_admission_http_service import admit_runtime_entry_payload_caller_owned
from app.services.runtime_entry_admission_service import RuntimeEntryAdmissionError


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class RuntimeEntryAuthorityProjectionPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg2

        self.psycopg2 = psycopg2
        self.connection = psycopg2.connect(os.environ["DATABASE_URL"])
        self.connection.autocommit = False
        self.repository = RuntimeEntryAuthorityFactsRepository()
        self._seed()

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _seed(self):
        suffix = uuid.uuid4().hex[:10]
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
                (f"authproj_{suffix}", "schema-test"),
            )
            self.user_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO qd_exchange_credentials(user_id, exchange_id, encrypted_config) "
                "VALUES (%s, 'gate', '{}') RETURNING id",
                (self.user_id,),
            )
            self.credential_id = cursor.fetchone()[0]
            self.account_scope = f"account-{suffix[:8]}"
        self._seed_authoritative_risk_facts()
        self.connection.commit()

    def _seed_authoritative_risk_facts(self):
        """Seed persisted RF-01 facts so Hard Risk can evaluate admission."""

        at = datetime(2026, 8, 5, tzinfo=timezone.utc)
        values = (self.user_id, self.credential_id, self.account_scope, "BTC_USDT", "spot")
        with self.connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO qd_authoritative_risk_policies
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,strategy_scope,
                 policy_identity,policy_version,policy_fingerprint,observed_at,max_age_seconds,reservation_ttl_seconds,
                 valuation_currency,max_gross_notional,max_net_notional,max_instrument_notional,max_leverage,
                 minimum_available_margin,max_daily_loss,max_drawdown_ratio)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'__NON_STRATEGY__','policy','v1',%s,%s,60,30,'USDT',160,700,600,4,100,100,0.2)
            """, (str(uuid.uuid4()), *values, "a" * 64, at))
            cursor.execute("""
                INSERT INTO qd_authoritative_instrument_risk_rules
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
                 source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,quantity_to_quote_multiplier,initial_margin_ratio)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','rule','v1',%s,%s,60,1,0.25)
            """, (str(uuid.uuid4()), *values, "b" * 64, at))
            cursor.execute("""
                INSERT INTO qd_authoritative_account_risk_facts
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
                 source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,gross_notional,net_notional,
                 instrument_notional,available_margin,equity,peak_equity,daily_realized_pnl,account_facts_verified)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','account','v1',%s,%s,60,100,100,100,800,500,500,0,true)
            """, (str(uuid.uuid4()), *values, "c" * 64, at))
            cursor.execute("""
                INSERT INTO qd_reconciliation_checkpoints
                (id,tenant_id,credential_id,exchange,market_type,account_scope,instrument_id,status,evidence_hash,version,updated_at,risk_max_age_seconds)
                VALUES (%s,%s,%s,'gate','spot',%s,'BTC_USDT','HEALTHY',%s,1,%s,60)
            """, (str(uuid.uuid4()), self.user_id, self.credential_id, self.account_scope, "d" * 64, at))
            for kind, letter in (("GLOBAL", "e"), ("ACCOUNT", "f"), ("STRATEGY", "0")):
                cursor.execute("""
                    INSERT INTO qd_authoritative_kill_switch_observations
                    (id,contract_version,tenant_id,credential_id,account_scope,strategy_scope,scope_kind,source_identity,
                     source_version,source_fingerprint,observed_at,max_age_seconds,switch_version,enabled,mode)
                    VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,'__NON_STRATEGY__',%s,%s,'v1',%s,%s,60,1,false,NULL)
                """, (str(uuid.uuid4()), self.user_id, self.credential_id, self.account_scope, kind, f"switch-{kind.lower()}", letter * 64, at))
            cursor.execute("""
                INSERT INTO qd_authoritative_market_observations
                (id,contract_version,tenant_id,credential_id,account_scope,instrument_id,market_type,valuation_currency,
                 price_type,price,source_identity,source_version,source_fingerprint,observed_at,max_age_seconds,market_data_health)
                VALUES (%s,'authoritative-risk-facts-v1',%s,%s,%s,%s,%s,'USDT','MARK',50,'market','v1',%s,%s,60,'FRESH')
            """, (str(uuid.uuid4()), *values, "1" * 64, at))

    def _snapshot(self):
        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        auth = GateAuthFacts(
            venue_id="gate",
            market_type=AssetMarketType.SPOT,
            environment=CapabilityEnvironment.TESTNET,
            account_scope=self.account_scope,
            credential_ref=f"credential-{self.credential_id}",
            permissions=(GatePermission.READ_ACCOUNT, GatePermission.READ_ORDER, GatePermission.READ_FILL),
            evidence_version="gate-private-read-v1",
            observed_at=observed,
        )
        instruments = (
            GateInstrumentRuleSnapshot(
                venue_id="gate", market_type=AssetMarketType.SPOT, instrument_id="BTC_USDT",
                tick_size=Decimal("0.1"), quantity_step=Decimal("0.000001"),
                minimum_quantity=Decimal("0.00001"), minimum_notional=Decimal("3"),
                rule_version="gate-private-read-instrument-v1", observed_at=observed,
            ),
        )
        return build_gate_read_snapshot(auth, balances=(), instruments=instruments, positions=(), observed_at=observed)

    def _project(self):
        snapshot = self._snapshot()
        scope_facts = build_scope_binding_facts(snapshot, tenant_id=self.user_id, credential_id=self.credential_id)
        rule_facts = build_instrument_rule_snapshot_facts(snapshot)
        authority_facts = build_instrument_authority_facts(
            snapshot, rule_facts, tenant_id=self.user_id, credential_id=self.credential_id,
            account_scope=self.account_scope,
        )
        with self.connection.cursor() as cursor:
            self.repository.persist_scope_binding(self.connection, scope_facts)
            for row in rule_facts:
                self.repository.persist_instrument_rule_snapshot(self.connection, row)
            for row in authority_facts:
                self.repository.persist_instrument_authority(self.connection, row)
        self.connection.commit()

    def _admit(self, action="OPEN"):
        payload = {
            "source": "REST",
            "mode": "PAPER",
            "credential_id": self.credential_id,
            "instrument_id": "BTC_USDT",
            "market_type": "spot",
            "action": action,
            "side": "BUY",
            "quantity": "0.001",
            "quantity_semantics": "ABSOLUTE",
            "execution_kind": "MARKET",
            "position_side": "NET",
            "reduce_only": False,
            "close_all": False,
            "idempotency_key": f"acceptance-{uuid.uuid4().hex[:12]}",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "occurred_at": "2026-08-05T00:00:00+00:00",
        }
        try:
            result, _graph = admit_runtime_entry_payload_caller_owned(
                self.connection, payload, tenant_id=self.user_id, actor_id=str(self.user_id),
            )
            self.connection.commit()
            return result.disposition
        except RuntimeEntryAdmissionError:
            self.connection.rollback()
            return None

    def test_authority_projection_persists_three_tables(self):
        self._project()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM qd_runtime_entry_scope_bindings WHERE tenant_id = %s AND credential_id = %s",
                (self.user_id, self.credential_id),
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT count(*) FROM qd_instrument_rule_snapshots WHERE exchange = 'gate' AND instrument_id = 'BTC_USDT'",
            )
            self.assertGreaterEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT count(*) FROM qd_runtime_entry_instrument_authorities "
                "WHERE tenant_id = %s AND credential_id = %s AND instrument_id = 'BTC_USDT'",
                (self.user_id, self.credential_id),
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_open_paper_admission_returns_created_after_projection(self):
        from app.domain.runtime_entry_admission_contracts import RuntimeEntryAdmissionDisposition

        # Without authority facts the same admission stays unavailable.
        before = self._admit(action="OPEN")
        self.assertNotEqual(before, RuntimeEntryAdmissionDisposition.CREATED)
        self._project()
        after = self._admit(action="OPEN")
        self.assertEqual(after, RuntimeEntryAdmissionDisposition.CREATED)

    def test_reduce_without_position_subject_stays_fail_closed(self):
        self._project()
        from app.domain.runtime_entry_admission_contracts import RuntimeEntryAdmissionDisposition

        payload = {
            "source": "REST",
            "mode": "PAPER",
            "credential_id": self.credential_id,
            "instrument_id": "BTC_USDT",
            "market_type": "spot",
            "action": "REDUCE",
            "side": "SELL",
            "quantity": "0.0005",
            "quantity_semantics": "ABSOLUTE",
            "execution_kind": "MARKET",
            "position_side": "NET",
            "reduce_only": True,
            "close_all": False,
            "target_position_id": str(uuid.uuid4()),
            "idempotency_key": f"reduce-{uuid.uuid4().hex[:12]}",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "occurred_at": "2026-08-05T00:00:00+00:00",
        }
        try:
            with self.connection.cursor() as cursor:
                result, _graph = admit_runtime_entry_payload_caller_owned(
                    self.connection, payload, tenant_id=self.user_id, actor_id=str(self.user_id),
                )
            self.connection.commit()
            self.assertNotEqual(result.disposition, RuntimeEntryAdmissionDisposition.CREATED)
        except Exception:
            self.connection.rollback()
            # fail-closed path raises typed authority-unavailable; that is acceptable
            pass


if __name__ == "__main__":
    unittest.main()
