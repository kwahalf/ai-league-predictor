"""Audit: every Temporal workflow/activity defined in the worker package is registered.

Blind spot this closes: a PR that adds a workflow file rarely touches worker.py,
so a diff-scoped reviewer cannot see that the new workflow is never registered and
therefore cannot run. (A workflow file added without a corresponding registration is defined but never runs.)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

try:
    from ._common import CheckResult, Finding, emit, repo_root
except ImportError:  # invoked as a script, not a package
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import CheckResult, Finding, emit, repo_root  # type: ignore


def _has_decorator(node: ast.AST, attr: str) -> bool:
    decorators = getattr(node, "decorator_list", [])
    for dec in decorators:
        target = dec.func if isinstance(dec, ast.Call) else dec
        # matches `@workflow.defn` / `@activity.defn` (and the bare/var forms)
        if isinstance(target, ast.Attribute) and target.attr == attr:
            return True
        if isinstance(target, ast.Name) and target.id == attr:
            return True
    return False


def _defined_names(root: Path, kind: str) -> set[str]:
    """kind='defn' on classes (workflows) or functions (activities)."""
    names: set[str] = set()
    if not root.exists():
        return names
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            is_class = isinstance(node, ast.ClassDef)
            is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if kind == "workflow" and is_class and _has_decorator(node, "defn"):
                names.add(node.name)
            elif kind == "activity" and is_func and _has_decorator(node, "defn"):
                names.add(node.name)
    return names


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map the local name back to the imported name.

    `from .activities.agent import build_activities as build_agent_activities`
    means the call site says one name and the definition carries another.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name.split(".")[-1]
    return aliases


def _factory_activity_names(
    call: ast.expr, src_root: Path, aliases: dict[str, str] | None = None
) -> set[str]:
    """Activity names registered through `*build_activities()`.

    A factory that closes over injected dependencies (a tool registry, a
    persistence layer) and returns its activities is a legitimate registration:
    the names never appear in worker.py, so a literal-only reader reports them as
    unregistered. This resolves the called function and collects the activities
    defined inside it.
    """
    if not isinstance(call, ast.Call):
        return set()
    func = call.func
    factory_name = (
        func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    )
    if not factory_name:
        return set()
    factory_name = (aliases or {}).get(factory_name, factory_name)

    names: set[str] = set()
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # A factory may be imported under an alias, so match either the
            # definition's own name or the name it is called by.
            if node.name != factory_name:
                continue
            for inner in ast.walk(node):
                is_func = isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                if is_func and inner is not node and _has_decorator(inner, "defn"):
                    names.add(inner.name)
    return names


def _registered_names(worker_py: Path, src_root: Path) -> tuple[set[str], set[str]]:
    """Extract names passed to Worker(workflows=[...], activities=[...])."""
    workflows: set[str] = set()
    activities: set[str] = set()
    if not worker_py.exists():
        return workflows, activities
    tree = ast.parse(worker_py.read_text(encoding="utf-8"))
    aliases = _import_aliases(tree)

    def collect(elts: list[ast.expr]) -> set[str]:
        out: set[str] = set()
        for el in elts:
            if isinstance(el, ast.Starred):  # *build_activities()
                out |= _factory_activity_names(el.value, src_root, aliases)
            elif isinstance(el, ast.Attribute):  # module.func
                out.add(el.attr)
            elif isinstance(el, ast.Name):  # ClassName / func
                out.add(el.id)
        return out

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "workflows" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    workflows |= collect(kw.value.elts)
                if kw.arg == "activities" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    activities |= collect(kw.value.elts)
    return workflows, activities


def find_unregistered(src_root: Path) -> list[Finding]:
    """src_root is temporal/src. Returns findings for defined-but-unregistered."""
    worker_py = src_root / "worker.py"
    defined_wf = _defined_names(src_root / "workflows", "workflow")
    defined_act = _defined_names(src_root / "activities", "activity")
    reg_wf, reg_act = _registered_names(worker_py, src_root)

    findings: list[Finding] = []
    for name in sorted(defined_wf - reg_wf):
        findings.append(
            Finding(
                check="temporal-registration",
                severity="CRITICAL",
                location="temporal/src/worker.py",
                message=f"Workflow `{name}` is @workflow.defn but not registered with the Worker — it cannot run.",
                issue="#269",
            )
        )
    for name in sorted(defined_act - reg_act):
        findings.append(
            Finding(
                check="temporal-registration",
                severity="HIGH",
                location="temporal/src/worker.py",
                message=f"Activity `{name}` is @activity.defn but not registered with the Worker.",
                issue="#269",
            )
        )
    return findings


def run(root: Path | None = None) -> CheckResult:
    root = root or repo_root()
    return CheckResult(
        name="temporal-registration",
        findings=find_unregistered(root / "temporal" / "src"),
    )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="exit non-zero if findings exist")
    args = ap.parse_args()
    result = run()
    emit([result])
    return 1 if (args.strict and not result.ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
