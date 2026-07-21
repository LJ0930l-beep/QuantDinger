"""AST guard for direct order side effects outside the future gateway.

The scanner reads Python source only. It never imports the modules it checks.
Legacy violations are pinned by a semantic fingerprint so new calls in an
already-baselined file still fail the guard.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


FORBIDDEN_ORDER_METHODS = frozenset(
    {
        "_signed_request",
        "_swap_private_request_raw",
        "add_order",
        "cancelOrder",
        "cancel_order",
        "placeOrder",
        "place_bracket_order",
        "place_conditional_order",
        "place_limit_order",
        "place_market_order",
        "place_order",
        "place_stop_order",
        "place_take_profit_order",
        "submitOrder",
        "submit_order",
    }
)

FORBIDDEN_ORDER_HELPERS = frozenset(
    {
        "cancel_grid_order",
        "execute_grid_market_order",
        "place_grid_limit_order",
        "place_native_protection_orders",
        "place_order_from_signal",
    }
)

DEFAULT_PROTECTED_PATHS = (
    "backend_api_python/app/routes",
    "backend_api_python/app/services/grid",
    "backend_api_python/app/services/quick_trade",
    "backend_api_python/app/services/live_trading/native_protection.py",
    "mcp_server/src/quantdinger_mcp",
)

EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".test_deps",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "generated",
        "node_modules",
        "venv",
    }
)


@dataclass(frozen=True, order=True)
class ArchitectureViolation:
    path: str
    symbol: str
    pattern: str
    fingerprint: str
    line: int

    def baseline_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GuardComparison:
    new_violations: tuple[ArchitectureViolation, ...]
    stale_baseline: tuple[ArchitectureViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.new_violations and not self.stale_baseline


class _OrderCallVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.aliases: dict[str, str] = {}
        self._occurrences: dict[tuple[str, str, str], int] = {}
        self.violations: list[ArchitectureViolation] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for item in node.names:
            if item.name in FORBIDDEN_ORDER_METHODS | FORBIDDEN_ORDER_HELPERS:
                self.aliases[item.asname or item.name] = item.name
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        target = self._forbidden_reference(node.value)
        if target:
            for item in node.targets:
                if isinstance(item, ast.Name):
                    self.aliases[item.id] = target
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target = self._forbidden_reference(node.value)
        if target and isinstance(node.target, ast.Name):
            self.aliases[node.target.id] = target
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        pattern = self._forbidden_call_pattern(node.func)
        if pattern:
            symbol = ".".join(self.scope) or "<module>"
            semantic_call = ast.dump(node, annotate_fields=True, include_attributes=False)
            key = (symbol, pattern, semantic_call)
            occurrence = self._occurrences.get(key, 0) + 1
            self._occurrences[key] = occurrence
            material = f"{self.relative_path}|{symbol}|{pattern}|{semantic_call}|{occurrence}"
            fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
            self.violations.append(
                ArchitectureViolation(
                    path=self.relative_path,
                    symbol=symbol,
                    pattern=pattern,
                    fingerprint=fingerprint,
                    line=int(getattr(node, "lineno", 0) or 0),
                )
            )
        self.generic_visit(node)

    def _forbidden_reference(self, node: ast.AST | None) -> str:
        if isinstance(node, ast.Name):
            if node.id in self.aliases:
                return self.aliases[node.id]
            if node.id in FORBIDDEN_ORDER_METHODS | FORBIDDEN_ORDER_HELPERS:
                return node.id
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ORDER_METHODS:
            return _safe_unparse(node)
        return ""

    def _forbidden_call_pattern(self, func: ast.AST) -> str:
        if isinstance(func, ast.Name):
            if func.id in self.aliases:
                return f"alias:{func.id}->{self.aliases[func.id]}"
            if func.id in FORBIDDEN_ORDER_METHODS | FORBIDDEN_ORDER_HELPERS:
                return func.id
            return ""
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_ORDER_METHODS:
            return _safe_unparse(func)
        if isinstance(func, ast.Call) and _is_forbidden_getattr(func):
            return f"getattr:{_safe_unparse(func)}"
        return ""


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node, annotate_fields=False, include_attributes=False)


def _is_forbidden_getattr(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != "getattr" or len(node.args) < 2:
        return False
    method = node.args[1]
    return (
        isinstance(method, ast.Constant)
        and isinstance(method.value, str)
        and method.value in FORBIDDEN_ORDER_METHODS
    )


def _iter_python_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        if path.suffix == ".py" and not any(part in EXCLUDED_PARTS for part in path.parts):
            yield path
        return
    if not path.is_dir():
        return
    for candidate in sorted(path.rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in candidate.parts):
            continue
        yield candidate


def scan_order_side_effects(
    repo_root: Path,
    protected_paths: Iterable[str | Path] = DEFAULT_PROTECTED_PATHS,
) -> tuple[ArchitectureViolation, ...]:
    root = repo_root.resolve()
    violations: list[ArchitectureViolation] = []
    seen_files: set[Path] = set()
    for configured in protected_paths:
        path = Path(configured)
        absolute = path if path.is_absolute() else root / path
        for source_path in _iter_python_files(absolute):
            resolved = source_path.resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            try:
                relative_path = resolved.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError(f"protected path is outside repository: {resolved}") from exc
            try:
                tree = ast.parse(resolved.read_text(encoding="utf-8-sig"), filename=relative_path)
            except (OSError, UnicodeDecodeError, SyntaxError) as exc:
                raise ValueError(f"cannot parse protected source {relative_path}: {exc}") from exc
            visitor = _OrderCallVisitor(relative_path)
            visitor.visit(tree)
            violations.extend(visitor.violations)
    return tuple(sorted(violations))


def load_baseline(path: Path) -> tuple[ArchitectureViolation, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("architecture baseline must be a JSON list")
    records: list[ArchitectureViolation] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("architecture baseline entries must be objects")
        records.append(
            ArchitectureViolation(
                path=str(item["path"]),
                symbol=str(item["symbol"]),
                pattern=str(item["pattern"]),
                fingerprint=str(item["fingerprint"]),
                line=int(item["line"]),
            )
        )
    return tuple(sorted(records))


def compare_with_baseline(
    current: Sequence[ArchitectureViolation],
    baseline: Sequence[ArchitectureViolation],
) -> GuardComparison:
    current_by_fingerprint = {item.fingerprint: item for item in current}
    baseline_by_fingerprint = {item.fingerprint: item for item in baseline}
    if len(current_by_fingerprint) != len(current) or len(baseline_by_fingerprint) != len(baseline):
        raise ValueError("duplicate architecture fingerprints")
    new = tuple(
        sorted(item for key, item in current_by_fingerprint.items() if key not in baseline_by_fingerprint)
    )
    stale = tuple(
        sorted(item for key, item in baseline_by_fingerprint.items() if key not in current_by_fingerprint)
    )
    return GuardComparison(new_violations=new, stale_baseline=stale)


def baseline_json(violations: Sequence[ArchitectureViolation]) -> str:
    return json.dumps(
        [item.baseline_record() for item in sorted(violations)],
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("backend_api_python/architecture/order_side_effect_baseline.json"),
    )
    parser.add_argument("--print-baseline", action="store_true")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    current = scan_order_side_effects(repo_root)
    if args.print_baseline:
        print(baseline_json(current), end="")
        return 0

    baseline_path = args.baseline
    if not baseline_path.is_absolute():
        baseline_path = repo_root / baseline_path
    comparison = compare_with_baseline(current, load_baseline(baseline_path))
    if comparison.passed:
        print(f"order architecture guard passed ({len(current)} baselined legacy calls)")
        return 0
    for label, items in (
        ("NEW", comparison.new_violations),
        ("STALE", comparison.stale_baseline),
    ):
        for item in items:
            print(
                f"{label} {item.path}:{item.line} {item.symbol} "
                f"{item.pattern} [{item.fingerprint}]"
            )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
