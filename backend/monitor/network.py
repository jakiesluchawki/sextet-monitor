"""Bounded, allowlisted provider HTTP. Provider documents are never instructions."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from monitor.contracts import FetchedDocument

ALLOWED_PREFIXES = {
    "earthquake.usgs.gov": ("/earthquakes/feed/", "/fdsnws/"),
    "www.gdacs.org": ("/xml/", "/gdacsapi/"),
    "gdacs.org": ("/xml/", "/gdacsapi/"),
    "feeds.meteoalarm.org": ("/feeds/", "/api/v1/warnings/"),
    "www.easa.europa.eu": ("/en/domains/air-operations/czibs",),
    "raw.githubusercontent.com": ("/cisagov/kev-data/",),
    "www.cisa.gov": ("/sites/default/files/feeds/",),
    "api.cloudflare.com": ("/client/v4/radar/",),
}


class FetchError(RuntimeError):
    pass


class RateLimited(FetchError):
    def __init__(self, seconds: int):
        self.retry_after_seconds = min(max(seconds, 60), 86400)
        super().__init__(f"Provider rate limit; retry after {self.retry_after_seconds}s")


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        raise FetchError("Only provider HTTPS URLs without credentials are allowed")
    if host not in ALLOWED_PREFIXES or not parts.path.startswith(ALLOWED_PREFIXES[host]):
        raise FetchError("Provider URL is outside the allowed paths")
    if any(segment in (".", "..") for segment in parts.path.split("/")) or "%" in parts.path:
        raise FetchError("Encoded or relative provider paths are not allowed")
    return host


def validate_addresses(host: str) -> None:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise FetchError("Provider DNS returned no addresses")
    for record in addresses:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise FetchError("Private or special provider addresses are not allowed")


def retry_seconds(value: str | None) -> int:
    if not value:
        return 300
    try:
        return max(60, int(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max(60, int((date - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError):
            return 300


class SafeHTTPClient:
    def __init__(self, *, transport=None, validate_dns: bool = True):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(25, connect=12), follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            headers={"User-Agent": "MieszkoMonitor/0.1 private-public-data-reader", "Accept": "*/*"},
            transport=transport,
        )
        self.validate_dns = validate_dns
        self.semaphore = asyncio.Semaphore(4)
        self.cache: OrderedDict[str, tuple[bytes, str, dict[str, str]]] = OrderedDict()
        self.cache_bytes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    def remember(self, key: str, body: bytes, content_type: str, headers: dict[str, str]):
        if key in self.cache:
            self.cache_bytes -= len(self.cache.pop(key)[0])
        self.cache[key] = (body, content_type, headers)
        self.cache_bytes += len(body)
        while self.cache and (len(self.cache) > 300 or self.cache_bytes > 64 * 1024 * 1024):
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= len(old[0])

    async def get(self, url: str, headers: dict[str, str] | None = None) -> FetchedDocument:
        async with self.semaphore:
            current = url
            request_headers = dict(headers or {})
            cached = self.cache.get(url)
            if cached:
                self.cache.move_to_end(url)
                request_headers.update(cached[2])
            for redirect_count in range(4):
                host = validate_url(current)
                if self.validate_dns:
                    await asyncio.to_thread(validate_addresses, host)
                try:
                    async with self.client.stream("GET", current, headers=request_headers) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if redirect_count == 3:
                                raise FetchError("Too many provider redirects")
                            target = urljoin(current, response.headers.get("location", ""))
                            target_host = validate_url(target)
                            if target_host != host:
                                request_headers = {}
                            current = target
                            continue
                        if response.status_code == 429:
                            raise RateLimited(retry_seconds(response.headers.get("retry-after")))
                        if response.status_code == 304:
                            if not cached:
                                raise FetchError("304 without a cached provider response")
                            return FetchedDocument(cached[0], cached[1], current, 200,
                                                   datetime.now(timezone.utc), True)
                        if response.status_code != 200:
                            raise FetchError(f"Provider returned HTTP {response.status_code}")
                        content_length = response.headers.get("content-length")
                        if content_length and int(content_length) > 10 * 1024 * 1024:
                            raise FetchError("Provider response is too large")
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > 10 * 1024 * 1024:
                                raise FetchError("Provider response exceeds 10 MiB")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        if not body:
                            raise FetchError("Provider returned an empty body")
                        content_type = response.headers.get("content-type", "")
                        conditional = {}
                        if response.headers.get("etag"):
                            conditional["If-None-Match"] = response.headers["etag"]
                        if response.headers.get("last-modified"):
                            conditional["If-Modified-Since"] = response.headers["last-modified"]
                        self.remember(url, body, content_type, conditional)
                        return FetchedDocument(body, content_type, current, 200, datetime.now(timezone.utc))
                except httpx.TimeoutException:
                    raise FetchError("Provider request timed out") from None
                except httpx.HTTPError:
                    raise FetchError("Provider connection failed") from None
            raise FetchError("Provider redirect failed")
