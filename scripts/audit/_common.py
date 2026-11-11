"""Shared finding type + reporting helpers for architecture-audit checks."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def repo_root() -> Path:
    """Resolve the repository root from this file's location."""
    return Path(__file__).resolve().parents[2]


@dataclass
class Finding:
    check: str
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    location: str  # file or file:line
    message: str
    issue: str = ""  # related tracking issue, e.g. "#269"


@dataclass
class CheckResult:
    name: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def render_markdown(results: list[CheckResult]) -> str:
    lines: list[str] = ["## Architecture Audit", ""]
    total = sum(len(r.findings) for r in results)
    if total == 0:
        lines.append("✅ No findings — all checks clean.")
        return "\n".join(lines) + "\n"

    lines.append(f"Found **{total}** finding(s) across {len(results)} check(s).")
    lines.append("")
    lines.append("| Check | Severity | Location | Finding | Issue |")
    lines.append("|---|---|---|---|---|")
    for result in results:
        for f in sorted(result.findings, key=lambda x: x.severity):
            msg = f.message.replace("|", "\\|")
            lines.append(
                f"| {f.check} | {f.severity} | `{f.location}` | {msg} | {f.issue} |"
            )
    return "\n".join(lines) + "\n"


def emit(results: list[CheckResult]) -> None:
    """Print the markdown report to stdout and to $GITHUB_STEP_SUMMARY when present."""
    report = render_markdown(results)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
