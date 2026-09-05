"""The CERT adapter indexes publication facts, never republishes articles."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from monitor.contracts import FetchedDocument
from monitor.providers import cert
from monitor.providers.common import ProviderError

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/providers/cert_users.xml"
NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


def tree():
    return ET.fromstring(FIXTURE.read_bytes())


def document(root=None, **overrides):
    return FetchedDocument(
        body=ET.tostring(root) if root is not None else FIXTURE.read_bytes(),
        content_type="application/xml", url=cert.URL,
        fetched_at=overrides.pop("fetched_at", NOW), **overrides,
    )


def first(root):
    return root.find("channel/item")


def set_text(root, field, value):
    element = first(root).find(field)
    if value is None:
        first(root).remove(element)
    else:
        element.text = value


def test_cert_records_are_only_generated_facts_and_links_not_source_content():
    batch = cert.parse(document())
    assert len(batch.events) == 2
    assert batch.rejected_count == 0
    assert batch.warnings == []
    event = batch.events[0]
    assert event.source_id == "cert_pl"
    assert event.provider_record_id == "2026/900001"
    assert event.title == "Komunikat CERT Polska 2026/900001 dla użytkowników"
    assert event.issued_at == datetime(2026, 9, 3, 14, 1, 2, tzinfo=timezone.utc)
    assert event.time_precision == "second"
    assert event.source_url == "https://moje.cert.pl/komunikaty/2026/900001/synthetic-one/"
    assert event.kind == "advisory"
    assert event.category == "cyber"
    assert event.origins == ["cert_pl"]
    assert event.external_ids == ["cert_pl:2026/900001"]
    assert set(event.raw) == {"publication_id", "audience", "published_at", "time_precision"}
    serialized = batch.model_dump_json()
    assert "WYMYŚLONY" not in serialized
    assert "Fioletowy" not in serialized
    assert "never-copy-this" not in serialized
    assert "Synthetic CERT RSS" not in serialized
    assert batch.metadata["publication_mode"] == "facts_and_links_only"
    assert batch.metadata["source_content_republished"] is False


def test_cert_does_not_infer_location_severity_incident_or_validity_from_polish_publisher():
    for event in cert.parse(document()).events:
        assert event.countries == []
        assert event.geometry is None
        assert event.location_precision == "unknown"
        assert event.occurred_start is None
        assert event.occurred_end is None
        assert event.source_updated_at is None
        assert event.valid_from is None
        assert event.valid_to is None
        assert event.lifecycle_status == "unknown"
        assert event.severity == 0
        assert event.original_severity is None


def test_cert_identity_survives_article_slug_edits():
    before = cert.parse(document()).events[0]
    root = tree()
    for field in ("link", "guid"):
        set_text(root, field, "https://moje.cert.pl/komunikaty/2026/900001/synthetic-renamed/")
    after = cert.parse(document(root)).events[0]
    assert after.provider_record_id == before.provider_record_id
    assert after.external_ids == before.external_ids
    assert after.source_url != before.source_url


@pytest.mark.parametrize("value", [None, "invalid private@example.test", "2026-09-03T14:01:02", "2026-09-03"])
def test_cert_unknown_publication_time_is_not_replaced_by_fetch_or_feed_clock(value):
    root = tree()
    set_text(root, "pubDate", value)
    batch = cert.parse(document(root))
    event = next(event for event in batch.events if event.provider_record_id == "2026/900001")
    assert event.issued_at is None
    assert event.time_precision == "unknown"
    assert event.raw["published_at"] is None
    assert batch.warnings
    assert "private@example.test" not in batch.model_dump_json()


def test_cert_uses_actual_publication_order_and_does_not_mutate_input():
    root = tree()
    channel = root.find("channel")
    older = channel.findall("item")[1]
    channel.remove(older)
    channel.insert(0, older)
    doc = document(root)
    original = copy.deepcopy(doc)
    batch = cert.parse(doc)
    assert [event.provider_record_id for event in batch.events] == ["2026/900001", "2026/900002"]
    assert doc == original
    assert batch.metadata["feed_last_build_at"] == "2026-09-03T14:01:02+00:00"


def test_cert_future_publication_remains_source_timestamp_with_explicit_warning():
    batch = cert.parse(document(fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    assert batch.events[0].issued_at == datetime(2026, 9, 3, 14, 1, 2, tzinfo=timezone.utc)
    assert any("późniejszy od pobrania" in warning for warning in batch.warnings)
    assert all(event.lifecycle_status == "unknown" for event in batch.events)


@pytest.mark.parametrize("value", ["Thu, 03 Sep 2026 14:01 +0000", "2026-09-03T14:01+00:00"])
def test_cert_keeps_minute_precision_when_source_does_not_supply_seconds(value):
    root = tree()
    set_text(root, "pubDate", value)
    event = cert.parse(document(root)).events[0]
    assert event.issued_at == datetime(2026, 9, 3, 14, 1, tzinfo=timezone.utc)
    assert event.time_precision == "minute"
    assert event.raw["time_precision"] == "minute"


def test_cert_filters_non_user_categories_and_rejects_missing_category():
    root = tree()
    set_text(root, "category", "Dla administratorów")
    batch = cert.parse(document(root))
    assert len(batch.events) == 1
    assert batch.metadata["excluded_categories"] == 1
    assert batch.rejected_count == 0
    assert any("inne kategorie" in warning for warning in batch.warnings)
    set_text(root, "category", None)
    batch = cert.parse(document(root))
    assert len(batch.events) == 1
    assert batch.rejected_count == 1
    assert batch.warnings


@pytest.mark.parametrize("value", [
    "http://moje.cert.pl/komunikaty/2026/900001/synthetic-one/",
    "https://moje.cert.pl.evil.test/komunikaty/2026/900001/synthetic-one/",
    "https://private:secret@moje.cert.pl/komunikaty/2026/900001/synthetic-one/",
    "https://moje.cert.pl:443/komunikaty/2026/900001/synthetic-one/",
    "https://moje.cert.pl/komunikaty/2026/900001/synthetic-one/?token=secret",
    "https://moje.cert.pl/komunikaty/2026/900001/synthetic-one/#fragment",
    "https://moje.cert.pl/komunikaty/2026/900001/../",
    "https://moje.cert.pl/komunikaty/2026/900001/%73ynthetic-one/",
    "javascript:alert(1)",
    "https://moje.cert.pl/komunikaty/2026/900001/synthetic-one/\n",
    None,
])
def test_cert_rejects_unsafe_or_unrecognized_article_links_without_echoing_them(value):
    root = tree()
    set_text(root, "link", value)
    batch = cert.parse(document(root))
    assert len(batch.events) == 1
    assert batch.rejected_count == 1
    assert batch.warnings
    assert "secret" not in batch.model_dump_json()


def test_cert_guid_and_link_must_agree_and_scalar_fields_must_be_unambiguous():
    root = tree()
    set_text(root, "guid", "https://moje.cert.pl/komunikaty/2026/900003/other-id/")
    assert cert.parse(document(root)).rejected_count == 1
    root = tree()
    ET.SubElement(first(root), "pubDate").text = "Thu, 03 Sep 2026 00:00:00 +0000"
    assert cert.parse(document(root)).rejected_count == 1
    root = tree()
    ET.SubElement(first(root).find("link"), "span").text = "nested"
    assert cert.parse(document(root)).rejected_count == 1


def test_cert_duplicate_id_is_partial_not_a_second_independent_record():
    root = tree()
    root.find("channel").append(copy.deepcopy(first(root)))
    batch = cert.parse(document(root))
    assert len(batch.events) == 2
    assert batch.rejected_count == 1
    assert batch.metadata["records_seen"] == 3


def test_cert_window_is_explicitly_bounded_without_crawling_history():
    root = tree()
    channel = root.find("channel")
    for number in range(3, 16):
        item = copy.deepcopy(first(root))
        for field in ("link", "guid"):
            item.find(field).text = f"https://moje.cert.pl/komunikaty/2026/{900000 + number}/synthetic-{number}/"
        channel.append(item)
    batch = cert.parse(document(root))
    assert len(batch.events) == cert.MAX_ITEMS == 10
    assert batch.metadata["records_seen"] == 15
    assert batch.metadata["excluded_by_limit"] == 5
    assert batch.metadata["rss_window_limit"] == 10
    assert any("pierwszych 10" in warning for warning in batch.warnings)


@pytest.mark.parametrize("body", [
    b"", b"not xml", b"<html><body>rate limit</body></html>",
    b'<rss version="2.0"/>', b'<rss version="1.0"><channel/></rss>',
    b'<rss version="2.0"><channel/><channel/></rss>',
    b'<!DOCTYPE rss [<!ENTITY ex SYSTEM "file:///etc/passwd">]><rss version="2.0"><channel>&ex;</channel></rss>',
    b"x" * (cert.MAX_FEED_BYTES + 1),
])
def test_cert_invalid_or_unsafe_feed_is_error_not_successful_empty_batch(body):
    with pytest.raises(ProviderError):
        cert.parse(FetchedDocument(body=body, content_type="application/xml", url=cert.URL))


def test_cert_valid_empty_feed_is_distinct_from_invalid_feed_and_cached_body_is_accepted():
    root = tree()
    channel = root.find("channel")
    for item in channel.findall("item"):
        channel.remove(item)
    batch = cert.parse(document(root))
    assert batch.events == []
    assert batch.rejected_count == 0
    assert batch.metadata["records_seen"] == 0
    cached = cert.parse(document(status=304, not_modified=True))
    assert len(cached.events) == 2
    assert cached.metadata["not_modified"] is True
    with pytest.raises(ProviderError, match="HTTP 503"):
        cert.parse(document(status=503))


class FakeFetcher:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_cert_collect_uses_injected_fetcher_exact_official_filter_and_one_request():
    fetcher = FakeFetcher(document())
    result = asyncio.run(cert.collect(fetcher, {"URL": "https://evil.test", "CERT_TOKEN": "ignored"}))
    assert len(result.events) == 2
    assert fetcher.calls == [(cert.URL, {"Accept": "application/rss+xml, application/xml"})]


def test_cert_fetch_error_keeps_only_bounded_retry_and_exception_class():
    error = RuntimeError("private:secret@private.example.test full response body")
    error.retry_after_seconds = 123
    fetcher = FakeFetcher(error)
    with pytest.raises(ProviderError) as failure:
        asyncio.run(cert.collect(fetcher, {}))
    assert failure.value.retry_after_seconds == 123
    assert "RuntimeError" in str(failure.value)
    assert "secret" not in str(failure.value)
    assert "private.example.test" not in str(failure.value)
