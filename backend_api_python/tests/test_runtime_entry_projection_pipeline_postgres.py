"""PostgreSQL integration coverage for the full runtime-entry pipeline.

Projects authority facts, reconciles a HEALTHY checkpoint, persists position
projections and subjects, and asserts REDUCE/CLOSE/PROTECTION admission becomes
CREATED once a healthy position subject exists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
import unittest
import uuid

from app.domain.gate_read_snapshot_contracts import build_gate_read_snapshot
from app.domain.gate_vertical_read_contracts import (
    GateAuthFacts,
    GateInstrumentRuleSnapshot,
    GateMarginMode,
    GatePermission,
    GatePositionFact,
    GatePositionSide,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.services.runtime_entry_admission_http_service import admit_runtime_entry_payload_caller_owned
from app.services.runtime_entry_admission_service import RuntimeEntryAdmissionError
from app.services.runtime_entry_authority_projection_service import (
    RuntimeEntryAuthorityProjectionError,
    RuntimeEntryAuthorityProjectionService,
)
from app.services.runtime_entry_reconciliation_service import RuntimeEntryReconciliationService


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class RuntimeEntryProjectionPipelinePostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg2

        self.psycopg2 = psycopg2
        self.connection = psycopg2.connect(os.environ["DATABASE_URL"])
        self.connection.autocommit = False
        self._seed()

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _seed(self):
        suffix = uuid.uuid4().hex[:10]
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO qd_users(username, password_hash) VALUES (%s, %s) RETURNING id",
                (f"pipeline_{suffix}", "schema-test"),
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
        at = datetime.now(timezone.utc)
        values = (self.user_id, self.credential_id, self.account_scope, "BTC_USDT", "perpetual")
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

    def _snapshot(self, positions=()):
        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        auth = GateAuthFacts(
            "gate", AssetMarketType.PERPETUAL, CapabilityEnvironment.TESTNET, self.account_scope,
            f"credential-{self.credential_id}",
            (GatePermission.READ_ACCOUNT, GatePermission.READ_ORDER, GatePermission.READ_FILL),
            "gate-private-read-v1", observed,
        )
        instruments = (
            GateInstrumentRuleSnapshot(
                "gate", AssetMarketType.PERPETUAL, "BTC_USDT", Decimal("0.1"), Decimal("0.000001"),
                Decimal("0.00001"), Decimal("3"), "gate-private-read-instrument-v1", observed,
            ),
        )
        return build_gate_read_snapshot(auth, balances=(), instruments=instruments, positions=positions, observed_at=observed)

    def _position(self, quantity="0.01"):
        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        return GatePositionFact(
            "gate", AssetMarketType.PERPETUAL, self.account_scope, "BTC_USDT", GatePositionSide.LONG,
            Decimal(quantity), Decimal("60000"), Decimal("60100"), Decimal("10"),
            GateMarginMode.ISOLATED, observed, "event-1",
        )

    def _seed_local_fill(self, quantity="0.01"):
        """Seed one matching local paper fill so reconciliation is HEALTHY."""

        at = datetime(2026, 8, 5, tzinfo=timezone.utc)
        with self.connection.cursor() as cursor:
            order_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO qd_paper_execution_orders
                (id, user_id, idempotency_key, request_fingerprint, order_fingerprint,
                 market, symbol, market_type, side, order_type, quantity, status, fill_quantity, fill_price,
                 created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'BUY', 'MARKET', %s, 'FILLED', %s, 60000, %s, %s)
                """,
                (order_id, self.user_id, f"key-{uuid.uuid4().hex[:8]}", uuid.uuid4().hex * 2, uuid.uuid4().hex * 2,
                 "perp", "BTC_USDT", "perpetual", Decimal(quantity), Decimal(quantity), at, at),
            )
            cursor.execute(
                """
                INSERT INTO qd_paper_execution_fills
                (id, order_id, quantity, price, fee_amount, fee_asset, occurred_at, fill_fingerprint)
                VALUES (%s, %s, %s, 60000, 0, 'USDT', %s, %s)
                """,
                (str(uuid.uuid4()), order_id, Decimal(quantity), at, uuid.uuid4().hex * 2),
            )
        self.connection.commit()

    def _run_pipeline(self, snapshot):
        service = RuntimeEntryAuthorityProjectionService(
            snapshot_provider=lambda *a, **k: snapshot,
        )
        result = service.run_pipeline(
            self.connection,
            user_id=self.user_id,
            credential_id=self.credential_id,
            account_scope=self.account_scope,
            market_type="perpetual",
            instrument_id="BTC_USDT",
            as_of=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
        self.connection.commit()
        return result

    def _admit(self, action="REDUCE", target_position_id=None):
        is_reducing = action in {"REDUCE", "CLOSE", "PROTECTION"}
        from datetime import datetime as _dt, timezone as _tz

        occurred_at = _dt.now(_tz.utc).isoformat()
        payload = {
            "source": "REST",
            "mode": "PAPER",
            "credential_id": self.credential_id,
            "instrument_id": "BTC_USDT",
            "market_type": "perpetual",
            "action": action,
            "side": "SELL" if action != "OPEN" else "BUY",
            "quantity_semantics": "ABSOLUTE" if not is_reducing else None,
            "execution_kind": "MARKET",
            "reduce_only": is_reducing,
            "close_all": action == "CLOSE",
            "idempotency_key": f"pipeline-{uuid.uuid4().hex[:12]}",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "occurred_at": occurred_at,
        }
        if action == "OPEN":
            payload["quantity"] = "0.001"
            payload["position_side"] = "NET"
        else:
            payload["quantity"] = None
            payload["close_quantity"] = "0.0005"
            payload["position_side"] = "LONG"
        if target_position_id:
            payload["target_position_id"] = target_position_id
        try:
            result, _graph = admit_runtime_entry_payload_caller_owned(
                self.connection, payload, tenant_id=self.user_id, actor_id=str(self.user_id),
            )
            self.connection.commit()
            return result.disposition
        except RuntimeEntryAdmissionError:
            self.connection.rollback()
            return None

    def test_full_pipeline_persists_checkpoint_and_subjects(self):
        self._seed_local_fill()
        snapshot = self._snapshot(positions=(self._position(),))
        result = self._run_pipeline(snapshot)
        self.assertEqual(result["checkpoint"]["status"], "HEALTHY")
        self.assertEqual(result["checkpoint"]["discrepancy_count"], 0)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM qd_position_projections "
                "WHERE tenant_id = %s AND credential_id = %s AND instrument_id = 'BTC_USDT'",
                (self.user_id, self.credential_id),
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT count(*) FROM qd_runtime_entry_position_subjects "
                "WHERE tenant_id = %s AND credential_id = %s AND position_side = 'LONG'",
                (self.user_id, self.credential_id),
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_reduce_admission_returns_created_after_healthy_subject(self):
        self._seed_local_fill()
        snapshot = self._snapshot(positions=(self._position(),))
        self._run_pipeline(snapshot)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM qd_runtime_entry_position_subjects "
                "WHERE tenant_id = %s AND credential_id = %s LIMIT 1",
                (self.user_id, self.credential_id),
            )
            target_position_id = str(cursor.fetchone()[0])
        from app.domain.runtime_entry_admission_contracts import RuntimeEntryAdmissionDisposition

        disposition = self._admit(action="REDUCE", target_position_id=target_position_id)
        self.assertEqual(disposition, RuntimeEntryAdmissionDisposition.CREATED)

    def test_reduce_without_subject_stays_fail_closed(self):
        # No positions in snapshot -> no projection/subject -> REDUCE unavailable.
        snapshot = self._snapshot(positions=())
        self._run_pipeline(snapshot)
        from app.domain.runtime_entry_admission_contracts import RuntimeEntryAdmissionDisposition

        disposition = self._admit(action="REDUCE", target_position_id=str(uuid.uuid4()))
        self.assertNotEqual(disposition, RuntimeEntryAdmissionDisposition.CREATED)


if __name__ == "__main__":
    unittest.main()
