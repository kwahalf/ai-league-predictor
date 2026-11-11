"""Ingest activities: all of the ingest's I/O.

- ``football_fetch_csv`` downloads one file from football-data.co.uk and stores the raw bytes in the
  private ``football-raw`` Storage bucket under a path that contains their SHA-256, so downloading
  identical content again stores nothing new.
- ``football_load_csv`` reads that stored file back, parses it with the 1.2 parser, cleans the odds
  (``payload.py``) and upserts teams, matches, stats and odds in one call to the
  ``football_ingest_matches`` RPC, which only writes rows whose values actually changed.

Both are synchronous (blocking HTTP), so the worker runs them on its thread pool. They're built
by ``build_activities`` so tests can inject a fake gateway, like the agent activities.
"""

from __future__ import annotations

import hashlib
from dataclasses import fields
from datetime import datetime, timezone
from typing import Callable

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..data import CsvFormatError, UnknownTeamError, parse_fixtures_csv, parse_season_csv
from .gateway import Gateway, HttpGateway, fixtures_url, season_url
from .models import (
    FETCH_ACTIVITY,
    FIXTURES_KIND,
    LOAD_ACTIVITY,
    SEASON_KIND,
    CsvSource,
    LoadResult,
    RawCsvRef,
    validate_season,
)
from .payload import DuplicateMatchError, build_payload

INGEST_RPC = "football_ingest_matches"


def storage_path(source: CsvSource, sha256: str) -> str:
    """``E0/2324/<sha256>.csv`` for a season file, ``fixtures/<sha256>.csv`` for fixtures.csv."""
    if source.kind == SEASON_KIND:
        return f"{source.division}/{source.season}/{sha256}.csv"
    return f"fixtures/{sha256}.csv"


def source_url(source: CsvSource) -> str:
    if source.kind == SEASON_KIND:
        return season_url(source.division, source.season)
    if source.kind == FIXTURES_KIND:
        return fixtures_url()
    raise ApplicationError(f"Unknown CSV kind {source.kind!r}", type="BadRequest", non_retryable=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _log(message: str) -> None:
    # One line per event (AGENTS.md logging rule). The activity logger adds workflow context.
    if activity.in_activity():
        activity.logger.info(message)


def fetch_csv(gateway: Gateway, source: CsvSource, now: Callable[[], datetime] = _utc_now) -> RawCsvRef:
    _check_source(source)
    url = source_url(source)
    data = gateway.download(url)
    downloaded_at = now().astimezone(timezone.utc).isoformat()
    if not data.strip():
        raise ApplicationError(f"Empty file from {url}", type="DownloadError")
    sha256 = hashlib.sha256(data).hexdigest()
    path = storage_path(source, sha256)
    stored = gateway.put_object(path, data)
    _log(
        f"football ingest fetched url={url} bytes={len(data)} sha256={sha256} "
        f"storage_path={path} new_object={stored}"
    )
    return RawCsvRef(
        kind=source.kind,
        season=source.season,
        division=source.division,
        url=url,
        storage_path=path,
        sha256=sha256,
        size_bytes=len(data),
        downloaded_at=downloaded_at,
    )


def load_csv(gateway: Gateway, ref: RawCsvRef) -> LoadResult:
    _check_source(CsvSource(ref.kind, ref.season, ref.division))
    data = gateway.get_object(ref.storage_path)
    if hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ApplicationError(
            f"Stored file {ref.storage_path} does not match its hash", type="StorageError", non_retryable=True
        )

    fixtures = ref.kind == FIXTURES_KIND
    parse = parse_fixtures_csv if fixtures else parse_season_csv
    try:
        records = parse(data, ref.season, division=ref.division)
        payload = build_payload(records, include_stats=not fixtures)
    except (CsvFormatError, UnknownTeamError, DuplicateMatchError) as exc:
        # Bad data doesn't improve on retry: fail the workflow so someone looks at it.
        raise ApplicationError(f"{ref.url}: {exc}", type=type(exc).__name__, non_retryable=True) from exc

    counts = gateway.rpc(
        INGEST_RPC,
        {
            "p_division": ref.division,
            "p_kind": ref.kind,
            "p_source_key": "fixtures.csv" if fixtures else f"{ref.division}/{ref.season}",
            "p_captured_at": ref.downloaded_at,
            "p_teams": payload.teams,
            "p_matches": payload.matches,
        },
    ) or {}
    known = {f.name for f in fields(LoadResult)}
    result = LoadResult(
        kind=ref.kind,
        season=ref.season,
        storage_path=ref.storage_path,
        matches_in_file=len(payload.matches),
        prices_dropped=payload.prices_dropped,
        price_sets_nulled=payload.price_sets_nulled,
        **{k: int(v) for k, v in counts.items() if k in known and k not in _LOCAL_COUNTS},
    )
    if result.stale_skipped:
        _log(
            f"football ingest skipped stale download file={ref.url} downloaded_at={ref.downloaded_at}: "
            "a newer download of this file was already loaded"
        )
    if result.prices_dropped or result.price_sets_nulled:
        _log(
            f"football ingest dropped prices<=1.0 file={ref.url} storage_path={ref.storage_path} "
            f"prices_dropped={result.prices_dropped} price_sets_nulled={result.price_sets_nulled}"
        )
    _log(
        "football ingest loaded "
        + " ".join(f"{f.name}={getattr(result, f.name)}" for f in fields(LoadResult))
    )
    return result


_LOCAL_COUNTS = {"kind", "season", "storage_path", "matches_in_file", "prices_dropped", "price_sets_nulled"}


def _check_source(source: CsvSource) -> None:
    if source.kind not in (SEASON_KIND, FIXTURES_KIND):
        raise ApplicationError(f"Unknown CSV kind {source.kind!r}", type="BadRequest", non_retryable=True)
    try:
        validate_season(source.season)
    except ValueError as exc:
        raise ApplicationError(str(exc), type="BadRequest", non_retryable=True) from exc
    if not source.division.isalnum():
        raise ApplicationError(f"Bad division {source.division!r}", type="BadRequest", non_retryable=True)


def build_activities(gateway: Gateway | None = None) -> list:
    """The ingest activities, bound to ``gateway`` (default: the real Supabase/HTTP one)."""

    def _gateway() -> Gateway:
        if gateway is not None:
            return gateway
        from ...config import settings

        return HttpGateway(settings.supabase_url, settings.supabase_service_role_key)

    @activity.defn(name=FETCH_ACTIVITY)
    def football_fetch_csv(source: CsvSource) -> RawCsvRef:
        return fetch_csv(_gateway(), source)

    @activity.defn(name=LOAD_ACTIVITY)
    def football_load_csv(ref: RawCsvRef) -> LoadResult:
        return load_csv(_gateway(), ref)

    return [football_fetch_csv, football_load_csv]
