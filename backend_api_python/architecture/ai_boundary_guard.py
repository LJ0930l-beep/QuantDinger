"""Static boundary guard for deterministic trading code.

The guard intentionally parses source without importing the application.  Its
scope is the deterministic trading core and strategy platform services: new
model-provider imports or a direct model-output-to-trading-fact flow fail the
guard.  Existing legacy imports are recorded individually in a reviewed
baseline and may only be retired, never expanded.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


AI_IMPORT_FRAGMENTS = (
    "openai", "anthropic", "litellm", "langchain", "app.services.llm", "app.services.ai_",
)

TRADING_FACT_SINKS = frozenset({
    "CanonicalEntryRequestV2", "CanonicalEconomicIntentV2", "build_runtime_entry_request",
    "HardRiskFacts", "RiskInputSnapshot", "PositionSizingInput", "calculate_position_size",
})


@dataclass(frozen=True, order=True)
class AIBoundaryViolation:
    path: str
    symbol: str
    category: str
    pattern: str
    fingerprint: str
    line: int
    count: int = 1

    def baseline_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GuardComparison:
    new_violations: tuple[AIBoundaryViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.new_violations


def load_manifest(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("manifest_version") != "ai-boundary-v1":
        raise ValueError("invalid AI boundary manifest version")
    paths = raw.get("guarded_paths")
    baseline = raw.get("legacy_ai_baseline")
    if not isinstance(paths, list) or not isinstance(baseline, list):
        raise ValueError("AI boundary manifest collections are invalid")
    if any(not isinstance(item, str) or not item or "*" in item for item in paths):
        raise ValueError("AI boundary paths must be explicit")
    for item in baseline:
        if not isinstance(item, dict) or not str(item.get("legacy_reason", "")).strip():
            raise ValueError("AI boundary baseline needs a migration reason")
        if any("*" in str(item.get(key, "")) for key in ("path", "symbol", "pattern")):
            raise ValueError("AI boundary baseline cannot use wildcards")
    return raw


def load_baseline(manifest: dict[str, Any]) -> tuple[AIBoundaryViolation, ...]:
    return tuple(sorted(AIBoundaryViolation(
        path=str(item["path"]), symbol=str(item["symbol"]), category=str(item["category"]),
        pattern=str(item["pattern"]), fingerprint=str(item["fingerprint"]),
        line=int(item["line"]), count=int(item.get("count", 1)),
    ) for item in manifest["legacy_ai_baseline"]))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _contains_model_call(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            name = _call_name(item.func).lower()
            if any(token in name for token in ("llm", "model", "completion", "generate", "chat")):
                return True
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.scope: list[str] = []
        self._records: list[AIBoundaryViolation] = []
        self._model_output_names: set[str] = set()

    def _record(self, node: ast.AST, category: str, pattern: str) -> None:
        symbol = ".".join(self.scope) or "<module>"
        material = f"{self.relative_path}|{symbol}|{category}|{pattern}"
        self._records.append(AIBoundaryViolation(
            path=self.relative_path, symbol=symbol, category=category, pattern=pattern,
            fingerprint=hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
            line=int(getattr(node, "lineno", 0) or 0),
        ))

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            if any(fragment in item.name.lower() for fragment in AI_IMPORT_FRAGMENTS):
                self._record(node, "ai_provider_import", f"import:{item.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = (node.module or "").lower()
        if any(fragment in module for fragment in AI_IMPORT_FRAGMENTS):
            for item in node.names:
                self._record(node, "ai_provider_import", f"from:{node.module}:{item.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        if _contains_model_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._model_output_names.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        sink = _call_name(node.func)
        if sink in TRADING_FACT_SINKS:
            direct_model = any(_contains_model_call(arg) for arg in (*node.args, *(kw.value for kw in node.keywords)))
            assigned_model = any(isinstance(arg, ast.Name) and arg.id in self._model_output_names for arg in node.args)
            if direct_model or assigned_model:
                self._record(node, "model_output_to_trading_fact", sink)
        self.generic_visit(node)

    def normalized(self) -> tuple[AIBoundaryViolation, ...]:
        grouped: dict[tuple[str, str, str, str], list[AIBoundaryViolation]] = {}
        for item in self._records:
            grouped.setdefault((item.path, item.symbol, item.category, item.pattern), []).append(item)
        return tuple(sorted(AIBoundaryViolation(
            path=path, symbol=symbol, category=category, pattern=pattern,
            fingerprint=hashlib.sha256(f"{path}|{symbol}|{category}|{pattern}".encode("utf-8")).hexdigest()[:24],
            line=min(item.line for item in items), count=len(items),
        ) for (path, symbol, category, pattern), items in grouped.items()))


def scan_ai_boundary(repo_root: Path, guarded_paths: Iterable[str]) -> tuple[AIBoundaryViolation, ...]:
    root = repo_root.resolve()
    visitor_records: list[AIBoundaryViolation] = []
    for configured in guarded_paths:
        path = root / configured
        if not path.is_file() or path.suffix != ".py":
            raise ValueError(f"guarded AI boundary path must be a Python file: {configured}")
        relative = path.resolve().relative_to(root).as_posix()
        visitor = _Visitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative))
        visitor_records.extend(visitor.normalized())
    grouped: dict[tuple[str, str, str, str], list[AIBoundaryViolation]] = {}
    for item in visitor_records:
        grouped.setdefault((item.path, item.symbol, item.category, item.pattern), []).append(item)
    return tuple(sorted(item for items in grouped.values() for item in items))


def compare_with_baseline(current: Sequence[AIBoundaryViolation], baseline: Sequence[AIBoundaryViolation]) -> GuardComparison:
    known = {(item.path, item.symbol, item.category, item.pattern): item for item in baseline}
    if len(known) != len(baseline):
        raise ValueError("duplicate AI boundary baseline keys")
    return GuardComparison(tuple(sorted(
        item for item in current
        if (previous := known.get((item.path, item.symbol, item.category, item.pattern))) is None
        or item.count > previous.count
    )))


def baseline_json(violations: Sequence[AIBoundaryViolation]) -> str:
    return json.dumps([item.baseline_record() for item in sorted(violations)], indent=2) + "\n"
