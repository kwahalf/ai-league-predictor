"""Network I/O for the ingest: football-data.co.uk downloads, Supabase Storage and the RPC.

Standard library only (``urllib``), like ``src/activities/client.py``, but with longer timeouts:
a whole season goes to the database in one call, and season files are a few hundred KB.

Errors become Temporal ``ApplicationError``s. Client errors that won't fix themselves (404 for a
season file that doesn't exist, 400 from the RPC for bad data) are non-retryable, so the workflow
fails visibly instead of retrying for hours; everything else is retried by the activity's policy.
Messages never include credentials or response bodies from the database.
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from temporalio.exceptions import ApplicationError

SOURCE_BASE_URL = "https://www.football-data.co.uk"
RAW_BUCKET = "football-raw"
USER_AGENT = "ai-league-predictor-ingest/1.0 (+https://github.com/kwahalf/ai-league-predictor)"
# A season file is about 200 KB and fixtures.csv about 50 KB; anything far bigger is not a CSV.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60
RPC_TIMEOUT_S = 120


def season_url(division: str, season: str) -> str:
    return f"{SOURCE_BASE_URL}/mmz4281/{season}/{division}.csv"


def fixtures_url() -> str:
    return f"{SOURCE_BASE_URL}/fixtures.csv"


class Gateway(Protocol):
    """What the activities need from the outside world. Tests pass a fake."""

    def download(self, url: str) -> bytes: ...

    def put_object(self, path: str, data: bytes) -> bool:
        """Store ``data`` at ``path`` in the raw bucket. False if it already existed (not replaced)."""
        ...

    def get_object(self, path: str) -> bytes: ...

    def rpc(self, function: str, args: dict[str, Any]) -> Any: ...


def _retryable(code: int) -> bool:
    return code >= 500 or code in (408, 425, 429)


class HttpGateway:
    """The real gateway: football-data.co.uk over HTTPS, Supabase over its REST and Storage APIs."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        self._base = supabase_url.rstrip("/")
        self._key = service_role_key

    def _auth(self) -> dict[str, str]:
        return {"apikey": self._key, "Authorization": "Bearer " + self._key}

    def download(self, url: str) -> bytes:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as response:
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
        except HTTPError as exc:
            raise ApplicationError(
                f"Download failed: HTTP {exc.code} for {url}",
                type="DownloadError",
                non_retryable=not _retryable(exc.code),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApplicationError(f"Download failed for {url}: {exc}", type="DownloadError") from exc
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ApplicationError(
                f"Download from {url} is larger than {MAX_DOWNLOAD_BYTES} bytes",
                type="DownloadError",
                non_retryable=True,
            )
        return data

    def put_object(self, path: str, data: bytes) -> bool:
        req = Request(
            f"{self._base}/storage/v1/object/{RAW_BUCKET}/{quote(path)}",
            data=data,
            method="POST",
            # x-upsert false: never overwrite. The path holds the content hash, so an existing
            # object already has these exact bytes.
            headers={**self._auth(), "Content-Type": "text/csv", "x-upsert": "false"},
        )
        try:
            with urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as response:
                response.read()
            return True
        except HTTPError as exc:
            if _is_duplicate(exc):
                return False
            raise ApplicationError(
                f"Storage upload failed (HTTP {exc.code}) for {path}",
                type="StorageError",
                non_retryable=not _retryable(exc.code),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApplicationError(f"Storage upload failed for {path}: {exc}", type="StorageError") from exc

    def get_object(self, path: str) -> bytes:
        req = Request(
            f"{self._base}/storage/v1/object/authenticated/{RAW_BUCKET}/{quote(path)}",
            headers=self._auth(),
        )
        try:
            with urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as response:
                return response.read(MAX_DOWNLOAD_BYTES + 1)
        except HTTPError as exc:
            raise ApplicationError(
                f"Storage download failed (HTTP {exc.code}) for {path}",
                type="StorageError",
                non_retryable=not _retryable(exc.code),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApplicationError(f"Storage download failed for {path}: {exc}", type="StorageError") from exc

    def rpc(self, function: str, args: dict[str, Any]) -> Any:
        req = Request(
            f"{self._base}/rest/v1/rpc/{function}",
            data=json.dumps(args).encode(),
            method="POST",
            headers={**self._auth(), "Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=RPC_TIMEOUT_S) as response:
                body = response.read()
        except HTTPError as exc:
            raise ApplicationError(
                f"Supabase RPC {function} failed (HTTP {exc.code})",
                type="RpcError",
                non_retryable=not _retryable(exc.code),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApplicationError(f"Supabase RPC {function} failed: {exc}", type="RpcError") from exc
        return json.loads(body) if body else None


def _is_duplicate(exc: HTTPError) -> bool:
    """Storage reports an existing object as HTTP 409, or as 400 with ``statusCode: "409"``."""
    if exc.code == 409:
        return True
    if exc.code != 400:
        return False
    try:
        body = json.loads(exc.read() or b"{}")
    except (ValueError, OSError):
        return False
    return str(body.get("statusCode")) == "409" or body.get("error") == "Duplicate"
