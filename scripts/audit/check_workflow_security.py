"""Audit: GitHub Actions workflows do not expose secrets to fork-influenced runs.

Blind spot this closes: the security reviewer is label-gated and does not scan
.github/workflows/** holistically, so an unsafe `pull_request_target`-with-secrets
pattern can ship in the factory's own workflows (see #274).

Heuristic (textual, no YAML dep): flag a workflow that triggers on
`pull_request_target` AND references `secrets.` anywhere. Also flag `permissions: write-all`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from ._common import CheckResult, Finding, emit, repo_root
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import CheckResult, Finding, emit, repo_root  # type: ignore

_PR_TARGET = re.compile(r"^\s*pull_request_target\s*:?", re.MULTILINE)
_SECRETS = re.compile(r"secrets\.[A-Za-z_][A-Za-z0-9_]*")
_WRITE_ALL = re.compile(r"^\s*permissions:\s*write-all\s*$", re.MULTILINE)


def scan_workflows(workflows_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    if not workflows_dir.exists():
        return findings
    for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        rel = f".github/workflows/{path.name}"
        if _PR_TARGET.search(text) and _SECRETS.search(text):
            secret_names = sorted(set(_SECRETS.findall(text)))[:5]
            findings.append(
                Finding(
                    check="workflow-security",
                    severity="CRITICAL",
                    location=rel,
                    message=(
                        "Uses `pull_request_target` AND references secrets "
                        f"({', '.join(secret_names)}) — exposes write-scoped creds to "
                        "fork-influenced runs (prompt-injection-to-write path)."
                    ),
                    issue="#274",
                )
            )
        if _WRITE_ALL.search(text):
            findings.append(
                Finding(
                    check="workflow-security",
                    severity="HIGH",
                    location=rel,
                    message="Grants `permissions: write-all` — use least-privilege explicit scopes instead.",
                )
            )
    return findings


def run(root: Path | None = None) -> CheckResult:
    root = root or repo_root()
    return CheckResult(
        name="workflow-security",
        findings=scan_workflows(root / ".github" / "workflows"),
    )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    result = run()
    emit([result])
    return 1 if (args.strict and not result.ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
