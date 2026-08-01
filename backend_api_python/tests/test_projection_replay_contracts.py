import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    names = ["app", "app.domain", "app.domain.outbox_projection_contracts", "app.domain.projection_consumer_contracts", "app.domain.projection_replay_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]; sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/outbox_projection_contracts.py"), (names[3], ROOT / "app/domain/projection_consumer_contracts.py"), (names[4], ROOT / "app/domain/projection_replay_contracts.py")):
            spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[4]], sys.modules[names[2]], sys.modules[names[3]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, OUTBOX, CONSUMER = load()
AGG = uuid4()


def consumer():
    return CONSUMER.RegisteredProjectionConsumer("entry-reader", "reader-v1", (("ENTRY_ADMITTED", "entry-admission-v2"),), ("entry",), "a" * 64)


def event(version=0, payload=None):
    return OUTBOX.OutboxEvent("entry", AGG, version, "ENTRY_ADMITTED", "entry-admission-v2", payload or {"value": version})


class ProjectionReplayTests(unittest.TestCase):
    def test_applies_contiguous_events_and_tracks_checkpoint(self):
        state = M.ProjectionReplayState(consumer())
        first = M.apply_projection_replay(state, M.ProjectionReplayEvent(0, event()), now_utc=UTC)
        second = M.apply_projection_replay(first.state, M.ProjectionReplayEvent(1, event(1)), now_utc=UTC)
        self.assertEqual(first.disposition, M.ProjectionReplayDisposition.CREATED)
        self.assertEqual(second.state.checkpoints[0].last_applied_version, 1)

    def test_exact_offset_replay_and_conflict(self):
        state = M.apply_projection_replay(M.ProjectionReplayState(consumer()), M.ProjectionReplayEvent(0, event()), now_utc=UTC).state
        replay = M.apply_projection_replay(state, M.ProjectionReplayEvent(0, event()), now_utc=UTC)
        self.assertEqual(replay.disposition, M.ProjectionReplayDisposition.REPLAYED)
        with self.assertRaises(M.ProjectionReplayError):
            M.apply_projection_replay(state, M.ProjectionReplayEvent(0, event(payload={"changed": True})), now_utc=UTC)

    def test_offset_gap_and_aggregate_version_gap_fail_closed(self):
        state = M.ProjectionReplayState(consumer())
        with self.assertRaises(M.ProjectionReplayError):
            M.apply_projection_replay(state, M.ProjectionReplayEvent(1, event(1)), now_utc=UTC)
        state = M.apply_projection_replay(state, M.ProjectionReplayEvent(0, event()), now_utc=UTC).state
        with self.assertRaises(M.ProjectionReplayError):
            M.apply_projection_replay(state, M.ProjectionReplayEvent(1, event(2)), now_utc=UTC)

    def test_unknown_event_and_state_are_typed_and_deterministic(self):
        with self.assertRaises(M.ProjectionReplayError):
            M.apply_projection_replay(M.ProjectionReplayState(consumer()), M.ProjectionReplayEvent(0, OUTBOX.OutboxEvent("other", AGG, 0, "UNKNOWN", "v1", {})), now_utc=UTC)
        first = M.apply_projection_replay(M.ProjectionReplayState(consumer()), M.ProjectionReplayEvent(0, event()), now_utc=UTC).state
        second = M.apply_projection_replay(M.ProjectionReplayState(consumer()), M.ProjectionReplayEvent(0, event()), now_utc=UTC).state
        self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)


if __name__ == "__main__": unittest.main()
