"""Data-quality check activity: all of its I/O.

``football_quality_check`` asks the database for the facts (``football_quality_check`` RPC, one
read-only call), judges them (``checks.py``), and either returns the report or raises a
non-retryable ``ApplicationError`` of type ``DataQualityError`` whose message names each failed
check and its offending rows. ``IngestWorkflow`` runs it as its last step, so a failure fails that
workflow run visibly in the Temporal UI; the data the run loaded stays committed.

A database outage is not a failed check: the gateway's errors keep their own retryable flags and
the activity's retry policy covers them.

Synchronous (blocking HTTP), so the worker runs it on its thread pool. Built by
``build_activities`` so tests can inject a fake gateway, like the ingest activities.
"""

from __future__ import annotations

from typing import Any, Protocol

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..ingest.models import FIRST_SEASON, season_range, validate_season
from .checks import KNOWN_MARGIN_EXCEPTIONS, MARGIN_LIMITS, SAMPLE, TEAMS_PER_SEASON, evaluate, failure_message
from .models import QUALITY_ACTIVITY, QUALITY_ERROR_TYPE, QualityReport, QualityRequest

QUALITY_RPC = "football_quality_check"
# Rows the RPC returns per list: enough to show SAMPLE rows after setting aside the acknowledged
# source errors, without shipping thousands of rows when something is badly wrong.
RPC_ROW_LIMIT = SAMPLE + len(KNOWN_MARGIN_EXCEPTIONS) + 10


class RpcGateway(Protocol):
    """What the activity needs from Supabase. The ingest's ``HttpGateway`` is the real one."""

    def rpc(self, function: str, args: dict[str, Any]) -> Any: ...


def _log(message: str) -> None:
    if activity.in_activity():
        activity.logger.info(message)


def expected_completed_seasons(current_season: str) -> list[str]:
    """Seasons that must be whole: ``FIRST_SEASON`` up to the one before ``current_season``."""
    validate_season(current_season)
    start = int(current_season[:2])
    previous = f"{(start - 1) % 100:02d}{start:02d}"
    try:
        return season_range(FIRST_SEASON, previous)
    except ValueError:
        # The current season is FIRST_SEASON (or earlier): nothing is complete yet.
        return []


def check_quality(gateway: RpcGateway, request: QualityRequest) -> QualityReport:
    try:
        expected = expected_completed_seasons(request.current_season)
    except ValueError as exc:
        raise ApplicationError(str(exc), type="BadRequest", non_retryable=True) from exc
    if request.division not in TEAMS_PER_SEASON:
        raise ApplicationError(
            f"No data-quality rules for division {request.division!r}", type="BadRequest", non_retryable=True
        )

    facts = gateway.rpc(
        QUALITY_RPC,
        {
            "p_division": request.division,
            "p_current_season": request.current_season,
            "p_expected_seasons": expected,
            "p_teams_per_season": TEAMS_PER_SEASON[request.division],
            "p_margin_limits": {market: {"min": lo, "max": hi} for market, (lo, hi) in MARGIN_LIMITS.items()},
            "p_limit": RPC_ROW_LIMIT,
        },
    )
    try:
        report = evaluate(facts, division=request.division, current_season=request.current_season)
    except (KeyError, TypeError, ValueError) as exc:
        raise ApplicationError(
            f"Malformed reply from {QUALITY_RPC}: {exc!r}", type="QualityCheckError", non_retryable=True
        ) from exc

    for note in report.acknowledged:
        _log(f"football quality acknowledged known source error: {note}")
    if report.failed:
        message = failure_message(report)
        _log(f"football quality FAILED {message}")
        raise ApplicationError(message, type=QUALITY_ERROR_TYPE, non_retryable=True)
    _log(
        f"football quality passed division={report.division} current_season={report.current_season} "
        f"checks={len(report.checks)} matches={report.matches_checked} odds={report.odds_checked} "
        f"acknowledged={len(report.acknowledged)}"
    )
    return report


def build_activities(gateway: RpcGateway | None = None) -> list:
    """The data-quality activity, bound to ``gateway`` (default: the real Supabase one)."""

    def _gateway() -> RpcGateway:
        if gateway is not None:
            return gateway
        from ...config import settings
        from ..ingest.gateway import HttpGateway

        return HttpGateway(settings.supabase_url, settings.supabase_service_role_key)

    @activity.defn(name=QUALITY_ACTIVITY)
    def football_quality_check(request: QualityRequest) -> QualityReport:
        return check_quality(_gateway(), request)

    return [football_quality_check]
