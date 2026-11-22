"""HTTP plumbing shared by price providers, discovery and notifications.

Tracking thousands of funds means thousands of requests per refresh, and free
APIs (Yahoo above all) answer bursts with 429s. Every outbound request goes
through ``RetryTransport``, which

* caps concurrent requests per host (``PER_HOST_CONCURRENCY``),
* retries connection errors, timeouts, 429 and 5xx with exponential backoff,
  honouring ``Retry-After`` (capped at 30 s).
"""
from __future__ import annotations

import asyncio
import logging
import random

import httpx

from .config import settings

log = logging.getLogger("etfme.net")
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError,
                httpx.RemoteProtocolError, httpx.PoolTimeout)


class RetryTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None, retries: int | None = None,
                 backoff: float = 1.0, per_host: int | None = None):
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.retries = settings.http_retries if retries is None else retries
        self.backoff = backoff
        self.per_host = per_host or settings.per_host_concurrency
        self._sems: dict[str, asyncio.Semaphore] = {}

    def _sem(self, host: str) -> asyncio.Semaphore:
        if host not in self._sems:
            self._sems[host] = asyncio.Semaphore(self.per_host)
        return self._sems[host]

    def _delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(30.0, self.backoff * 2 ** attempt) * (0.75 + random.random() / 2)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self.retries + 1):
            async with self._sem(request.url.host):
                try:
                    resp = await self.inner.handle_async_request(request)
                except RETRY_ERRORS as e:
                    if attempt >= self.retries:
                        raise
                    log.debug("%s %s: %s, retrying", request.method, request.url.host, type(e).__name__)
                    delay = self._delay(attempt)
                else:
                    if resp.status_code not in RETRY_STATUS or attempt >= self.retries:
                        return resp
                    delay = self._delay(attempt, resp.headers.get("retry-after"))
                    await resp.aclose()
                    log.debug("%s %s: HTTP %s, retrying in %.1fs", request.method, request.url.host,
                              resp.status_code, delay)
            await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self.inner.aclose()


def client(timeout: float = 30, **kw) -> httpx.AsyncClient:
    """The AsyncClient every outbound call should use."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=RetryTransport(), **kw)
