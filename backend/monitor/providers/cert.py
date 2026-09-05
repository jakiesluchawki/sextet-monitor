"""Facts-and-links index of CERT Polska's public RSS for end users.

RSS access is not an open licence to the articles. Original titles, descriptions,
HTML and the feed body are deliberately excluded from normalized records/raw.
NASK retains rights to the source content: https://moje.cert.pl/terms/.
Publication time is not incident time; the RSS supplies no affected country,
severity, validity period, or reliable current/expired state.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import ProviderError, metadata, reject, retry_after, timestamp, warn, xml_document

URL = "https://moje.cert.pl/advisory_feed/advisory/feed/?category=1"
SOURCE_NAME = "CERT Polska"
USER_CATEGORY = "Dla użytkowników"
MAX_FEED_BYTES = 512 * 1024
MAX_ITEMS = 10
_ARTICLE_PATH = re.compile(r"/komunikaty/(20\d{2})/([1-9]\d{0,5})/([a-z0-9][a-z0-9-]{0,249})/")


def _article(value: str | None) -> tuple[str, str]:
    """Accept only the publisher's observed HTTPS article URL format."""
    if not isinstance(value, str) or len(value) > 800 or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ValueError("invalid article link")
    parts = urlsplit(value)
    if (
        parts.scheme != "https" or parts.netloc != "moje.cert.pl" or
        parts.query or parts.fragment
    ):
        raise ValueError("article outside publisher")
    match = _ARTICLE_PATH.fullmatch(parts.path)
    if match is None:
        raise ValueError("invalid article identity")
    return f"{match[1]}/{match[2]}", value


def _field(element, name: str) -> str | None:
    """Do not silently resolve ambiguous or nested scalar RSS fields."""
    fields = element.findall(name)
    if len(fields) > 1 or (fields and len(fields[0])):
        raise ValueError("invalid scalar RSS field")
    return fields[0].text if fields else None


def parse(doc: FetchedDocument) -> ProviderBatch:
    if len(doc.body) > MAX_FEED_BYTES:
        raise ProviderError("CERT Polska: RSS przekracza limit 512 KiB")
    root = xml_document(doc, SOURCE_NAME, "rss")
    channels = root.findall("channel")
    if root.get("version") != "2.0" or len(channels) != 1:
        raise ProviderError("CERT Polska: oczekiwano jednego kanału RSS 2.0")
    channel = channels[0]
    try:
        channel_link = _field(channel, "link")
        channel_title = _field(channel, "title")
        if channel_link not in (URL, URL.split("?")[0]) or not channel_title:
            raise ValueError("invalid channel metadata")
    except ValueError:
        raise ProviderError("CERT Polska: nieprawidłowe metadane kanału RSS") from None

    records = channel.findall("item")
    batch = ProviderBatch(events=[], metadata=metadata(
        doc, len(records), rss_window_limit=MAX_ITEMS, excluded_categories=0,
        excluded_by_limit=max(0, len(records) - MAX_ITEMS),
        publication_mode="facts_and_links_only", source_content_republished=False,
    ))
    if len(records) > MAX_ITEMS:
        warn(batch.warnings, "CERT Polska: RSS zawiera ponad 10 pozycji; przetworzono pierwszych 10 w kolejności wydawcy.")
    try:
        last_build_value = _field(channel, "lastBuildDate")
    except ValueError:
        last_build_value = None
        warn(batch.warnings, "CERT Polska: niejednoznaczne lastBuildDate; pozostawiono nieznane.")
    built, _ = timestamp(last_build_value, warnings=batch.warnings, field="CERT RSS lastBuildDate")
    # In this feed lastBuildDate is the newest publication, not the fetch clock.
    batch.metadata["feed_last_build_at"] = built.isoformat() if built else None
    fetched, _ = timestamp(doc.fetched_at)

    seen: set[str] = set()
    for index, item in enumerate(records[:MAX_ITEMS]):
        try:
            categories = item.findall("category")
            if not categories or any(len(category) or not category.text for category in categories):
                raise ValueError("missing or invalid category")
            if not any(category.text.strip() == USER_CATEGORY for category in categories):
                batch.metadata["excluded_categories"] += 1
                continue
            publication_id, source_url = _article(_field(item, "link"))
            guid = _field(item, "guid")
            if guid is not None and _article(guid)[0] != publication_id:
                raise ValueError("guid and link disagree")
            if publication_id in seen:
                raise ValueError("duplicate publication id")
            publication_value = _field(item, "pubDate")
            issued, precision = timestamp(
                publication_value, warnings=batch.warnings, field="CERT RSS pubDate",
            )
            # RFC 2822 also allows HH:MM. The common RFC parser supplies a zero
            # second, but that must not increase the source's time precision.
            if issued is not None and precision == "second" and re.search(
                r"(?:^|\s)\d{1,2}:\d{2}\s", publication_value or "",
            ):
                precision = "minute"
            if issued is None:
                warn(batch.warnings, "CERT Polska: brak poprawnego pubDate; czas publikacji pozostaje nieznany.")
            if issued is not None and fetched is not None and issued > fetched:
                warn(batch.warnings, "CERT Polska: pubDate jest późniejszy od pobrania; zachowano datę źródła bez uznania jej za czas incydentu.")
            tags = ["publication_index", "end_user_notice", "no_incident_location", "validity_unknown"]
            event = NormalizedEvent(
                source_id="cert_pl", provider_record_id=publication_id,
                kind="advisory", category="cyber",
                title=f"Komunikat CERT Polska {publication_id} dla użytkowników",
                description=(
                    "CERT Polska opublikował komunikat dla użytkowników. "
                    "Treść i zalecenia są dostępne u wydawcy pod odsyłaczem. "
                    "RSS nie określa miejsca incydentu ani terminu ważności komunikatu."
                ),
                source_url=source_url, issued_at=issued, time_precision=precision,
                occurred_start=None, occurred_end=None, source_updated_at=None,
                valid_from=None, valid_to=None, lifecycle_status="unknown",
                countries=[], geometry=None, location_precision="unknown",
                severity=0, original_severity=None,
                severity_reason="RSS nie zawiera skali dotkliwości ani danych o skutkach konkretnego incydentu.",
                verification_status="published_by_cert_pl", origins=["cert_pl"],
                external_ids=[f"cert_pl:{publication_id}"], tags=tags,
                raw={
                    "publication_id": publication_id, "audience": "users",
                    "published_at": issued.isoformat() if issued else None,
                    "time_precision": precision,
                },
            )
            batch.events.append(event)
            seen.add(publication_id)
        except (ValueError, TypeError, KeyError) as exc:
            reject(batch, SOURCE_NAME, index, exc)
    if batch.metadata["excluded_categories"]:
        warn(batch.warnings, "CERT Polska: odfiltrowano inne kategorie mimo żądania kanału dla użytkowników.")
    batch.events.sort(key=lambda event: (
        event.issued_at or datetime.min.replace(tzinfo=timezone.utc), event.provider_record_id,
    ), reverse=True)
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    # No per-item requests, accounts, configurable hosts, or historical crawl.
    try:
        doc = await fetcher.get(URL, headers={"Accept": "application/rss+xml, application/xml"})
    except Exception as exc:
        raise ProviderError(
            f"CERT Polska: nie udało się pobrać RSS ({type(exc).__name__})",
            retry_after_seconds=retry_after(exc),
        ) from None
    return parse(doc)
