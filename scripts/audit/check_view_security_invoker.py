"""Audit: Postgres views exposed via PostgREST declare security_invoker.

Blind spot this closes: a view without `WITH (security_invoker = true)` runs with
the owner's privileges and bypasses base-table RLS. The frontend queries views, so
this makes RLS non-load-bearing on the real surface even after anon read is removed
(see #272). Existence-only SQL tests never catch this.

Heuristic (textual, no SQL-parser dep): for each `CREATE [OR REPLACE] VIEW <name>`,
inspect the text up to the view body (`AS`) for `security_invoker`.
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

_CREATE_VIEW = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:materialized\s+)?view\s+(?:if\s+not\s+exists\s+)?([A-Za-z0-9_.\"]+)",
    re.IGNORECASE,
)


def scan_migrations(migrations_dir: Path) -> list[Finding]:
    findings_by_view: dict[str, Finding] = {}
    if not migrations_dir.exists():
        return []
    for path in sorted(migrations_dir.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        rel = f"supabase/migrations/{path.name}"
        for m in _CREATE_VIEW.finditer(text):
            view_name = m.group(1)
            normalized_view_name = view_name.replace('"', "").lower()
            # header = text between CREATE VIEW and the view body keyword ` AS `
            tail = text[m.end():]
            as_match = re.search(r"\bas\b", tail, re.IGNORECASE)
            header = tail[: as_match.start()] if as_match else tail[:200]
            if "security_invoker" in header.lower():
                findings_by_view.pop(normalized_view_name, None)
                continue

            line = text[: m.start()].count("\n") + 1
            findings_by_view[normalized_view_name] = Finding(
                check="view-security-invoker",
                severity="HIGH",
                location=f"{rel}:{line}",
                message=(
                    f"View `{view_name}` is created without "
                    "`WITH (security_invoker = true)` — it bypasses base-table RLS."
                ),
                issue="#272",
            )
    return sorted(findings_by_view.values(), key=lambda finding: finding.location)


def run(root: Path | None = None) -> CheckResult:
    root = root or repo_root()
    return CheckResult(
        name="view-security-invoker",
        findings=scan_migrations(root / "supabase" / "migrations"),
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
