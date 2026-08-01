from datetime import datetime, timezone
import ast
import importlib.util
import sys
from types import ModuleType
from pathlib import Path
from uuid import uuid4
import unittest


def _load_contracts():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    names = ("app", "app.domain", "app.domain.outbox_projection_contracts", "app.domain.projection_consumer_contracts")
    missing = object()
    original = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(root / "app")]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(root / "app" / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        modules = {}
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, root / "app" / "domain" / f"{name.rsplit('.', 1)[-1]}.py")
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load {name}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name.rsplit('.', 1)[-1]] = module
        return modules["outbox_projection_contracts"].OutboxEvent, modules["projection_consumer_contracts"]
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


OutboxEvent, contracts = _load_contracts()
ProjectionConsumeRequest = contracts.ProjectionConsumeRequest
ConsumerApplyDisposition = contracts.ConsumerApplyDisposition
ProjectionConsumerContractError = contracts.ProjectionConsumerContractError
ProjectionConsumeResult = contracts.ProjectionConsumeResult
RegisteredProjectionConsumer = contracts.RegisteredProjectionConsumer
UnsupportedProjectionEvent = contracts.UnsupportedProjectionEvent


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _event(*, aggregate_type="ECONOMIC_ORDER", event_type="ENTRY_ADMITTED", schema_version="entry-admission-v2"):
    return OutboxEvent(aggregate_type, uuid4(), 0, event_type, schema_version, {"mode": "PAPER"})


def _consumer(**changes):
    values = {
        "consumer_name": "candidate-ledger",
        "contract_version": "projection-consumer-v1",
        "supported_schemas": (("ENTRY_ADMITTED", "entry-admission-v2"),),
        "aggregate_types": ("ECONOMIC_ORDER",),
    }
    values.update(changes)
    return RegisteredProjectionConsumer(**values)


class ProjectionConsumerContractTests(unittest.TestCase):
    def test_registration_is_immutable_and_fingerprint_is_stable(self):
        consumer = _consumer()
        self.assertEqual(consumer.fingerprint, _consumer().fingerprint)
        with self.assertRaises(AttributeError):
            consumer.consumer_name = "other"

    def test_schema_and_aggregate_allow_list_is_canonical(self):
        consumer = _consumer(
            supported_schemas=(("ENTRY_ADMITTED", "entry-admission-v2"), ("CANCEL_ADMITTED", "entry-admission-v2")),
            aggregate_types=("ECONOMIC_ORDER", "CANCEL"),
        )
        self.assertTrue(consumer.accepts(_event()))
        self.assertTrue(consumer.accepts(_event(aggregate_type="CANCEL", event_type="CANCEL_ADMITTED")))
        with self.assertRaises(UnsupportedProjectionEvent):
            consumer.accepts(_event(event_type="UNKNOWN"))
        with self.assertRaises(UnsupportedProjectionEvent):
            consumer.accepts(_event(aggregate_type="POSITION"))

    def test_duplicate_or_empty_registration_fails_closed(self):
        with self.assertRaises(ProjectionConsumerContractError):
            _consumer(supported_schemas=())
        with self.assertRaises(ProjectionConsumerContractError):
            _consumer(supported_schemas=(("ENTRY_ADMITTED", "v1"), ("ENTRY_ADMITTED", "v1")))
        with self.assertRaises(ProjectionConsumerContractError):
            _consumer(aggregate_types=("ECONOMIC_ORDER", "ECONOMIC_ORDER"))
        with self.assertRaises(ProjectionConsumerContractError):
            _consumer(consumer_name="Candidate-Ledger")

    def test_request_validates_scope_offset_event_and_strict_utc(self):
        consumer = _consumer()
        request = ProjectionConsumeRequest(consumer, str(uuid4()), 0, _event(), NOW)
        self.assertEqual(request.now_utc, NOW)
        with self.assertRaises(ProjectionConsumerContractError):
            ProjectionConsumeRequest(consumer, str(uuid4()), -1, _event(), NOW)
        with self.assertRaises(ProjectionConsumerContractError):
            ProjectionConsumeRequest(consumer, str(uuid4()), 0, _event(), datetime(2026, 8, 1, 12, 0))

    def test_request_rejects_unknown_event_before_persistence(self):
        with self.assertRaises(UnsupportedProjectionEvent):
            ProjectionConsumeRequest(_consumer(), str(uuid4()), 0, _event(event_type="UNKNOWN"), NOW)

    def test_result_is_typed_and_replay_fingerprint_is_deterministic(self):
        request = ProjectionConsumeRequest(_consumer(), str(uuid4()), 3, _event(), NOW, 2)
        first = ProjectionConsumeResult(request, ConsumerApplyDisposition.CREATED, 3)
        replay = ProjectionConsumeResult(request, ConsumerApplyDisposition.REPLAYED, 3)
        self.assertEqual(first.fingerprint, ProjectionConsumeResult(request, ConsumerApplyDisposition.CREATED, 3).fingerprint)
        self.assertNotEqual(first.fingerprint, replay.fingerprint)
        with self.assertRaises(ProjectionConsumerContractError):
            ProjectionConsumeResult(request, "CREATED", 3)

    def test_contract_module_has_no_runtime_or_infrastructure_imports(self):
        path = Path(__file__).resolve().parents[1] / "app" / "domain" / "projection_consumer_contracts.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        forbidden = ("flask", "psycopg", "exchange", "worker", "scheduler", "executor")
        self.assertFalse(any(any(token in module.lower() for token in forbidden) for module in imports), imports)


if __name__ == "__main__":
    unittest.main()
