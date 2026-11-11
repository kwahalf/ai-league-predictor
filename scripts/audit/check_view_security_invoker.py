"""Audit: Postgres views exposed via PostgREST declare security_invoker.

Blind spot this closes: a view without `WITH (security_invoker = true)` runs with
the owner's privileges and bypasses base-table RLS. The frontend queries views, so
this makes RLS non-load-bearing on the real surface even after anon read is removed
(see #272). Existence-only SQL tests never catch this.

Method (no SQL-parser dependency): each migration is split into SQL tokens by a
small lexer that understands comments (`--`, nested `/* */`), single-quoted and
`E'...'` strings, quoted identifiers and dollar-quoted bodies. For each
`CREATE [OR REPLACE] [MATERIALIZED] VIEW [IF NOT EXISTS] <name>`, an optional
column list is skipped and the view's own `WITH (...)` options list is parsed.
Only `security_invoker` set to a true value (`true`/`on`/`yes`/`1`, or the bare
option, which Postgres reads as true) is safe. A false value, any value the
audit can't read as true, or no option at all is a finding. String bodies
(dollar-quoted or single-quoted, e.g. function and `DO` bodies or `EXECUTE`
arguments) are scanned too, since they can create views; only `COMMENT ... IS`
text is skipped as inert.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from ._common import CheckResult, Finding, emit, repo_root
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import CheckResult, Finding, emit, repo_root  # type: ignore

# Postgres boolean spellings it accepts as true for reloptions.
_TRUE_VALUES = {"true", "on", "yes", "1", "t", "y"}

# Unquoted identifiers and numbers. `$` is allowed after the first character.
_WORD = re.compile(r"[A-Za-z0-9_\x80-\uffff][A-Za-z0-9_$\x80-\uffff]*")
# Dollar-quote opening tag: `$$` or `$tag$`, tag an identifier.
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_\x80-\uffff][A-Za-z0-9_\x80-\uffff]*)?\$")
# Modifiers allowed between CREATE and VIEW.
_VIEW_MODIFIERS = {"or", "replace", "temp", "temporary", "recursive", "materialized"}


@dataclass
class Token:
    kind: str  # word | ident | string | dollar | punct
    value: str  # word text, unquoted identifier/string content, or the char
    pos: int  # offset of the token's first character in the text
    body_pos: int = -1  # strings: offset where the content starts


def tokenize(text: str) -> list[Token]:
    """Split SQL into tokens, dropping whitespace and comments."""
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
        elif text.startswith("/*", i):
            # Postgres block comments nest.
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif ch in ("'", '"'):
            value, i_end = _read_quoted(text, i, backslash_escapes=False)
            tokens.append(Token("string" if ch == "'" else "ident", value, i, i + 1))
            i = i_end
        elif ch in "eE" and text.startswith("'", i + 1):
            # E'...' escape string: backslash escapes the next character.
            value, i_end = _read_quoted(text, i + 1, backslash_escapes=True)
            tokens.append(Token("string", value, i, i + 2))
            i = i_end
        elif ch == "$" and (m := _DOLLAR_TAG.match(text, i)):
            # Words consume their own `$`s, so a `$` here is at a token boundary.
            tag = m.group(0)
            body_start = m.end()
            close = text.find(tag, body_start)
            body_end = n if close == -1 else close
            tokens.append(Token("dollar", text[body_start:body_end], i, body_start))
            i = n if close == -1 else close + len(tag)
        elif m := _WORD.match(text, i):
            tokens.append(Token("word", m.group(0), i))
            i = m.end()
        else:
            tokens.append(Token("punct", ch, i))
            i += 1
    return tokens


def _read_quoted(text: str, start: int, *, backslash_escapes: bool) -> tuple[str, int]:
    """Read a quoted string or identifier at `start`; return (content, end)."""
    quote = text[start]
    out: list[str] = []
    j, n = start + 1, len(text)
    while j < n:
        c = text[j]
        if backslash_escapes and c == "\\":
            out.append(text[j + 1 : j + 2])
            j += 2
            continue
        if c == quote:
            if text.startswith(quote, j + 1):  # doubled quote escape
                out.append(quote)
                j += 2
                continue
            return "".join(out), j + 1
        out.append(c)
        j += 1
    return "".join(out), n


def _is_word(tokens: list[Token], k: int, *words: str) -> bool:
    return k < len(tokens) and tokens[k].kind == "word" and tokens[k].value.lower() in words


def _is_punct(tokens: list[Token], k: int, ch: str) -> bool:
    return k < len(tokens) and tokens[k].kind == "punct" and tokens[k].value == ch


def _skip_parens(tokens: list[Token], k: int) -> int:
    """`tokens[k]` is `(`; return the index just past its matching `)`."""
    depth = 0
    while k < len(tokens):
        if _is_punct(tokens, k, "("):
            depth += 1
        elif _is_punct(tokens, k, ")"):
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return k


def _split_options(tokens: list[Token]) -> list[list[Token]]:
    """Split the tokens inside `WITH (...)` on top-level commas."""
    options: list[list[Token]] = [[]]
    depth = 0
    for tok in tokens:
        if tok.kind == "punct" and tok.value == "(":
            depth += 1
        elif tok.kind == "punct" and tok.value == ")":
            depth -= 1
        elif tok.kind == "punct" and tok.value == "," and depth == 0:
            options.append([])
            continue
        options[-1].append(tok)
    return [opt for opt in options if opt]


def security_invoker_from_options(option_tokens: list[Token]) -> bool:
    """True when the options set `security_invoker` to true (the last setting wins)."""
    enabled = False
    for opt in _split_options(option_tokens):
        name = opt[0]
        if name.kind not in ("word", "ident") or name.value.lower() != "security_invoker":
            continue
        if len(opt) == 1:  # bare option means true
            enabled = True
        elif (
            len(opt) == 3
            and opt[1].kind == "punct"
            and opt[1].value == "="
            and opt[2].kind in ("word", "ident", "string", "dollar")
        ):
            enabled = opt[2].value.lower() in _TRUE_VALUES
        else:  # anything we can't read as true, e.g. `= +0`
            enabled = False
    return enabled


@dataclass
class ViewDecl:
    name: str
    line: int
    secure: bool


def find_views(text: str, first_line: int = 1) -> list[ViewDecl]:
    """Every CREATE VIEW in `text`, including those inside string bodies.

    Single-quoted and dollar-quoted strings are scanned as SQL, because `DO`
    blocks, function bodies and `EXECUTE` arguments can create views. Content
    keeps its newlines, so nested line numbers stay exact. Only the text after
    `IS` (as in `COMMENT ON ... IS '...'`) is treated as inert.
    """
    tokens = tokenize(text)
    views: list[ViewDecl] = []
    k = 0
    while k < len(tokens):
        tok = tokens[k]
        if tok.kind in ("dollar", "string"):
            if not _is_word(tokens, k - 1, "is"):
                body_line = first_line + text[: tok.body_pos].count("\n")
                views.extend(find_views(tok.value, body_line))
            k += 1
            continue
        if not _is_word(tokens, k, "create"):
            k += 1
            continue
        start = tok.pos
        j = k + 1
        while _is_word(tokens, j, *_VIEW_MODIFIERS):
            j += 1
        if not _is_word(tokens, j, "view"):
            k += 1
            continue
        j += 1
        if _is_word(tokens, j, "if") and _is_word(tokens, j + 1, "not") and _is_word(tokens, j + 2, "exists"):
            j += 3
        # Name: identifiers separated by dots, e.g. public.v or "public"."v".
        parts: list[str] = []
        while j < len(tokens) and tokens[j].kind in ("word", "ident"):
            parts.append(tokens[j].value)
            j += 1
            if _is_punct(tokens, j, "."):
                j += 1
            else:
                break
        if not parts:
            k += 1
            continue
        if _is_punct(tokens, j, "("):  # column list
            j = _skip_parens(tokens, j)
        secure = False
        if _is_word(tokens, j, "with") and _is_punct(tokens, j + 1, "("):
            close = _skip_parens(tokens, j + 1)
            secure = security_invoker_from_options(tokens[j + 2 : close - 1])
            j = close
        line = first_line + text[:start].count("\n")
        views.append(ViewDecl(name=".".join(parts), line=line, secure=secure))
        k = j
    return views


def scan_migrations(migrations_dir: Path) -> list[Finding]:
    findings_by_view: dict[str, Finding] = {}
    if not migrations_dir.exists():
        return []
    for path in sorted(migrations_dir.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        rel = f"supabase/migrations/{path.name}"
        for view in find_views(text):
            normalized_view_name = view.name.lower()
            if view.secure:
                findings_by_view.pop(normalized_view_name, None)
                continue

            findings_by_view[normalized_view_name] = Finding(
                check="view-security-invoker",
                severity="HIGH",
                location=f"{rel}:{view.line}",
                message=(
                    f"View `{view.name}` is created without "
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
