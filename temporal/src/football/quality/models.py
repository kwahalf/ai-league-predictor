"""Input and output of the data-quality check activity.

Plain dataclasses only: the workflow imports this module, so nothing here may do I/O or read the
clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

QUALITY_ACTIVITY = "football_quality_check"
# The ApplicationError type a failed check raises, so callers can tell it from an outage.
QUALITY_ERROR_TYPE = "DataQualityError"


@dataclass
class QualityRequest:
    """Check one league's data.

    ``current_season`` is the season running on the workflow's date (``season_for``, which rolls
    over on 1 July). Every season before it is **completed** and must be whole; the current season
    may still be partial.
    """

    current_season: str
    division: str = "E0"


@dataclass
class CheckResult:
    """One named check: whether it passed, how many offending rows, and a readable message."""

    name: str
    passed: bool
    count: int = 0
    message: str = ""


@dataclass
class QualityReport:
    """Every check's result, plus what was looked at. Returned only when all checks pass."""

    division: str
    current_season: str
    matches_checked: int = 0
    odds_checked: int = 0
    checks: list[CheckResult] = field(default_factory=list)
    # Known source errors that would otherwise fail a check (see ``checks.KNOWN_MARGIN_EXCEPTIONS``).
    acknowledged: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]
