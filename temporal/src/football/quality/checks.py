"""The data-quality rules, their thresholds, and the messages. Pure: no I/O.

``evaluate`` judges the facts returned by the ``football_quality_check`` RPC and returns one
``CheckResult`` per check. Each failing check's message names the offending rows, the first
``SAMPLE`` of them, so the Temporal UI shows what to look at.

The checks:

- ``completed_seasons``: every completed season (every season before the current one, from
  ``FIRST_SEASON``, plus any other stored season that starts earlier) has exactly 380 matches,
  20 teams, 19 home and 19 away matches per team (38 in all), each ordered home/away pair once,
  and every match finished.
- ``current_season``: the current season has at most 20 teams and no team with more than 19 home
  or 19 away matches. It may be partial.
- ``duplicate_fixtures``: no two matches with the same season, home and away team, and no team
  in two matches on the same UK date, across all seasons (which also catches the same teams on
  the same date stored under two seasons).
- ``scores_match_status``: scores only on finished matches, and every finished match has both.
- ``odds_above_one``: no stored price is 1.0 or less (the ingest stores those as missing; this
  confirms none reached the database).
- ``odds_margins``: each complete 1X2 set, and separately each complete over/under 2.5 set, has a
  margin (sum of 1/price, minus 1) within ``MARGIN_LIMITS`` for its market, apart from the source
  errors listed in ``KNOWN_MARGIN_EXCEPTIONS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import CheckResult, QualityReport

# Teams per division: 20 for the Premier League, so 380 matches and 38 per team.
TEAMS_PER_SEASON = {"E0": 20}

# Margin limits per market, as fractions: 0.15 is 15%. Measured on the 2026-11-11 backfill (every
# bookmaker and snapshot, 1617..2627): 1X2 0.05%..12.75%, over/under 0.19%..8.46%, apart from the
# one source error below. Below 0 is an arbitrage the source never offers; above 15% is far beyond
# any EPL book in the data.
MARGIN_LIMITS: dict[str, tuple[float, float]] = {
    "1x2": (0.0, 0.15),
    "over_under_2_5": (0.0, 0.15),
}

# Offending rows shown per failing check.
SAMPLE = 10


@dataclass(frozen=True)
class KnownMarginException:
    """A stored price set outside the margin limits that is a known error in the source file.

    Matched on the exact prices: if the source corrects the file, the exception simply stops
    matching; if the prices change to other bad values, the check fails again.
    """

    season: str
    home: str
    away: str
    bookmaker: str
    snapshot: str
    market: str
    prices: tuple[float, ...]
    reason: str

    def matches(self, row: dict[str, Any]) -> bool:
        return (
            row.get("season") == self.season
            and row.get("home") == self.home
            and row.get("away") == self.away
            and row.get("bookmaker") == self.bookmaker
            and row.get("snapshot") == self.snapshot
            and row.get("market") == self.market
            and tuple(round(float(p), 3) for p in row.get("prices") or ()) == self.prices
        )

    def describe(self) -> str:
        return (
            f"{self.season} {self.home} v {self.away} {self.bookmaker} {self.snapshot} "
            f"{_market_label(self.market)} {_prices(self.prices)}: {self.reason}"
        )


KNOWN_MARGIN_EXCEPTIONS: tuple[KnownMarginException, ...] = (
    # E0 2022/23, 18/02/2023: Bet365's pre-match away price is 1.50 while every other pre-match
    # and closing price for Arsenal is 1.78-1.95 (Bet365 closing 1.91), giving a 16.67% margin.
    KnownMarginException(
        season="2223",
        home="Aston Villa",
        away="Arsenal",
        bookmaker="B365",
        snapshot="pre_match",
        market="1x2",
        prices=(4.5, 3.6, 1.5),
        reason="source error in Bet365's pre-match away price (1.50; other books 1.78-1.85), margin 16.67%",
    ),
)

CHECK_NAMES = (
    "completed_seasons",
    "current_season",
    "duplicate_fixtures",
    "scores_match_status",
    "odds_above_one",
    "odds_margins",
)


def evaluate(
    facts: dict[str, Any],
    *,
    division: str,
    current_season: str,
    exceptions: tuple[KnownMarginException, ...] = KNOWN_MARGIN_EXCEPTIONS,
) -> QualityReport:
    """Judge the RPC's facts. Raises ``KeyError``/``TypeError``/``ValueError`` on a malformed reply."""
    teams = TEAMS_PER_SEASON[division]
    seasons = facts["seasons"]
    margin_result, acknowledged = _odds_margins(facts["odds_margins"], exceptions)
    totals = facts.get("totals") or {}
    return QualityReport(
        division=division,
        current_season=current_season,
        matches_checked=int(totals.get("matches", 0)),
        odds_checked=int(totals.get("odds", 0)),
        checks=[
            _completed_seasons([s for s in seasons if s["completed"]], teams),
            _current_season([s for s in seasons if not s["completed"]], teams),
            _duplicates(facts["duplicate_fixtures"]),
            _scores(facts["scores"]),
            _prices_above_one(facts["odds_prices"]),
            margin_result,
        ],
        acknowledged=acknowledged,
    )


