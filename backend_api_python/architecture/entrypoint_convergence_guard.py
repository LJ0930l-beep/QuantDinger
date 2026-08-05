"""AST guard for the trading-entry convergence boundary.

The guard is deliberately static: it parses source without importing routes or
trading services.  Existing bypasses are listed individually in a versioned
manifest.  A new call, including one in a previously baselined file, receives a
new semantic fingerprint and fails.  Retiring a legacy call is allowed; moving
or renaming it requires an explicit reviewed manifest record and reason.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


EXCLUDED_PARTS = frozenset({
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".test_deps",
    ".venv", "__pycache__", "build", "dist", "generated", "node_modules", "venv",
})

DIRECT_METHODS = frozenset({
    "_signed_request", "_swap_private_request_raw", "attach_quick_trade_protection",
    "cancel_order", "execute_grid_market_order",
    "place_grid_limit_order", "place_limit_order", "place_market_order",
    "place_native_protection_orders", "place_order", "place_order_from_signal",
    "submit_order",
})

DIRECT_COMMAND_METHODS = frozenset({
    "accept_command_graph", "create_reservation", "persist_command_graph",
    "persist_durable_entry", "persist_reservation",
})

FORBIDDEN_IMPORT_FRAGMENTS = (
    "app.services.trading_executor",
    "app.services.live_trading.execution",
    "app.services.live_trading.native_protection",
    "app.services.quick_trade.credentials",
    "app.services.quick_trade.orders",
    "app.services.command_intent_repository",
    "app.services.durable_entry_repository",
    "app.services.risk_enforcement_repository",
    "app.services.outbox_repository",
)

TRACKED_IMPORTED_SYMBOLS = frozenset({
    "CommandIntentRepository", "DurableEntryRepository", "RiskEnforcementRepository",
    "TradingExecutor", "create_exchange_client", "place_native_protection_orders",
    "place_order_from_signal",
})


@dataclass(frozen=True, order=True)
class EntryPointViolation:
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
    new_violations: tuple[EntryPointViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.new_violations


def load_manifest(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("manifest_version") != "entrypoint-convergence-v1":
        raise ValueError("invalid entrypoint convergence manifest version")
    paths = raw.get("guarded_paths")
    entries = raw.get("entry_points")
    baseline = raw.get("legacy_bypass_baseline")
    if not isinstance(paths, list) or not isinstance(entries, list) or not isinstance(baseline, list):
        raise ValueError("entrypoint convergence manifest has invalid collections")
    for item in paths:
        if not isinstance(item, str) or not item or "*" in item:
            raise ValueError("guarded paths must be explicit paths")
    for item in baseline:
        if not isinstance(item, dict):
            raise ValueError("legacy baseline entries must be objects")
        if any("*" in str(item.get(key, "")) for key in ("path", "symbol", "pattern")):
            raise ValueError("legacy baseline cannot contain wildcards")
        if not isinstance(item.get("legacy_reason"), str) or not item["legacy_reason"].strip():
            raise ValueError("legacy baseline entry needs a migration reason")
    return raw


def load_baseline(manifest: dict[str, Any]) -> tuple[EntryPointViolation, ...]:
    records: list[EntryPointViolation] = []
    for item in manifest["legacy_bypass_baseline"]:
        records.append(EntryPointViolation(
            path=str(item["path"]), symbol=str(item["symbol"]), category=str(item["category"]),
            pattern=str(item["pattern"]), fingerprint=str(item["fingerprint"]), line=int(item["line"]),
            count=int(item.get("count", 1)),
        ))
    return tuple(sorted(records))


class _Visitor(ast.NodeVisitor):
    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.aliases: dict[str, str] = {}
        self._occurrences: dict[tuple[str, str, str, str], int] = {}
        self.violations: list[EntryPointViolation] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
            for item in node.names:
                if item.name in TRACKED_IMPORTED_SYMBOLS:
                    self.aliases[item.asname or item.name] = item.name
                    self._record(node, "direct_import", f"from:{module}:{item.name}")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            if any(fragment in item.name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                self.aliases[item.asname or item.name.split(".")[-1]] = item.name
                self._record(node, "direct_import", f"import:{item.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        # A handler whose first executable statement unconditionally terminates
        # cannot reach its historical body.  Excluding that body lets a retired
        # bypass leave the baseline; removing the terminal statement makes the
        # historical direct call visible as a new violation again.
        if body and isinstance(body[0], (ast.Return, ast.Raise)):
            self.visit(body[0])
            self.scope.pop()
            return
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name in DIRECT_METHODS:
            self._record(node, "direct_order_or_exchange_call", _safe_unparse(node.func))
        elif name in DIRECT_COMMAND_METHODS:
            self._record(node, "direct_persistence_bypass", _safe_unparse(node.func))
        elif name == "execute" and _contains_pending_order_insert(node):
            self._record(node, "legacy_order_intent_write", "SQL:INSERT pending_orders")
        elif name == "_post" and _contains_quick_trade_endpoint(node):
            self._record(node, "direct_quick_trade_http", _safe_unparse(node.args[0]))
        self.generic_visit(node)

    def _record(self, node: ast.AST, category: str, pattern: str) -> None:
        symbol = ".".join(self.scope) or "<module>"
        semantic = ast.dump(node, annotate_fields=True, include_attributes=False)
        key = (symbol, category, pattern, semantic)
        occurrence = self._occurrences.get(key, 0) + 1
        self._occurrences[key] = occurrence
        material = f"{self.relative_path}|{symbol}|{category}|{pattern}|{semantic}|{occurrence}"
        self.violations.append(EntryPointViolation(
            path=self.relative_path, symbol=symbol, category=category, pattern=pattern,
            fingerprint=hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
            line=int(getattr(node, "lineno", 0) or 0),
        ))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _contains_pending_order_insert(node: ast.Call) -> bool:
    return any(isinstance(item, ast.Constant) and isinstance(item.value, str)
               and "insert into pending_orders" in item.value.lower() for item in ast.walk(node))


def _contains_quick_trade_endpoint(node: ast.Call) -> bool:
    return bool(node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "/quick-trade/" in node.args[0].value)


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node, annotate_fields=False, include_attributes=False)


def _iter_python_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        if path.suffix == ".py":
            yield path
        return
    if path.is_dir():
        for candidate in sorted(path.rglob("*.py")):
            if not any(part in EXCLUDED_PARTS for part in candidate.parts):
                yield candidate


def scan_entrypoint_bypasses(repo_root: Path, guarded_paths: Iterable[str]) -> tuple[EntryPointViolation, ...]:
    root = repo_root.resolve()
    violations: list[EntryPointViolation] = []
    seen: set[Path] = set()
    for configured in guarded_paths:
        path = root / configured
        for source in _iter_python_files(path):
            resolved = source.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            relative = resolved.relative_to(root).as_posix()
            tree = ast.parse(resolved.read_text(encoding="utf-8-sig"), filename=relative)
            visitor = _Visitor(relative)
            visitor.visit(tree)
            violations.extend(visitor.violations)
    grouped: dict[tuple[str, str, str, str], list[EntryPointViolation]] = {}
    for item in violations:
        grouped.setdefault((item.path, item.symbol, item.category, item.pattern), []).append(item)
    normalized: list[EntryPointViolation] = []
    for (path, symbol, category, pattern), items in grouped.items():
        first = min(items, key=lambda item: item.line)
        count = len(items)
        material = f"{path}|{symbol}|{category}|{pattern}|count:{count}"
        normalized.append(EntryPointViolation(
            path=path, symbol=symbol, category=category, pattern=pattern,
            fingerprint=hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
            line=first.line, count=count,
        ))
    return tuple(sorted(normalized))


def compare_with_baseline(current: Sequence[EntryPointViolation], baseline: Sequence[EntryPointViolation]) -> GuardComparison:
    baseline_by_key = {(item.path, item.symbol, item.category, item.pattern): item for item in baseline}
    if len(baseline_by_key) != len(baseline):
        raise ValueError("duplicate entrypoint guard baseline keys")
    new = []
    for item in current:
        previous = baseline_by_key.get((item.path, item.symbol, item.category, item.pattern))
        if previous is None or item.count > previous.count:
            new.append(item)
    return GuardComparison(tuple(sorted(new)))


def baseline_json(violations: Sequence[EntryPointViolation]) -> str:
    return json.dumps([item.baseline_record() for item in sorted(violations)], indent=2) + "\n"


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_default_root())
    parser.add_argument("--manifest", type=Path, default=Path("backend_api_python/architecture/entrypoint_convergence_manifest.json"))
    parser.add_argument("--print-baseline", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    manifest = load_manifest(manifest_path)
    current = scan_entrypoint_bypasses(root, manifest["guarded_paths"])
    if args.print_baseline:
        print(baseline_json(current), end="")
        return 0
    comparison = compare_with_baseline(current, load_baseline(manifest))
    if comparison.passed:
        print(f"entrypoint convergence guard passed ({len(current)} tracked legacy bypasses)")
        return 0
    for item in comparison.new_violations:
        print(f"NEW {item.path}:{item.line} {item.symbol} {item.category} {item.pattern} [{item.fingerprint}]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
