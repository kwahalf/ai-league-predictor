"""Run all architecture-audit checks and report findings.

Usage:
  python scripts/audit/run_audits.py            # report mode (always exit 0)
  python scripts/audit/run_audits.py --strict   # exit 1 if any findings (gating)

Report mode is the default so the audit surfaces a worklist without blocking
merges (existing tracked defects are expected). Promote to --strict per-check
once the corresponding tracking issues are closed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit  # noqa: E402
import check_temporal_registration  # noqa: E402
import check_view_security_invoker  # noqa: E402
import check_workflow_security  # noqa: E402

CHECKS = [
    check_temporal_registration.run,
    check_workflow_security.run,
    check_view_security_invoker.run,
]


def main() -> int:
    strict = "--strict" in sys.argv[1:]
    results = [check() for check in CHECKS]
    emit(results)
    total = sum(len(r.findings) for r in results)
    if total:
        print(f"\nArchitecture audit: {total} finding(s).", file=sys.stderr)
    return 1 if (strict and total) else 0


if __name__ == "__main__":
    raise SystemExit(main())
