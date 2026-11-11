"""Audit: Postgres views exposed via PostgREST declare security_invoker.

Blind spot this closes: a view without `WITH (security_invoker = true)` runs with
the owner's privileges and bypasses base-table RLS. The frontend queries views, so
this makes RLS non-load-bearing on the real surface even after anon read is removed
(see #272). Existence-only SQL tests never catch this.

Heuristic (textual, no SQL-parser dep): SQL comments are blanked out first, then for
each `CREATE [OR REPLACE] VIEW <name>` the text up to the view body (`AS`) is searched
for the `security_invoker` option and its value is parsed. Only a true value
(`true`/`on`/`yes`/`1`, or the bare option, which Postgres reads as true) is safe;
`false`/`off`/`no`/`0`, an unrecognised value, or no option at all is a finding.
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

# The view's `WITH (...)` options list; a column list before it is not options.
_WITH_OPTIONS = re.compile(r"\bwith\s*\(([^)]*)\)", re.IGNORECASE)
# `security_invoker` (optionally a quoted identifier) with an optional `= value`;
# the value may be quoted.
_SECURITY_INVOKER = re.compile(
    r"(?:\"security_invoker\"|\bsecurity_invoker\b)"
    r"(?:\s*=\s*('[^']*'|\"[^\"]*\"|[A-Za-z0-9_]+))?",
    re.IGNORECASE,
)
# Dollar-quote opening tag: `$$` or `$tag$`, tag an identifier (digits allowed
# after the first character).
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
# Postgres boolean spellings it accepts for reloptions.
_TRUE_VALUES = {"true", "on", "yes", "1", "t", "y"}


def strip_sql_comments(text: str) -> str:
    """Blank out `--` and `/* */` comments, keeping newlines so line numbers hold.

    Single-quoted strings (including `E'...'` escape strings with backslash
    escapes), double-quoted identifiers and dollar-quoted bodies are skipped so
    comment markers inside them are left alone.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("--", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif text.startswith("/*", i):
            # Postgres block comments nest.
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        elif ch in ("'", '"'):
            # E'...' allows backslash escapes, e.g. E'it\'s'.
            backslash_escapes = (
                ch == "'"
                and i > 0
                and text[i - 1] in "eE"
                and (i < 2 or not (text[i - 2].isalnum() or text[i - 2] == "_"))
            )
            j = i + 1
            while j < n:
                if backslash_escapes and text[j] == "\\":
                    j += 2
                    continue
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:  # doubled quote escape
                        j += 2
                        continue
                    break
                j += 1
            out.append(text[i : j + 1])
            i = j + 1
        elif ch == "$":
            m = _DOLLAR_TAG.match(text, i)
            if m:
                tag = m.group(0)
                end = text.find(tag, i + len(tag))
                end = n if end == -1 else end + len(tag)
                out.append(text[i:end])
                i = end
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def security_invoker_enabled(header: str) -> bool:
    """True when the view's `WITH (...)` options set `security_invoker` to true.

    Only the options list is searched, so a column named `security_invoker` in
    the view's column list does not count.
    """
    options = _WITH_OPTIONS.search(header)
    if not options:
        return False
    match = _SECURITY_INVOKER.search(options.group(1))
    if not match:
        return False
    value = match.group(1)
    if value is None:  # bare `WITH (security_invoker)` means true
        return True
    return value.strip("'\"").lower() in _TRUE_VALUES


def scan_migrations(migrations_dir: Path) -> list[Finding]:
    findings_by_view: dict[str, Finding] = {}
    if not migrations_dir.exists():
        return []
    for path in sorted(migrations_dir.glob("*.sql")):
        text = strip_sql_comments(path.read_text(encoding="utf-8"))
        rel = f"supabase/migrations/{path.name}"
        for m in _CREATE_VIEW.finditer(text):
            view_name = m.group(1)
            normalized_view_name = view_name.replace('"', "").lower()
            # header = text between CREATE VIEW and the view body keyword ` AS `
            tail = text[m.end():]
            as_match = re.search(r"\bas\b", tail, re.IGNORECASE)
            header = tail[: as_match.start()] if as_match else tail[:200]
            if security_invoker_enabled(header):
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