def failure_message(report: QualityReport) -> str:
    """One message for the Temporal UI: which checks failed and their offending rows."""
    failed = report.failed
    parts = [f"{check.name}: {check.message}" for check in failed]
    return (
        f"Data-quality check failed for {report.division} (current season {report.current_season}): "
        f"{len(failed)} of {len(report.checks)} checks failed. " + " | ".join(parts)
    )


# --- individual checks -------------------------------------------------------------------------


def _completed_seasons(rows: list[dict[str, Any]], teams: int) -> CheckResult:
    problems: list[str] = []
    for row in rows:
        issues: list[str] = []
        if int(row["matches"]) != teams * (teams - 1):
            issues.append(f"{row['matches']} matches, expected {teams * (teams - 1)}")
        if int(row["teams"]) != teams:
            issues.append(f"{row['teams']} teams, expected {teams}")
        off = int(row["teams_off_count"])
        if off:
            listed = ", ".join(
                f"{t['team']} {int(t['home']) + int(t['away'])} ({t['home']} home, {t['away']} away)"
                for t in row["teams_off"][:SAMPLE]
            )
            issues.append(
                f"{off} teams without {teams - 1} home and {teams - 1} away matches: {listed}{_more(off)}"
            )
        missing = int(row["missing_pairs"])
        if missing:
            listed = ", ".join(f"{p['home']} v {p['away']}" for p in row["missing_pair_rows"][:SAMPLE])
            issues.append(f"{missing} home/away pairs with no match: {listed}{_more(missing)}")
        unfinished = int(row["unfinished"])
        if unfinished:
            listed = ", ".join(
                f"{m['date']} {m['home']} v {m['away']} ({m['status']})" for m in row["unfinished_rows"][:SAMPLE]
            )
            issues.append(f"{unfinished} matches not finished: {listed}{_more(unfinished)}")
        if issues:
            problems.append(f"season {row['season']}: " + "; ".join(issues))
    return _result("completed_seasons", problems, len(problems), "seasons")


def _current_season(rows: list[dict[str, Any]], teams: int) -> CheckResult:
    problems: list[str] = []
    for row in rows:
        issues: list[str] = []
        if int(row["matches"]) > teams * (teams - 1):
            issues.append(f"{row['matches']} matches, more than {teams * (teams - 1)}")
        if int(row["teams"]) > teams:
            issues.append(f"{row['teams']} teams, more than {teams}")
        if int(row["max_home"]) > teams - 1 or int(row["max_away"]) > teams - 1:
            issues.append(
                f"a team has {row['max_home']} home / {row['max_away']} away matches, more than {teams - 1}"
            )
        if issues:
            problems.append(f"season {row['season']}: " + "; ".join(issues))
    return _result("current_season", problems, len(problems), "seasons")


