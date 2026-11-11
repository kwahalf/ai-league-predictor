"""Regression tests for scripts/audit/check_view_security_invoker.py.

The audit used to accept any mention of `security_invoker` before the view body,
so `WITH (security_invoker = false)` or a comment naming the option passed. It
must parse the value and ignore comments.

The audit lives outside `temporal/`, so it is loaded by path. CI runs pytest from
the repo root; inside the worker image only `temporal/` is mounted at /app, so the
test is skipped there unless `scripts/` is mounted next to it (e.g. at /scripts).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _find_audit_module() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "scripts" / "audit" / "check_view_security_invoker.py"
        if candidate.is_file():
            return candidate
    return None


_AUDIT_PATH = _find_audit_module()
if _AUDIT_PATH is None:
    pytest.skip("scripts/audit is not available in this checkout", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("check_view_security_invoker", _AUDIT_PATH)
audit = importlib.util.module_from_spec(_spec)
# Registered before exec: dataclasses look the module up in sys.modules.
sys.modules[_spec.name] = audit
_spec.loader.exec_module(audit)


def _scan(tmp_path: Path, *migrations: str) -> list:
    for i, sql in enumerate(migrations):
        (tmp_path / f"2026010100000{i}_m.sql").write_text(sql, encoding="utf-8")
    return audit.scan_migrations(tmp_path)


def test_security_invoker_true_is_safe(tmp_path):
    sql = "create view v_ok with (security_invoker = true) as select 1;"
    assert _scan(tmp_path, sql) == []


@pytest.mark.parametrize("value", ["on", "ON", "1", "yes", "'true'"])
def test_other_true_spellings_are_safe(tmp_path, value):
    sql = f"create or replace view v_ok with (security_invoker={value}) as select 1;"
    assert _scan(tmp_path, sql) == []


def test_bare_option_is_safe(tmp_path):
    # Postgres reads a boolean reloption given without a value as true.
    sql = "create view v_ok with (security_invoker) as select 1;"
    assert _scan(tmp_path, sql) == []


@pytest.mark.parametrize("value", ["false", "FALSE", "off", "0", "no", "'false'"])
def test_security_invoker_false_is_a_finding(tmp_path, value):
    sql = f"create view v_bad with (security_invoker = {value}) as select 1;"
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert "v_bad" in findings[0].message


def test_line_comment_mentioning_option_is_a_finding(tmp_path):
    sql = (
        "create view v_bad -- TODO add security_invoker = true\n"
        "as select 1;\n"
    )
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert findings[0].location.endswith(":1")


def test_block_comment_mentioning_option_is_a_finding(tmp_path):
    sql = "create view v_bad /* security_invoker = true */ as select 1;"
    assert len(_scan(tmp_path, sql)) == 1


def test_commented_out_safe_view_is_not_scanned(tmp_path):
    sql = "-- create view v_old as select 1;\nselect 1;\n"
    assert _scan(tmp_path, sql) == []


def test_view_without_option_is_a_finding(tmp_path):
    sql = "\n\ncreate view public.v_plain as select 1;"
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert findings[0].location.endswith("_m.sql:3")
    assert findings[0].severity == "HIGH"


def test_later_migration_fixing_view_clears_finding(tmp_path):
    findings = _scan(
        tmp_path,
        "create view v_x as select 1;",
        "create or replace view v_x with (security_invoker = true) as select 1;",
    )
    assert findings == []


def test_later_migration_disabling_option_reintroduces_finding(tmp_path):
    findings = _scan(
        tmp_path,
        "create view v_x with (security_invoker = true) as select 1;",
        "create or replace view v_x with (security_invoker = false) as select 1;",
    )
    assert len(findings) == 1


def test_comment_markers_inside_strings_are_kept(tmp_path):
    sql = (
        "create view v_ok with (security_invoker = true) as\n"
        "select '-- not a comment' as a, $$/* nor this */$$ as b;\n"
    )
    assert _scan(tmp_path, sql) == []


def test_quoted_option_name_false_is_a_finding(tmp_path):
    sql = 'create view v_bad with ("security_invoker" = false) as select 1;'
    assert len(_scan(tmp_path, sql)) == 1


def test_quoted_option_name_true_is_safe(tmp_path):
    sql = 'create view v_ok with ("security_invoker" = true) as select 1;'
    assert _scan(tmp_path, sql) == []


def test_column_named_security_invoker_is_a_finding(tmp_path):
    sql = "create view v_bad (security_invoker) as select 1;"
    assert len(_scan(tmp_path, sql)) == 1


def test_column_named_security_invoker_does_not_hide_false_option(tmp_path):
    sql = (
        "create view v_bad (security_invoker) "
        "with (security_invoker = false) as select 1;"
    )
    assert len(_scan(tmp_path, sql)) == 1


def test_column_list_with_true_option_is_safe(tmp_path):
    sql = (
        "create view v_ok (security_invoker) "
        "with (security_barrier, security_invoker = true) as select 1;"
    )
    assert _scan(tmp_path, sql) == []


def test_escape_string_with_backslash_quote_does_not_hide_view(tmp_path):
    sql = (
        "select E'it\\'s /* text';\n"
        "create view v_bad as select 1;\n"
    )
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert findings[0].location.endswith(":2")


def test_dollar_tag_with_digits_does_not_hide_view(tmp_path):
    sql = (
        "select $body1$/* literal text$body1$;\n"
        "create view v_bad as select 1;\n"
    )
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert findings[0].location.endswith(":2")


def test_quoted_column_name_cannot_mask_options(tmp_path):
    sql = (
        'create view v_bad ("with (security_invoker)") '
        "with (security_invoker = false) as select 1;"
    )
    assert len(_scan(tmp_path, sql)) == 1


@pytest.mark.parametrize("value", ["$$false$$", "$flag$off$flag$", "+0", "-0", "1 + 0"])
def test_unreadable_or_false_values_are_findings(tmp_path, value):
    sql = f"create view v_bad with (security_invoker = {value}) as select 1;"
    assert len(_scan(tmp_path, sql)) == 1


def test_dollar_inside_identifier_is_not_a_dollar_quote(tmp_path):
    sql = (
        "create table t (col$tag$ integer);\n"
        "-- $tag$ /* text\n"
        "create view v_bad as select 1;\n"
    )
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert findings[0].location.endswith(":3")


def test_view_created_inside_function_body_is_scanned(tmp_path):
    sql = (
        "create function f() returns void language plpgsql as $fn$\n"
        "begin\n"
        "  create view v_dyn as select 1;\n"
        "end $fn$;\n"
    )
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert findings[0].location.endswith(":3")


def test_view_created_in_single_quoted_do_body_is_scanned(tmp_path):
    sql = "DO 'BEGIN CREATE VIEW v_bad AS SELECT 1; END;';"
    assert len(_scan(tmp_path, sql)) == 1


def test_view_created_by_execute_literal_is_scanned(tmp_path):
    sql = (
        "DO $$\n"
        "BEGIN\n"
        "  EXECUTE 'CREATE VIEW v_bad AS SELECT 1';\n"
        "END;\n"
        "$$;\n"
    )
    findings = _scan(tmp_path, sql)
    assert len(findings) == 1
    assert findings[0].location.endswith(":3")


def test_execute_literal_with_true_option_is_safe(tmp_path):
    sql = (
        "DO $$ BEGIN EXECUTE "
        "'CREATE VIEW v_ok WITH (security_invoker = ''true'') AS SELECT 1'; END $$;"
    )
    assert _scan(tmp_path, sql) == []


def test_create_view_text_in_comment_on_is_ignored(tmp_path):
    sql = "comment on table t is 'create view v_fake as select 1';"
    assert _scan(tmp_path, sql) == []


def test_repo_migrations_pass():
    root = _AUDIT_PATH.parents[2]
    assert audit.scan_migrations(root / "supabase" / "migrations") == []
