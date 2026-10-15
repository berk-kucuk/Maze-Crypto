"""The only place that talks to the network.

* Host allow-list: a request to any host not registered by a provider is
  refused before a socket is opened. No telemetry, no surprise endpoints.
* Optional proxy (SOCKS5/Tor or HTTP) for every request.
* Per-host minimum spacing between requests and bounded retries, honouring
  ``Retry-After`` on HTTP 429.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from mazecrypto import __version__

USER_AGENT = f"maze-crypto/{__version__}"
DOWN_SECONDS = 20.0


class NetError(Exception):
    """Any failure to get a usable response."""


class HostNotAllowed(NetError):
    pass


class RateLimited(NetError):
    pass


class Http:
    def __init__(self, allowed_hosts: dict[str, float], proxy: str | None = None, timeout: float = 10.0,
                 retries: int = 2, transport: httpx.AsyncBaseTransport | None = None):
        """``allowed_hosts`` maps host -> minimum seconds between two requests to it."""
        self.allowed = dict(allowed_hosts)
        self.proxy = proxy
        self.retries = retries
        self._client = httpx.AsyncClient(
            proxy=None if transport else proxy, transport=transport,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 8.0)),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/xml;q=0.9"},
            follow_redirects=False, http2=False)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._down_until: dict[str, float] = {}
        self.requests = 0

    async def close(self) -> None:
        await self._client.aclose()

    def _host(self, url: str) -> str:
        u = urlparse(url)
        if u.scheme != "https" or not u.hostname or u.hostname not in self.allowed:
            raise HostNotAllowed(f"blocked request to {u.hostname or url}")
        return u.hostname

    async def _space(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last.get(host, 0.0) + self.allowed[host] - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        host = self._host(url)
        if self._down_until.get(host, 0.0) > time.monotonic():
            # Recently unreachable: fail fast so offline mode shows cached data at once.
            raise NetError(f"{host}: unreachable")
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            await self._space(host)
            try:
                self.requests += 1
                r = await self._client.get(url, params=params)
            except (httpx.ConnectError, httpx.ProxyError, httpx.UnsupportedProtocol) as e:
                # Refused / no route / DNS / dead proxy: retrying right away will not help.
                self._down_until[host] = time.monotonic() + DOWN_SECONDS
                raise NetError(f"{host}: {type(e).__name__}") from e
            except httpx.HTTPError as e:      # timeouts, dropped connections: worth a retry
                last_exc = e
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 2.0 * (attempt + 1)
                if delay > 10 or attempt == self.retries:
                    raise RateLimited(f"{host}: rate limited")
                await asyncio.sleep(delay)
                continue
            if r.status_code >= 500:
                last_exc = NetError(f"{host}: HTTP {r.status_code}")
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise NetError(f"{host}: HTTP {r.status_code}")
            return r
        raise NetError(f"{host}: {type(last_exc).__name__ if last_exc else 'failed'}") from last_exc

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        r = await self.get(url, params)
        if len(r.content) > 16 * 1024 * 1024:
            raise NetError("response too large")
        try:
            return json.loads(r.content)
        except ValueError as e:
            raise NetError(f"{urlparse(url).hostname}: invalid JSON") from e

    async def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        r = await self.get(url, params)
        if len(r.content) > 4 * 1024 * 1024:
            raise NetError("response too large")
        return r.text
