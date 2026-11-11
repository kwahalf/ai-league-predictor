"""Small synchronous REST client for thread-pool activities."""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid4, uuid5

from temporalio import activity
from temporalio.exceptions import ApplicationError
from ..config import settings


def operation_id(kind: str) -> str:
    """Stable across retries, distinct across workflow runs and activities."""
    if activity.in_activity():
        info = activity.info()
        return str(uuid5(NAMESPACE_URL, f'{info.workflow_run_id}/{info.activity_id}/{kind}'))
    return str(uuid4())


def request(path: str, data=None, method: str = 'POST', prefer: str = 'return=representation'):
    req = Request(
        settings.supabase_url.rstrip('/') + '/rest/v1/' + path,
        data=None if data is None else json.dumps(data).encode(),
        method=method,
        headers={'apikey': settings.supabase_service_role_key,
                 'Authorization': 'Bearer ' + settings.supabase_service_role_key,
                 'Content-Type': 'application/json', 'Prefer': prefer},
    )
    try:
        with urlopen(req, timeout=15) as response:
            body = response.read()
            return json.loads(body) if body else None
    except HTTPError as exc:
        # Do not put credentials or arbitrary database payloads in logs/errors.
        raise ApplicationError(
            f'Supabase request failed (HTTP {exc.code})',
            non_retryable=400 <= exc.code < 500 and exc.code not in (408, 429),
        ) from exc
