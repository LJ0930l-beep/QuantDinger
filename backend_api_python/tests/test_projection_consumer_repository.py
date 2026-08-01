"""Pure caller-owned projection consumer repository boundary tests."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4
import unittest


_MISSING = object()


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_modules() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.services",
        "app.domain.outbox_projection_contracts",
        "app.domain.projection_consumer_contracts",
        "app.services.outbox_projection_repository",
        "app.services.projection_consumer_repository",
    )
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        services = ModuleType("app.services")
        services.__path__ = [str(app_dir / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        outbox = _load(
            "app.domain.outbox_projection_contracts",
            app_dir / "domain" / "outbox_projection_contracts.py",
        )
        contracts = _load(
            "app.domain.projection_consumer_contracts",
            app_dir / "domain" / "projection_consumer_contracts.py",
        )
        repository = _load(
            "app.services.outbox_projection_repository",
            app_dir / "services" / "outbox_projection_repository.py",
        )
        consumer = _load(
            "app.services.projection_consumer_repository",
            app_dir / "services" / "projection_consumer_repository.py",
        )
        return SimpleNamespace(outbox=outbox, contracts=contracts, repository=repository, consumer=consumer)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


MODULES = _load_modules()
OutboxEvent = MODULES.outbox.OutboxEvent
ProjectionCheckpoint = MODULES.outbox.ProjectionCheckpoint
ProjectionApplyResult = MODULES.outbox.ProjectionApplyResult
ProjectionPersistResult = MODULES.repository.ProjectionPersistResult
ProjectionConsumeRequest = MODULES.contracts.ProjectionConsumeRequest
ProjectionConsumeResult = MODULES.contracts.ProjectionConsumeResult
ConsumerApplyDisposition = MODULES.contracts.ConsumerApplyDisposition
RegisteredProjectionConsumer = MODULES.contracts.RegisteredProjectionConsumer
ProjectionGenerationConflict = MODULES.repository.ProjectionGenerationConflict
ProjectionConsumerRepository = MODULES.consumer.ProjectionConsumerRepository
ProjectionConsumerRepositoryError = MODULES.consumer.ProjectionConsumerRepositoryError

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class _FakeCursor:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self):
        self.cursor_value = _FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _event():
    return OutboxEvent(
        "ECONOMIC_ORDER", uuid4(), 0, "ENTRY_ADMITTED", "entry-admission-v2", {"mode": "PAPER"}
    )


def _request():
    consumer = RegisteredProjectionConsumer(
        "candidate-ledger", "projection-consumer-v1",
        (("ENTRY_ADMITTED", "entry-admission-v2"),), ("ECONOMIC_ORDER",),
    )
    return ProjectionConsumeRequest(consumer, str(uuid4()), 0, _event(), NOW)


class _FakeRepository:
    def __init__(self, outcome=None, error=None):
        self.outcome = outcome
        self.error = error
        self.calls = []

    def apply_to_projection(self, connection, **kwargs):
        self.calls.append((connection, kwargs))
        cursor = connection.cursor()
        cursor.close()
        if self.error is not None:
            raise self.error
        return self.outcome


class ProjectionConsumerRepositoryTests(unittest.TestCase):
    def _projection_outcome(self, request, *, replay):
        checkpoint = ProjectionCheckpoint(
            request.consumer.consumer_name,
            request.event.aggregate_type,
            request.event.aggregate_id,
            request.event.aggregate_version,
            request.event.event_id,
            request.event.payload_hash,
            request.now_utc,
        )
        return ProjectionPersistResult(ProjectionApplyResult(checkpoint, replay), request.generation_id)

    def test_created_delegates_without_transaction_control_and_closes_cursor(self):
        request = _request()
        connection = _FakeConnection()
        repository = _FakeRepository(self._projection_outcome(request, replay=False))
        result = ProjectionConsumerRepository(repository=repository).consume(connection, request)
        self.assertIsInstance(result, ProjectionConsumeResult)
        self.assertIs(result.disposition, ConsumerApplyDisposition.CREATED)
        self.assertEqual(result.resulting_checkpoint_version, 0)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(connection.cursor_value.closed)
        self.assertEqual(repository.calls[0][1]["source_offset"], request.source_offset)

    def test_replay_is_typed_and_does_not_commit_or_rollback(self):
        request = _request()
        connection = _FakeConnection()
        repository = _FakeRepository(self._projection_outcome(request, replay=True))
        result = ProjectionConsumerRepository(repository=repository).apply(connection, request)
        self.assertIs(result.disposition, ConsumerApplyDisposition.REPLAYED)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)

    def test_business_conflict_is_preserved(self):
        request = _request()
        conflict = ProjectionGenerationConflict("different immutable event")
        with self.assertRaises(ProjectionGenerationConflict) as caught:
            ProjectionConsumerRepository(repository=_FakeRepository(error=conflict)).consume(
                _FakeConnection(), request
            )
        self.assertIs(caught.exception, conflict)

    def test_unclassified_database_failure_is_typed(self):
        request = _request()
        with self.assertRaises(ProjectionConsumerRepositoryError) as caught:
            ProjectionConsumerRepository(repository=_FakeRepository(error=RuntimeError("driver detail"))).consume(
                _FakeConnection(), request
            )
        self.assertNotIn("driver detail", str(caught.exception))

    def test_untyped_repository_result_is_rejected(self):
        request = _request()
        with self.assertRaises(ProjectionConsumerRepositoryError):
            ProjectionConsumerRepository(repository=_FakeRepository(outcome=object())).consume(
                _FakeConnection(), request
            )

    def test_invalid_request_fails_before_repository_call(self):
        connection = _FakeConnection()
        fake = _FakeRepository()
        with self.assertRaises(MODULES.contracts.ProjectionConsumerContractError):
            ProjectionConsumerRepository(repository=fake).consume(connection, object())
        self.assertEqual(fake.calls, [])
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 0)

    def test_source_has_no_commit_or_rollback_calls(self):
        path = Path(__file__).resolve().parents[1] / "app" / "services" / "projection_consumer_repository.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertNotIn("commit", calls)
        self.assertNotIn("rollback", calls)


if __name__ == "__main__":
    unittest.main()
