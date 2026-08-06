import unittest

from app.services import non_live_product_rehearsal_service as S


class NonLiveProductRehearsalTests(unittest.TestCase):
    def test_complete_fixture_chain_is_deterministic_and_non_live(self):
        first = S.build_offline_product_rehearsal()
        second = S.build_offline_product_rehearsal()
        self.assertEqual(first, second)
        self.assertFalse(first["live_enabled"])
        self.assertFalse(first["network_access"])
        self.assertEqual(first["execution_boundary"], "READ_ONLY_FIXTURE")
        self.assertEqual(first["environment"]["LIVE"], False)
        self.assertEqual(first["deterministic_backtest"]["decisions"][0]["decision"], "executed")
        self.assertEqual(first["paper_account"]["filled_count"], 1)
        self.assertEqual(first["paper_recovery"]["status"], "VERIFIED")
        self.assertEqual(first["paper_recovery"]["snapshot_fingerprint"], first["paper_account"]["snapshot_fingerprint"])
        self.assertTrue(first["paper_recovery"]["replay_fingerprint"])
        self.assertIsNotNone(first["candidate_entry"])
        self.assertEqual(first["candidate_entry"]["action"], "OPEN")
        self.assertEqual(first["candidate_entry"]["mode"], "PAPER")
        self.assertEqual(first["candidate_entry"]["admission_persistence"], "NOT_PERSISTED_OFFLINE_REHEARSAL")
        self.assertEqual(first["admission"]["disposition"], "CREATED")
        self.assertEqual(first["admission"]["risk_decision_status"], "ALLOW")
        self.assertTrue(first["admission"]["reservation_id"])
        self.assertTrue(first["admission"]["outbox_event_id"])
        self.assertEqual(first["admission"]["typed_event_parser"], "PASS")
        self.assertEqual(first["admission"]["persistence"], "NOT_INVOKED_READ_ONLY_REHEARSAL")
        self.assertEqual(first["admission"]["transaction_owner"], "CALLER_NOT_GATEWAY")
        self.assertEqual(len(first["strategy_catalog"]), 9)
        self.assertEqual(first["strategy_catalog"][7]["supported_timeframes"], ["5m"])

    def test_rehearsal_does_not_require_credentials_or_database(self):
        result = S.build_offline_product_rehearsal()
        serialized = repr(result)
        self.assertNotIn("secret", serialized.lower())
        self.assertNotIn("api_key", serialized.lower())


if __name__ == "__main__":
    unittest.main()