def _duplicates(block: dict[str, Any]) -> CheckResult:
    def describe(row: dict[str, Any]) -> str:
        if row["kind"] == "same_season":
            return f"season {row['season']} {row['home']} v {row['away']} stored {row['matches']} times"
        a, b = row["a"], row["b"]
        return (
            f"{row['date']}: {a['season']} {a['home']} v {a['away']} and "
            f"{b['season']} {b['home']} v {b['away']} share a team on the same day"
        )

    return _listed("duplicate_fixtures", block, describe, "duplicates")


def _scores(block: dict[str, Any]) -> CheckResult:
    def describe(row: dict[str, Any]) -> str:
        score = f"{_goal(row['home_goals'])}-{_goal(row['away_goals'])}"
        return f"{row['season']} {row['date']} {row['home']} v {row['away']}: status {row['status']}, score {score}"

    return _listed("scores_match_status", block, describe, "matches")


def _prices_above_one(block: dict[str, Any]) -> CheckResult:
    def describe(row: dict[str, Any]) -> str:
        bad = ", ".join(
            f"{name}={value}"
            for name, value in row["prices"].items()
            if value is not None and float(value) <= 1.0
        )
        return f"{row['season']} {row['date']} {row['home']} v {row['away']} {row['bookmaker']} {row['snapshot']}: {bad}"

    return _listed("odds_above_one", block, describe, "odds rows")


def _odds_margins(
    block: dict[str, Any], exceptions: tuple[KnownMarginException, ...]
) -> tuple[CheckResult, list[str]]:
    rows = block["rows"]
    known = [row for row in rows if any(e.matches(row) for e in exceptions)]
    acknowledged = [e.describe() for e in exceptions if any(e.matches(row) for row in known)]
    unknown = [row for row in rows if row not in known]
    # The RPC caps rows but counts all of them; the acknowledged ones are among the rows returned.
    count = int(block["count"]) - len(known)
    limits = ", ".join(
        f"{_market_label(market)} {lo * 100:g}-{hi * 100:g}%" for market, (lo, hi) in MARGIN_LIMITS.items()
    )

    def describe(row: dict[str, Any]) -> str:
        return (
            f"{row['season']} {row['date']} {row['home']} v {row['away']} {row['bookmaker']} {row['snapshot']} "
            f"{_market_label(row['market'])} {_prices(row['prices'])}: margin {float(row['margin']) * 100:.2f}%"
        )

    result = _listed("odds_margins", {"count": count, "rows": unknown}, describe, f"price sets outside {limits}")
    return result, acknowledged


# --- helpers -----------------------------------------------------------------------------------


def _listed(name: str, block: dict[str, Any], describe, noun: str) -> CheckResult:
    count = int(block["count"])
    if count <= 0:
        return CheckResult(name=name, passed=True)
    shown = [describe(row) for row in block["rows"][:SAMPLE]]
    return CheckResult(
        name=name,
        passed=False,
        count=count,
        message=f"{count} {noun}: " + "; ".join(shown) + _more(count),
    )


def _result(name: str, problems: list[str], count: int, noun: str) -> CheckResult:
    if not problems:
        return CheckResult(name=name, passed=True)
    return CheckResult(
        name=name,
        passed=False,
        count=count,
        message=f"{count} {noun}: " + " / ".join(problems[:SAMPLE]) + _more(count),
    )


def _more(count: int) -> str:
    return f" (first {SAMPLE} of {count})" if count > SAMPLE else ""


def _goal(value: Any) -> str:
    return "?" if value is None else str(value)


def _prices(prices) -> str:
    return "/".join(f"{float(p):g}" for p in prices)


def _market_label(market: str) -> str:
    return {"1x2": "1X2", "over_under_2_5": "over/under 2.5"}.get(market, market)
