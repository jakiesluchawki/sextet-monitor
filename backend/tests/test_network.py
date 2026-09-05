import httpx
import pytest
from monitor.network import FetchError, RateLimited, SafeHTTPClient, validate_url


@pytest.mark.parametrize("url", [
    "http://earthquake.usgs.gov/earthquakes/feed/x",
    "https://127.0.0.1/x",
    "https://earthquake.usgs.gov.evil.test/earthquakes/feed/x",
    "https://user:pass@earthquake.usgs.gov/earthquakes/feed/x",
    "https://api.cloudflare.com/client/v4/accounts",
    "https://feeds.meteoalarm.org/api/v1/warnings/%2e%2e/private",
    "https://moje.cert.pl/komunikaty/2026/1/example/",
    "https://moje.cert.pl/accounts/login/",
    "https://hydro-back.imgw.pl/station/details",
    "https://hydro-back.imgw.pl.evil.test/alerts/warnings/hydro/getCurrentWarnings",
])
def test_untrusted_provider_urls_are_rejected(url):
    with pytest.raises(FetchError):
        validate_url(url)


@pytest.mark.parametrize("url,host", [
    ("https://moje.cert.pl/advisory_feed/advisory/feed/?category=1", "moje.cert.pl"),
    ("https://hydro-back.imgw.pl/alerts/warnings/hydro/getCurrentWarnings", "hydro-back.imgw.pl"),
])
def test_polish_collectors_use_only_the_reviewed_official_endpoints(url, host):
    assert validate_url(url) == host


async def test_conditional_fetch_preserves_body():
    count = 0
    def respond(request):
        nonlocal count
        count += 1
        if count == 2:
            assert request.headers["if-none-match"] == '"v1"'
            return httpx.Response(304)
        return httpx.Response(200, content=b'{"features":[]}', headers={"etag": '"v1"'})
    async with SafeHTTPClient(transport=httpx.MockTransport(respond), validate_dns=False) as client:
        first = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")
        second = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")
    assert second.body == first.body
    assert second.not_modified


async def test_redirect_cannot_reach_private_host():
    def respond(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/secrets"})
    async with SafeHTTPClient(transport=httpx.MockTransport(respond), validate_dns=False) as client:
        with pytest.raises(FetchError):
            await client.get("https://www.gdacs.org/xml/rss.xml")


async def test_rate_limit_is_not_empty_success():
    def respond(request):
        return httpx.Response(429, headers={"retry-after": "600"})
    async with SafeHTTPClient(transport=httpx.MockTransport(respond), validate_dns=False) as client:
        with pytest.raises(RateLimited) as error:
            await client.get("https://www.gdacs.org/xml/rss.xml")
    assert error.value.retry_after_seconds == 600


async def test_oversize_response_rejected_before_buffering():
    def respond(request):
        return httpx.Response(200, content=b"x", headers={"content-length": "20000000"})
    async with SafeHTTPClient(transport=httpx.MockTransport(respond), validate_dns=False) as client:
        with pytest.raises(FetchError):
            await client.get("https://www.gdacs.org/xml/rss.xml")
