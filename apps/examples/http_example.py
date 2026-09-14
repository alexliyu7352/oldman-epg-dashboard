"""Real upstream diagnostics using the framework's shared HTTP client API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

import httpx

from apps.examples.apps import app
from oldman.contrib.http import (
    ClientType,
    HttpContentDecodingError,
    HttpMethod,
    HttpStatusError,
    MultiHttpClient,
)

REQUEST_TIMEOUT = 5
STREAM_LIMIT = 1024 * 1024
OPERATION_PATHS = {
    "json": "/get",
    "status": "/status/404",
    "timeout": "/delay/10",
    "stream": "/stream-bytes/65536",
}
# WebService initializes/closes this per-worker client, never per browser request.
# No browser identity or upstream login credentials belong to this shared pool.
http_client = MultiHttpClient(
    client_type=ClientType.HTTPX,
    retry_count=0,
    max_connections=8,
    user_agent="Oldman-EPG-HTTP-Example",
    content_decoding=True,
    verify=True,
)


@dataclass
class HTTPResult:
    """Observed upstream outcome, separate from the Demo response's HTTP status."""

    path: str
    outcome: Literal["success", "http_error", "timeout", "connection_error", "invalid_content", "too_large"] = "success"
    status_code: int | None = None
    content_type: str = ""
    elapsed_ms: float = 0
    json_text: str | None = None
    byte_count: int | None = None
    sha256: str = ""


async def run_http_example(operation: str) -> HTTPResult:
    """Return known network failures as diagnostics; programming errors propagate."""
    path = OPERATION_PATHS[operation]
    url = f"{str(app.settings.http_base_url).rstrip('/')}{path}"
    result = HTTPResult(path=path)
    started = perf_counter()
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            if operation == "stream":
                async with http_client.stream(
                    HttpMethod.GET, url, timeout=REQUEST_TIMEOUT, follow_redirects=False,
                ) as response:
                    result.status_code = response.status_code
                    result.content_type = response.headers.get("content-type", "")
                    response.raise_for_status()
                    if not response.is_success:  # raise_for_status does not reject 3xx.
                        result.outcome = "http_error"
                        return result
                    digest = hashlib.sha256()
                    result.byte_count = 0
                    async for chunk in response.aiter_bytes(4096):
                        result.byte_count += len(chunk)
                        if result.byte_count > STREAM_LIMIT:
                            result.outcome = "too_large"
                            return result  # Exiting the context closes the response.
                        digest.update(chunk)
                    result.sha256 = digest.hexdigest()
            else:
                response = await http_client.get(
                    url, timeout=REQUEST_TIMEOUT, follow_redirects=False,
                    params={"example": "oldman-epg-dashboard"} if operation == "json" else None,
                )
                result.status_code = response.status_code
                result.content_type = response.headers.get("content-type", "")
                response.raise_for_status()
                if not response.is_success:
                    result.outcome = "http_error"
                else:
                    result.json_text = json.dumps(response.json(), ensure_ascii=False, indent=2)
    except HttpStatusError:
        result.outcome = "http_error"
    except (TimeoutError, httpx.TimeoutException):
        result.outcome = "timeout"
    except httpx.RequestError:
        result.outcome = "connection_error"
    except (HttpContentDecodingError, json.JSONDecodeError, UnicodeDecodeError):
        result.outcome = "invalid_content"
    finally:
        result.elapsed_ms = round((perf_counter() - started) * 1000, 1)
    return result
