from datetime import datetime, timedelta, timezone
import unittest

from tests.pr07_contract_loader import load_outbox_projection_contracts


contracts = load_outbox_projection_contracts()
OutboxConflict = contracts.OutboxConflict
OutboxEvent = contracts.OutboxEvent
OutboxProjectionContractError = contracts.OutboxProjectionContractError
ProjectionCheckpoint = contracts.ProjectionCheckpoint
ProjectionGap = contracts.ProjectionGap
ProjectionVersionConflict = contracts.ProjectionVersionConflict
UnsupportedEventSchema = contracts.UnsupportedEventSchema
apply_projection_event = contracts.apply_projection_event


AGGREGATE = "00000000-0000-0000-0000-000000000501"
NOW = datetime(2026, 7, 26, 5, 10, tzinfo=timezone.utc)
SCHEMAS = {("FILL_APPLIED", "v1")}


def event(version=0, payload=None):
    return OutboxEvent("ECONOMIC_ORDER", AGGREGATE, version, "FILL_APPLIED", "v1", payload or {"fill": "f-1"})


class OutboxProjectionContractTests(unittest.TestCase):
    def test_event_identity_is_deterministic_and_excludes_write_time(self):
        first, second = event(), event()
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.payload_hash, second.payload_hash)
        self.assertNotIn("created", first.event_id)

    def test_identity_same_payload_replays_and_different_payload_conflicts(self):
        first = event()
        self.assertEqual(first.event_id, event(payload={"fill": "f-1"}).event_id)
        with self.assertRaises(OutboxConflict):
            if event(payload={"fill": "changed"}).event_id == first.event_id and event(payload={"fill": "changed"}).payload_hash != first.payload_hash:
                raise OutboxConflict("same identity with different payload")

    def test_float_payload_is_rejected(self):
        with self.assertRaises(OutboxProjectionContractError):
            event(payload={"price": 1.2})

    def test_checkpoint_requires_monotonic_contiguous_versions(self):
        checkpoint = ProjectionCheckpoint("projection-test", "ECONOMIC_ORDER", AGGREGATE)
        applied = apply_projection_event(checkpoint, event(0), supported_schemas=SCHEMAS, now_utc=NOW)
        self.assertFalse(applied.idempotent_replay)
        replay = apply_projection_event(applied.checkpoint, event(0), supported_schemas=SCHEMAS, now_utc=NOW)
        self.assertTrue(replay.idempotent_replay)
        with self.assertRaises(ProjectionGap):
            apply_projection_event(applied.checkpoint, event(2), supported_schemas=SCHEMAS, now_utc=NOW)
        with self.assertRaises(ProjectionVersionConflict):
            apply_projection_event(applied.checkpoint, event(0, {"fill": "other"}), supported_schemas=SCHEMAS, now_utc=NOW)

    def test_unknown_schema_and_scope_fail_closed(self):
        checkpoint = ProjectionCheckpoint("projection-test", "ECONOMIC_ORDER", AGGREGATE)
        with self.assertRaises(UnsupportedEventSchema):
            apply_projection_event(checkpoint, OutboxEvent("ECONOMIC_ORDER", AGGREGATE, 0, "UNKNOWN", "v1", {}), supported_schemas=SCHEMAS, now_utc=NOW)
        with self.assertRaises(OutboxProjectionContractError):
            apply_projection_event(checkpoint, OutboxEvent("OTHER", AGGREGATE, 0, "FILL_APPLIED", "v1", {}), supported_schemas=SCHEMAS, now_utc=NOW)

    def test_non_utc_clock_is_rejected(self):
        with self.assertRaises(OutboxProjectionContractError):
            apply_projection_event(ProjectionCheckpoint("projection-test", "ECONOMIC_ORDER", AGGREGATE), event(), supported_schemas=SCHEMAS, now_utc=NOW.astimezone(timezone(timedelta(hours=8))))
