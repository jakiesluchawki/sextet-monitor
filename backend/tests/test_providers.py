"""Deterministic normalization tests. No real HTTP client is used."""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from monitor.contracts import FetchedDocument
from monitor.providers import MissingCredentials, ProviderError, collect
from monitor.providers import (
    cisa, cloudflare_status, easa, eonet, gdacs, github_status, meteoalarm, radar, swpc, usgs,
)
from monitor.providers.common import timestamp

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "providers"
NOW = datetime(2026, 8, 26, 21, tzinfo=timezone.utc)
CAP_URL = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-poland/90f17002-353a-4686-88d3-0af8cde7f40c"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def document(body: bytes, url: str = "https://example.invalid/feed", **kwargs):
    return FetchedDocument(
        body=body, content_type="application/json" if body.lstrip().startswith(b"{") else "application/xml",
        url=url, fetched_at=kwargs.pop("fetched_at", NOW), **kwargs,
    )


def data_fixture(name):
    return json.loads(fixture(name))


def json_doc(data, **kwargs):
    return document(json.dumps(data).encode(), **kwargs)


def cap_tree():
    return ET.fromstring(fixture("meteoalarm_cap.xml"))


def cap_doc(tree, **kwargs):
    return document(ET.tostring(tree), url=CAP_URL, **kwargs)


def replace_cap(root, name, value):
    element = root.find(meteoalarm.CAP + name)
    if element is None:
        element = ET.SubElement(root, meteoalarm.CAP + name)
    element.text = value


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, FetchedDocument):
            return value
        return document(value, url=url)


def test_usgs_converts_epoch_utc_and_does_not_turn_depth_into_altitude():
    batch = usgs.parse(document(fixture("usgs.json")))
    event = batch.events[0]
    assert event.occurred_start == datetime(2026, 8, 26, 20, 48, 7, 551000, tzinfo=timezone.utc)
    assert event.geometry == {"type": "Point", "coordinates": [-104.313, 31.645]}
    assert event.raw["geometry"]["coordinates"][2] == 4.0435
    assert event.origins == ["usgs:tx"]
    assert event.external_ids == ["usgs:tx2026quqlha"]
    assert event.countries == []
    assert event.time_precision == "second"


def test_usgs_keeps_unknown_time_location_and_magnitude_unknown():
    data = data_fixture("usgs.json")
    data["features"][0]["geometry"] = None
    data["features"][0]["properties"].update(time=None, mag=None)
    event = usgs.parse(json_doc(data)).events[0]
    assert event.geometry is None
    assert event.location_precision == "unknown"
    assert event.occurred_start is None
    assert event.severity == 0


def test_usgs_rejects_bad_record_without_hiding_valid_records():
    data = data_fixture("usgs.json")
    data["features"].append({"type": "Feature", "properties": {}})
    data["metadata"]["count"] = 2
    batch = usgs.parse(json_doc(data))
    assert len(batch.events) == 1
    assert batch.rejected_count == 1
    assert batch.warnings


def test_usgs_invalid_position_is_unmapped_not_zero_zero():
    data = data_fixture("usgs.json")
    data["features"][0]["geometry"]["coordinates"] = [200, 95]
    batch = usgs.parse(json_doc(data))
    assert batch.events[0].geometry is None
    assert batch.warnings


def test_usgs_does_not_label_quarry_blast_as_earthquake():
    data = data_fixture("usgs.json")
    data["features"][0]["properties"]["type"] = "quarry blast"
    batch = usgs.parse(json_doc(data))
    assert not batch.events
    assert batch.metadata["excluded_non_earthquake"] == 1
    withdrawal = batch.metadata["reclassifications"][0]
    assert withdrawal["provider_record_id"] == data["features"][0]["id"]
    assert withdrawal["lifecycle_status"] == "withdrawn"
    assert withdrawal["verification_status"] == "reclassified_by_usgs"
    assert withdrawal["raw"]["properties"]["type"] == "quarry blast"


def test_usgs_foreign_ids_require_provider_explicit_ids():
    data = data_fixture("usgs.json")
    props = data["features"][0]["properties"]
    props["ids"] = ",tx2026quqlha,us7000abcd,untrusted id with spaces,"
    event = usgs.parse(json_doc(data)).events[0]
    assert event.external_ids == ["usgs:tx2026quqlha", "usgs:us7000abcd"]


@pytest.mark.parametrize("body", [
    b"", b"{}", b"[]", b"<html>error</html>",
    b'{"type":"FeatureCollection","features":[],"bad":NaN}',
])
def test_invalid_json_feed_never_becomes_empty_success(body):
    with pytest.raises(ProviderError):
        usgs.parse(document(body))


def test_http_error_is_not_a_valid_empty_collection():
    with pytest.raises(ProviderError):
        usgs.parse(document(b'{"type":"FeatureCollection","features":[]}', status=503))


def test_conditional_get_with_cached_body_is_supported():
    batch = usgs.parse(document(fixture("usgs.json"), status=304, not_modified=True))
    assert len(batch.events) == 1
    assert batch.metadata["not_modified"] is True


def test_304_without_cached_body_is_not_an_empty_feed():
    with pytest.raises(ProviderError):
        usgs.parse(document(b"", status=304, not_modified=True))


def test_offsetless_timestamp_stays_unknown():
    warnings = []
    value, precision = timestamp("2026-08-26T12:00:00", warnings=warnings)
    assert value is None and precision == "unknown"
    assert warnings


def test_gdacs_preserves_unknown_upstream_and_country():
    event = gdacs.parse(document(fixture("gdacs.xml"))).events[0]
    assert event.provider_record_id == "EQ:1561972"
    assert event.origins == ["unknown:gdacs"]
    assert event.countries == ["JP"]
    assert event.geometry["coordinates"] == [143.8477, 38.1292]
    assert event.external_ids == ["gdacs:EQ:1561972"]
    assert event.issued_at == datetime(2026, 8, 26, 19, 39, 59, tzinfo=timezone.utc)


def test_gdacs_episodes_are_revisions_with_current_last():
    root = ET.fromstring(fixture("gdacs.xml"))
    item = root.find("channel/item")
    current = copy.deepcopy(item)
    current.find(gdacs.GD + "episodeid").text = "1729396"
    current.find(gdacs.GD + "datemodified").text = "Wed, 26 Aug 2026 20:00:00 GMT"
    item.find(gdacs.GD + "iscurrent").text = "false"
    root.find("channel").insert(0, current)
    batch = gdacs.parse(document(ET.tostring(root)))
    assert len(batch.events) == 2
    assert {event.provider_record_id for event in batch.events} == {"EQ:1561972"}
    assert batch.events[-1].raw["episode_id"] == "1729396"
    assert batch.events[-1].raw["is_current"] is True
    assert batch.metadata["distinct_events"] == 1


def test_gdacs_only_adds_usgs_id_for_explicit_record_link():
    root = ET.fromstring(fixture("gdacs.xml"))
    resource = root.find(".//" + gdacs.GD + "resource")
    resource.set("source", "USGS")
    resource.set("url", "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd/executive")
    event = gdacs.parse(document(ET.tostring(root))).events[0]
    assert event.origins == ["usgs"]
    assert "usgs:us7000abcd" in event.external_ids


def test_gdacs_wildfires_retain_firms_dependency_and_approximate_position():
    root = ET.fromstring(fixture("gdacs.xml"))
    root.find(".//" + gdacs.GD + "eventtype").text = "WF"
    event = gdacs.parse(document(ET.tostring(root))).events[0]
    assert event.origins == ["nasa:firms"]
    assert event.location_precision == "area"
    assert "representative_point_not_extent" in event.tags


@pytest.mark.parametrize("body", [
    b"<html/>", b"<rss/>",
    b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel>&x;</channel></rss>',
])
def test_gdacs_rejects_invalid_or_unsafe_xml(body):
    with pytest.raises(ProviderError):
        gdacs.parse(document(body))


def test_cap_actual_does_not_mean_active_and_language_is_not_new_evidence():
    batch = meteoalarm.parse_cap(document(fixture("meteoalarm_cap.xml"), url=CAP_URL))
    assert len(batch.events) == 1
    event = batch.events[0]
    assert event.lifecycle_status == "expired"
    assert event.raw["status"] == "Actual"
    assert event.raw["languages"] == ["pl-PL", "en-GB"]
    assert event.raw["selected_language"] == "pl-PL"
    assert event.origins == ["imgw"]
    assert event.valid_to == datetime(2026, 8, 26, 7, tzinfo=timezone.utc)
    assert event.geometry is None
    assert event.location_precision == "area"


def test_cap_update_links_previous_identifier():
    root = cap_tree()
    previous = root.findtext(meteoalarm.CAP + "identifier")
    replace_cap(root, "identifier", "updated-warning")
    replace_cap(root, "msgType", "Update")
    replace_cap(root, "references", f"https://www.imgw.pl,{previous},2026-08-25T09:33:00Z")
    for info in root.findall(meteoalarm.CAP + "info"):
        info.find(meteoalarm.CAP + "expires").text = "2026-08-27T09:00:00+02:00"
    event = meteoalarm.parse_cap(cap_doc(root)).events[0]
    assert event.supersedes == [previous]
    assert event.lifecycle_status == "active"


def test_cap_cancel_without_info_can_withdraw_previous_warning():
    root = cap_tree()
    previous = root.findtext(meteoalarm.CAP + "identifier")
    replace_cap(root, "identifier", "cancelled-warning")
    replace_cap(root, "msgType", "Cancel")
    replace_cap(root, "references", f"https://www.imgw.pl,{previous},2026-08-25T09:33:00Z")
    for info in root.findall(meteoalarm.CAP + "info"):
        root.remove(info)
    event = meteoalarm.parse_cap(cap_doc(root)).events[0]
    assert event.lifecycle_status == "withdrawn"
    assert event.supersedes == [previous]
    assert event.origins == ["imgw"]


def test_cap_does_not_silently_cancel_other_sender():
    root = cap_tree()
    replace_cap(root, "msgType", "Cancel")
    replace_cap(root, "references", "other-sender,foreign-warning,2026-08-25T09:33:00Z")
    batch = meteoalarm.parse_cap(cap_doc(root))
    assert batch.events[0].supersedes == []
    assert batch.warnings


def test_cap_without_expiry_has_unknown_lifecycle():
    root = cap_tree()
    for info in root.findall(meteoalarm.CAP + "info"):
        info.remove(info.find(meteoalarm.CAP + "expires"))
    event = meteoalarm.parse_cap(cap_doc(root)).events[0]
    assert event.lifecycle_status == "unknown"
    assert event.valid_to is None


def test_cap_future_effective_is_not_yet_active():
    root = cap_tree()
    for info in root.findall(meteoalarm.CAP + "info"):
        info.find(meteoalarm.CAP + "effective").text = "2026-08-28T00:00:00Z"
        info.find(meteoalarm.CAP + "expires").text = "2026-08-29T00:00:00Z"
    event = meteoalarm.parse_cap(cap_doc(root)).events[0]
    assert event.lifecycle_status == "unknown"


@pytest.mark.parametrize(("field", "value"), [("status", "Test"), ("scope", "Private"), ("msgType", "Ack")])
def test_non_operational_cap_is_not_shown_as_real_warning(field, value):
    root = cap_tree()
    replace_cap(root, field, value)
    batch = meteoalarm.parse_cap(cap_doc(root))
    assert batch.events == []
    assert batch.rejected_count == 1
    assert batch.warnings


def test_cap_polygons_swap_lat_lon_and_bad_polygons_are_not_fabricated():
    root = cap_tree()
    area = root.find(meteoalarm.CAP + "info/" + meteoalarm.CAP + "area")
    ET.SubElement(area, meteoalarm.CAP + "polygon").text = "52,20 52,21 53,21 52,20"
    event = meteoalarm.parse_cap(cap_doc(root)).events[0]
    assert event.geometry == {"type": "Polygon", "coordinates": [[[20.0, 52.0], [21.0, 52.0], [21.0, 53.0], [20.0, 52.0]]]}
    area.find(meteoalarm.CAP + "polygon").text = "200,20 52,21 53,21 200,20"
    batch = meteoalarm.parse_cap(cap_doc(root))
    assert batch.events[0].geometry is None
    assert batch.warnings


@pytest.mark.asyncio
async def test_meteoalarm_fetches_duplicate_cap_link_once():
    fetcher = FakeFetcher({meteoalarm.feed_url("poland"): fixture("meteoalarm_atom.xml"), CAP_URL: fixture("meteoalarm_cap.xml")})
    batch = await collect("meteoalarm", fetcher)
    assert len(batch.events) == 1
    assert [url for url, _ in fetcher.calls] == [meteoalarm.feed_url("poland"), CAP_URL]
    assert batch.metadata["records_seen"] == 2
    assert batch.metadata["cap_requested"] == 1


@pytest.mark.asyncio
async def test_meteoalarm_fetch_failure_is_explicit_partial_not_ok_empty():
    fetcher = FakeFetcher({meteoalarm.feed_url("poland"): fixture("meteoalarm_atom.xml"), CAP_URL: RuntimeError("offline")})
    batch = await collect("meteoalarm", fetcher)
    assert batch.events == []
    assert batch.rejected_count == 1
    assert batch.warnings and batch.metadata["partial"]
    assert batch.metadata["cap_failed"] == 1


@pytest.mark.asyncio
async def test_meteoalarm_rejects_untrusted_cap_url_before_request():
    body = fixture("meteoalarm_atom.xml").replace(CAP_URL.encode(), b"http://127.0.0.1/admin")
    fetcher = FakeFetcher({meteoalarm.feed_url("poland"): body})
    batch = await collect("meteoalarm", fetcher)
    assert batch.events == []
    assert len(fetcher.calls) == 1
    assert batch.rejected_count == 2


@pytest.mark.asyncio
async def test_meteoalarm_invalid_atom_cannot_be_empty_success():
    fetcher = FakeFetcher({meteoalarm.feed_url("poland"): b'<feed xmlns="http://www.w3.org/2005/Atom"/>'})
    with pytest.raises(ProviderError):
        await collect("meteoalarm", fetcher)


@pytest.mark.asyncio
async def test_meteoalarm_valid_empty_atom_is_ok_empty():
    root = ET.fromstring(fixture("meteoalarm_atom.xml"))
    for entry in root.findall(meteoalarm.ATOM + "entry"):
        root.remove(entry)
    fetcher = FakeFetcher({meteoalarm.feed_url("poland"): ET.tostring(root)})
    batch = await collect("meteoalarm", fetcher)
    assert batch.events == [] and batch.warnings == [] and batch.rejected_count == 0


@pytest.mark.asyncio
async def test_meteoalarm_country_validation_happens_before_network():
    fetcher = FakeFetcher({})
    with pytest.raises(ProviderError):
        await collect("meteoalarm", fetcher, {"meteoalarm_country": "../private"})
    assert not fetcher.calls


def test_easa_html_timestamp_date_precision_country_and_no_fake_fir():
    events = easa.parse(document(fixture("easa.json"))).events
    libya, ukraine, withdrawn = events
    assert libya.source_updated_at == datetime(2026, 7, 24, 11, 20, 52, tzinfo=timezone.utc)
    assert libya.issued_at == datetime(2017, 3, 31, tzinfo=timezone.utc)
    assert libya.valid_to == datetime(2027, 2, 1, tzinfo=timezone.utc)
    assert libya.time_precision == "day"
    assert "valid_to_exclusive_day_boundary" in libya.tags
    assert libya.geometry is None and libya.raw["coordinates"]
    assert ukraine.geometry is None and ukraine.countries == ["UA"]
    assert withdrawn.lifecycle_status == "withdrawn"
    assert {"BH", "IR", "IQ", "IL", "SA", "AE"} <= set(withdrawn.countries)


def test_easa_invalid_dates_and_country_remain_unknown():
    data = data_fixture("easa.json")
    data["conflict_zones"] = [data["conflict_zones"][0]]
    data["conflict_zones"][0].update(issued_date="", updated="not a date", valid_until_date="31/02/2027", country="", status="")
    batch = easa.parse(json_doc(data))
    event = batch.events[0]
    assert event.issued_at is None and event.valid_to is None and event.source_updated_at is None
    assert event.countries == [] and event.lifecycle_status == "unknown"
    assert batch.warnings


def test_cisa_catalog_addition_and_deadline_are_not_attack_times():
    batch = cisa.parse(document(fixture("cisa.json")))
    event = batch.events[0]
    assert event.kind == "vulnerability_notice"
    assert event.occurred_start is None and event.source_updated_at is None
    assert event.issued_at == datetime(2026, 8, 26, tzinfo=timezone.utc)
    assert event.time_precision == "day" and "date_only_utc_anchor" in event.tags
    assert event.valid_to is None and event.geometry is None and event.countries == []
    assert event.severity == 0
    assert event.external_ids == ["cve:CVE-2026-99999"]


def test_cisa_wrong_count_and_bad_cve_do_not_hide_partial_feed():
    data = data_fixture("cisa.json")
    data["count"] = 3
    bad = copy.deepcopy(data["vulnerabilities"][0])
    bad["cveID"] = "not-a-cve"
    data["vulnerabilities"].append(bad)
    batch = cisa.parse(json_doc(data))
    assert len(batch.events) == 1 and batch.rejected_count == 1
    assert len(batch.warnings) >= 2


@pytest.mark.parametrize("parser", [cisa.parse, easa.parse, radar.parse])
def test_missing_expected_root_array_is_error(parser):
    with pytest.raises(ProviderError):
        parser(json_doc({"success": True, "result": {"outages": []}}))


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, {}, {"radar_token": ""}, {"radar_token": "   "}])
async def test_radar_missing_token_never_calls_network(config):
    fetcher = FakeFetcher({})
    with pytest.raises(MissingCredentials):
        await collect("cloudflare_radar", fetcher, config)
    assert fetcher.calls == []


def test_radar_uses_annotations_and_preserves_cause_as_attribution():
    event = radar.parse(document(fixture("radar.json"))).events[0]
    assert event.kind == "incident" and event.lifecycle_status == "expired"
    assert event.origins == ["cloudflare"]
    assert event.raw["origins"] == ["amazon-us-east-1"]
    assert event.raw["linkedUrl"] == "http://example.com"
    assert "Przyczyna przypisana przez Cloudflare: CABLE_CUT" in event.description
    assert event.geometry is None and event.countries == ["US"]
    assert event.severity == 0


def test_radar_anomaly_is_not_normalized_as_an_outage():
    data = data_fixture("radar.json")
    data["result"]["annotations"][0]["eventType"] = "ANOMALY"
    event = radar.parse(json_doc(data)).events[0]
    assert event.kind == "measurement"
    assert "anomaly_is_not_an_outage" in event.tags


def test_radar_error_envelope_is_not_ok_empty():
    with pytest.raises(ProviderError):
        radar.parse(json_doc({"success": False, "errors": [{"code": 9106}], "result": {"annotations": []}}))


@pytest.mark.asyncio
async def test_radar_sends_token_only_in_header_and_never_in_result():
    fetcher = FakeFetcher({radar.page_url(0): fixture("radar.json")})
    batch = await collect("cloudflare_radar", fetcher, {"radar_token": "fixture-token"})
    assert fetcher.calls[0][1] == {"Authorization": "Bearer fixture-token"}
    assert "fixture-token" not in fetcher.calls[0][0]
    assert "fixture-token" not in batch.model_dump_json()


@pytest.mark.asyncio
async def test_radar_transport_error_does_not_echo_credentials():
    fetcher = FakeFetcher({radar.page_url(0): RuntimeError("failure with fixture-secret")})
    with pytest.raises(ProviderError) as exc:
        await collect("cloudflare_radar", fetcher, {"radar_token": "fixture-secret"})
    assert "fixture-secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_radar_pagination_error_preserves_first_page_as_partial(monkeypatch):
    monkeypatch.setattr(radar, "PAGE_SIZE", 1)
    fetcher = FakeFetcher({radar.page_url(0): fixture("radar.json"), radar.page_url(1): RuntimeError("offline")})
    batch = await collect("cloudflare_radar", fetcher, {"radar_token": "fixture-token"})
    assert len(batch.events) == 1
    assert batch.warnings and batch.metadata["partial"]
    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_radar_bounded_pagination_reports_truncation(monkeypatch):
    monkeypatch.setattr(radar, "PAGE_SIZE", 1)
    monkeypatch.setattr(radar, "MAX_PAGES", 2)
    second = data_fixture("radar.json")
    second["result"]["annotations"][0]["id"] = "551"
    fetcher = FakeFetcher({radar.page_url(0): fixture("radar.json"), radar.page_url(1): json.dumps(second).encode()})
    batch = await collect("cloudflare_radar", fetcher, {"radar_token": "fixture-token"})
    assert len(batch.events) == 2
    assert batch.metadata["truncated"] and batch.warnings


@pytest.mark.asyncio
async def test_collect_dispatches_usgs_repair_feed_explicitly():
    fetcher = FakeFetcher({usgs.WEEK_URL: fixture("usgs.json")})
    batch = await collect("usgs", fetcher, {"usgs_window": "week"})
    assert len(batch.events) == 1 and fetcher.calls[0][0] == usgs.WEEK_URL


@pytest.mark.asyncio
async def test_unknown_source_is_error_before_network():
    fetcher = FakeFetcher({})
    with pytest.raises(ProviderError):
        await collect("invented", fetcher)
    assert not fetcher.calls


@pytest.mark.asyncio
async def test_meteoalarm_mixed_cap_failure_keeps_successful_evidence():
    root = ET.fromstring(fixture("meteoalarm_atom.xml"))
    second_url = CAP_URL + "-second"
    root.findall(meteoalarm.ATOM + "entry")[1].find(meteoalarm.ATOM + "link").set("href", second_url)
    fetcher = FakeFetcher({
        meteoalarm.feed_url("poland"): ET.tostring(root),
        CAP_URL: fixture("meteoalarm_cap.xml"),
        second_url: RuntimeError("offline"),
    })
    batch = await collect("meteoalarm", fetcher)
    assert len(batch.events) == 1
    assert batch.rejected_count == 1 and batch.metadata["partial"]


@pytest.mark.asyncio
async def test_meteoalarm_same_identifier_on_two_urls_is_one_event():
    root = ET.fromstring(fixture("meteoalarm_atom.xml"))
    second_url = CAP_URL + "-language"
    root.findall(meteoalarm.ATOM + "entry")[1].find(meteoalarm.ATOM + "link").set("href", second_url)
    fetcher = FakeFetcher({
        meteoalarm.feed_url("poland"): ET.tostring(root),
        CAP_URL: fixture("meteoalarm_cap.xml"),
        second_url: fixture("meteoalarm_cap.xml"),
    })
    batch = await collect("meteoalarm", fetcher)
    assert len(batch.events) == 1
    assert batch.metadata["cap_requested"] == 2
    assert batch.events[0].origins == ["imgw"]


@pytest.mark.asyncio
async def test_meteoalarm_cap_limit_is_explicit_partial(monkeypatch):
    monkeypatch.setattr(meteoalarm, "MAX_CAP_REQUESTS", 1)
    root = ET.fromstring(fixture("meteoalarm_atom.xml"))
    second_url = CAP_URL + "-second"
    root.findall(meteoalarm.ATOM + "entry")[1].find(meteoalarm.ATOM + "link").set("href", second_url)
    fetcher = FakeFetcher({
        meteoalarm.feed_url("poland"): ET.tostring(root),
        CAP_URL: fixture("meteoalarm_cap.xml"),
    })
    batch = await collect("meteoalarm", fetcher)
    assert len(batch.events) == 1
    assert batch.metadata["truncated"] and batch.rejected_count == 1
    assert second_url not in [url for url, _ in fetcher.calls]


def test_cap_inconsistent_onset_and_expiry_does_not_claim_valid_interval():
    root = cap_tree()
    for info in root.findall(meteoalarm.CAP + "info"):
        info.find(meteoalarm.CAP + "onset").text = "2026-08-29T00:00:00Z"
        info.find(meteoalarm.CAP + "expires").text = "2026-08-28T00:00:00Z"
    batch = meteoalarm.parse_cap(cap_doc(root))
    assert batch.events[0].valid_to is None
    assert batch.events[0].lifecycle_status == "unknown"
    assert batch.warnings


def test_cap_missing_issue_and_effective_does_not_claim_active():
    root = cap_tree()
    root.remove(root.find(meteoalarm.CAP + "sent"))
    for info in root.findall(meteoalarm.CAP + "info"):
        info.remove(info.find(meteoalarm.CAP + "effective"))
        info.find(meteoalarm.CAP + "expires").text = "2026-08-29T00:00:00Z"
    event = meteoalarm.parse_cap(cap_doc(root)).events[0]
    assert event.valid_from is None
    assert event.lifecycle_status == "unknown"


def test_easa_malformed_iso_issue_time_is_not_rescued_as_calendar_date():
    data = data_fixture("easa.json")
    data["conflict_zones"][0]["issued_date"] = "2017-03-31Tinvalid"
    batch = easa.parse(json_doc(data))
    assert batch.events[0].issued_at is None
    assert batch.warnings


def test_gdacs_noncurrent_episode_does_not_prove_disaster_ended():
    root = ET.fromstring(fixture("gdacs.xml"))
    root.find(".//" + gdacs.GD + "iscurrent").text = "false"
    event = gdacs.parse(document(ET.tostring(root))).events[0]
    assert event.lifecycle_status == "unknown"


def test_gdacs_valid_empty_channel_is_empty_without_error():
    root = ET.fromstring(fixture("gdacs.xml"))
    channel = root.find("channel")
    channel.remove(channel.find("item"))
    batch = gdacs.parse(document(ET.tostring(root)))
    assert batch.events == [] and batch.rejected_count == 0


def test_gdacs_non_numeric_event_id_is_rejected():
    root = ET.fromstring(fixture("gdacs.xml"))
    root.find(".//" + gdacs.GD + "eventid").text = "invented&query=yes"
    batch = gdacs.parse(document(ET.tostring(root)))
    assert batch.events == [] and batch.rejected_count == 1


def test_cisa_catalog_release_change_does_not_revise_unchanged_rows():
    data = data_fixture("cisa.json")
    before = cisa.parse(json_doc(data)).events[0]
    data["dateReleased"] = "2026-08-27T17:00:00Z"
    data["catalogVersion"] = "2026.08.27"
    after = cisa.parse(json_doc(data)).events[0]
    assert before.model_dump() == after.model_dump()


def test_gdacs_multiple_named_countries_are_not_reduced_to_primary_iso3():
    root = ET.fromstring(fixture("gdacs.xml"))
    root.find(".//" + gdacs.GD + "iso3").text = "AUS"
    root.find(".//" + gdacs.GD + "country").text = "Australia, Indonesia, Cambodia, Laos, Papua New Guinea, Philippines, Solomon Is., Thailand, Vietnam"
    event = gdacs.parse(document(ET.tostring(root))).events[0]
    assert set(event.countries) == {"AU", "ID", "KH", "LA", "PG", "PH", "SB", "TH", "VN"}


class FixtureRateLimit(RuntimeError):
    def __init__(self, seconds):
        super().__init__("untrusted transport message with fixture-secret")
        self.retry_after_seconds = seconds


def atom_with_cap_urls(urls):
    root = ET.fromstring(fixture("meteoalarm_atom.xml"))
    template = copy.deepcopy(root.find(meteoalarm.ATOM + "entry"))
    for entry in root.findall(meteoalarm.ATOM + "entry"):
        root.remove(entry)
    for index, url in enumerate(urls):
        entry = copy.deepcopy(template)
        entry.find(meteoalarm.ATOM + "id").text = "urn:fixture:warning:" + str(index)
        entry.find(meteoalarm.ATOM + "link").set("href", url)
        root.append(entry)
    return ET.tostring(root)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds, expected", [
    (1, 60), (7200, 7200), (200000, 86400), (True, None), ("7200", None),
])
async def test_radar_first_page_rate_limit_preserves_only_bounded_numeric_backoff(seconds, expected):
    fetcher = FakeFetcher({radar.page_url(0): FixtureRateLimit(seconds)})
    with pytest.raises(ProviderError) as error:
        await collect("cloudflare_radar", fetcher, {"radar_token": "fixture-secret"})
    assert error.value.retry_after_seconds == expected
    assert "fixture-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_radar_later_page_rate_limit_preserves_success_and_defers_retry(monkeypatch):
    monkeypatch.setattr(radar, "PAGE_SIZE", 1)
    fetcher = FakeFetcher({
        radar.page_url(0): fixture("radar.json"),
        radar.page_url(1): FixtureRateLimit(7200),
    })
    batch = await collect("cloudflare_radar", fetcher, {"radar_token": "fixture-secret"})
    assert len(batch.events) == 1
    assert batch.metadata["partial"] is True
    assert batch.metadata["retry_after_seconds"] == 7200
    assert len(fetcher.calls) == 2
    assert "fixture-secret" not in batch.model_dump_json()


@pytest.mark.asyncio
async def test_meteoalarm_first_rate_limit_stops_unstarted_cap_requests():
    urls = [CAP_URL + "-" + str(index) for index in range(3)]
    fetcher = FakeFetcher({
        meteoalarm.feed_url("poland"): atom_with_cap_urls(urls),
        urls[0]: FixtureRateLimit(3600),
    })
    batch = await collect("meteoalarm", fetcher)
    assert batch.events == []
    assert batch.rejected_count == 3
    assert batch.metadata["partial"] is True
    assert batch.metadata["cap_requested"] == 1
    assert batch.metadata["cap_failed"] == 1
    assert batch.metadata["cap_skipped_after_rate_limit"] == 2
    assert batch.metadata["retry_after_seconds"] == 3600
    assert [url for url, _ in fetcher.calls] == [meteoalarm.feed_url("poland"), urls[0]]
    assert "fixture-secret" not in batch.model_dump_json()


@pytest.mark.asyncio
async def test_meteoalarm_rate_limit_keeps_already_inflight_success_without_starting_more():
    urls = [CAP_URL + "-" + str(index) for index in range(3)]
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    class InflightFetcher(FakeFetcher):
        async def get(self, url, headers=None):
            if url == urls[0]:
                self.calls.append((url, headers))
                await second_started.wait()
                raise FixtureRateLimit(7200)
            if url == urls[1]:
                self.calls.append((url, headers))
                second_started.set()
                await release_second.wait()
                return document(fixture("meteoalarm_cap.xml"), url=url)
            return await super().get(url, headers)

    fetcher = InflightFetcher({meteoalarm.feed_url("poland"): atom_with_cap_urls(urls)})
    pending = asyncio.create_task(collect("meteoalarm", fetcher))
    await second_started.wait()
    await asyncio.sleep(0)
    release_second.set()
    batch = await pending
    assert len(batch.events) == 1
    assert batch.rejected_count == 2
    assert batch.metadata["partial"] is True
    assert batch.metadata["cap_requested"] == 2
    assert batch.metadata["cap_failed"] == 1
    assert batch.metadata["cap_skipped_after_rate_limit"] == 1
    assert batch.metadata["retry_after_seconds"] == 7200
    assert urls[2] not in [url for url, _ in fetcher.calls]


def test_returned_http_429_without_transport_headers_uses_minimum_backoff():
    with pytest.raises(ProviderError) as error:
        usgs.parse(document(fixture("usgs.json"), status=429))
    assert error.value.retry_after_seconds == 60


@pytest.mark.parametrize("released", [None, "2026-08-26", "2026-08-26T17:00:00", "2026-08-26T17:00Z"])
def test_cisa_snapshot_clock_requires_seconds_and_explicit_timezone(released):
    data = data_fixture("cisa.json")
    data["dateReleased"] = released
    batch = cisa.parse(json_doc(data))
    assert batch.metadata["provider_timestamp"] is None
    assert any("dateReleased" in warning for warning in batch.warnings)
    assert batch.events  # Content remains distinguishable from a verified catalog clock.


@pytest.mark.parametrize(("module", "name", "origin", "lifecycle"), [
    (github_status, "github_status.json", "github", "expired"),
    (cloudflare_status, "cloudflare_status.json", "cloudflare", "active"),
])
def test_status_metadata_preserves_operator_identity_and_separate_clocks(module, name, origin, lifecycle):
    batch = module.parse(document(fixture(name)))
    event = batch.events[0]
    assert event.source_id == module.SOURCE_ID
    assert event.source_url == module.URL.replace("/api/v2/incidents.json", "/incidents/") + event.provider_record_id
    assert event.origins == [origin]
    assert event.external_ids == [module.SOURCE_ID + ":" + event.provider_record_id]
    assert event.kind == "incident" and event.category == "internet"
    assert event.geometry is None and event.countries == [] and event.location_precision == "unknown"
    assert event.occurred_start == datetime(2026, 8, 26, 8, tzinfo=timezone.utc)
    assert event.issued_at == datetime(2026, 8, 26, 8, 5, tzinfo=timezone.utc)
    assert event.source_updated_at == datetime(2026, 8, 26, 10, tzinfo=timezone.utc)
    assert event.time_precision == "second" and event.lifecycle_status == lifecycle
    assert event.valid_from is None and event.valid_to is None
    assert batch.warnings == [] and batch.rejected_count == 0


@pytest.mark.parametrize(("module", "name"), [
    (github_status, "github_status.json"), (cloudflare_status, "cloudflare_status.json"),
])
def test_status_metadata_excludes_prose_arbitrary_urls_and_current_component_status(module, name):
    data = data_fixture(name)
    record = data["incidents"][0]
    record["shortlink"] = "https://credentials.example.invalid/TOKEN_MARKER"
    record["unknown_payload"] = {"body": "TOKEN_MARKER"}
    event = module.parse(json_doc(data)).events[0]
    assert set(event.raw) == {
        "id", "name", "status", "impact", "started_at", "created_at",
        "updated_at", "resolved_at", "components",
    }
    assert all(set(component) == {"id", "name"} for component in event.raw["components"])
    serialized = event.model_dump_json()
    assert "TEST_ONLY_" not in serialized and "TOKEN_MARKER" not in serialized
    assert "status_metadata_only" in event.tags
    assert "całego Internetu" in event.description
    record["incident_updates"][0]["body"] = "ANOTHER_BODY"
    record["components"][0]["status"] = "partial_outage"
    record["postmortem_body"] = "ANOTHER_POSTMORTEM"
    assert module.parse(json_doc(data)).events[0].model_dump() == event.model_dump()


@pytest.mark.parametrize(("status", "lifecycle"), [
    ("investigating", "active"), ("identified", "active"), ("monitoring", "active"),
    ("resolved", "expired"), ("postmortem", "expired"),
    (None, "unknown"), ("completed", "unknown"), ({"body": "TOKEN_MARKER"}, "unknown"),
])
def test_status_lifecycle_comes_from_incident_status_not_dates_or_component_state(status, lifecycle):
    data = data_fixture("github_status.json")
    data["incidents"][0].update(status=status, resolved_at=None)
    event = github_status.parse(json_doc(data)).events[0]
    assert event.lifecycle_status == lifecycle and event.occurred_end is None
    if lifecycle == "unknown":
        assert event.raw["status"] is None
    assert "TOKEN_MARKER" not in event.model_dump_json()


@pytest.mark.parametrize(("impact", "severity"), [
    ("none", 1), ("minor", 2), ("major", 3), ("critical", 4),
    (None, 0), ("TOKEN_MARKER", 0), ({"body": "TOKEN_MARKER"}, 0),
])
def test_status_priority_uses_only_incident_impact(impact, severity):
    data = data_fixture("cloudflare_status.json")
    data["incidents"][0]["impact"] = impact
    event = cloudflare_status.parse(json_doc(data)).events[0]
    assert event.severity == severity
    assert event.original_severity == (impact if severity else None)
    assert "TOKEN_MARKER" not in event.model_dump_json()


@pytest.mark.parametrize("value", [None, "", "2026-08-26", "2026-08-26T10:00:00", "TOKEN_MARKER", True])
def test_status_unknown_dates_are_not_filled_from_other_clocks_or_fetch_time(value):
    data = data_fixture("cloudflare_status.json")
    record = data["incidents"][0]
    for field in ("started_at", "created_at", "updated_at", "resolved_at"):
        record[field] = value
    batch = cloudflare_status.parse(json_doc(data))
    event = batch.events[0]
    assert event.occurred_start is None and event.occurred_end is None
    assert event.issued_at is None and event.source_updated_at is None
    assert event.time_precision == "unknown"
    assert event.lifecycle_status == "active"
    assert "TOKEN_MARKER" not in batch.model_dump_json()


def test_status_publication_date_does_not_replace_missing_start():
    data = data_fixture("github_status.json")
    data["incidents"][0]["started_at"] = None
    event = github_status.parse(json_doc(data)).events[0]
    assert event.occurred_start is None and event.time_precision == "unknown"
    assert event.issued_at is not None and event.source_updated_at is not None
    assert event.occurred_end == datetime(2026, 8, 26, 9, tzinfo=timezone.utc)


def test_status_inverted_interval_does_not_claim_an_end_before_start():
    data = data_fixture("github_status.json")
    data["incidents"][0]["resolved_at"] = "2026-08-26T07:59:00Z"
    batch = github_status.parse(json_doc(data))
    assert batch.events[0].occurred_end is None and batch.events[0].raw["resolved_at"] is None
    assert batch.events[0].lifecycle_status == "expired"
    assert batch.warnings


@pytest.mark.parametrize("bad_record", [
    None, [], {"id": "../TOKEN_MARKER", "name": "name"},
    {"id": "abc?TOKEN_MARKER", "name": "name"}, {"id": True, "name": "name"},
    {"id": "", "name": "name"}, {"id": "missingtitle"},
])
def test_status_bad_rows_are_rejected_without_payload_in_warnings(bad_record):
    data = data_fixture("github_status.json")
    data["incidents"].append(bad_record)
    batch = github_status.parse(json_doc(data))
    assert len(batch.events) == 1 and batch.rejected_count == 1
    assert "TOKEN_MARKER" not in batch.model_dump_json()
    assert "(ValueError)" in batch.warnings[-1]


def test_status_duplicate_ids_do_not_become_independent_events():
    data = data_fixture("github_status.json")
    data["incidents"].append(copy.deepcopy(data["incidents"][0]))
    batch = github_status.parse(json_doc(data))
    assert len(batch.events) == 1 and batch.rejected_count == 1
    assert batch.metadata["records_seen"] == 2


@pytest.mark.parametrize("body", [
    b"", b"{}", b"[]", b"<html>error</html>", b'{"incidents":null}',
    b'{"incidents":{}}', b'{"incidents":[],"bad":NaN}',
])
def test_status_invalid_envelope_does_not_become_empty_success(body):
    with pytest.raises(ProviderError):
        github_status.parse(document(body))


@pytest.mark.parametrize("count", [0, 50])
def test_status_recent_fifty_is_declared_coverage_not_partial_or_a_complete_archive(count):
    base = data_fixture("github_status.json")["incidents"][0]
    records = [{**copy.deepcopy(base), "id": f"test{index:08d}"} for index in range(count)]
    batch = github_status.parse(json_doc({"incidents": records}))
    assert len(batch.events) == count and batch.metadata["records_seen"] == count
    assert batch.metadata["history_complete"] is False
    assert batch.metadata["coverage"] == "latest_50_incidents"
    assert batch.warnings == [] and batch.rejected_count == 0
    assert not batch.metadata.get("partial") and not batch.metadata.get("truncated")


@pytest.mark.asyncio
@pytest.mark.parametrize(("module", "name"), [
    (github_status, "github_status.json"), (cloudflare_status, "cloudflare_status.json"),
])
async def test_status_collect_uses_only_fixed_anonymous_endpoint(module, name):
    fetcher = FakeFetcher({
        module.URL: document(fixture(name), url="https://example.invalid/TOKEN_MARKER"),
    })
    batch = await module.collect(fetcher, {
        "url": "http://127.0.0.1/private", "token": "TOKEN_MARKER", "radar_token": "TOKEN_MARKER",
    })
    assert fetcher.calls == [(module.URL, None)]
    assert batch.metadata["feed_url"] == module.URL
    assert "TOKEN_MARKER" not in batch.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [github_status, cloudflare_status])
async def test_status_transport_error_carries_only_class_and_bounded_backoff(module):
    error = RuntimeError("TOKEN_MARKER https://private.example.invalid")
    error.retry_after_seconds = 900
    fetcher = FakeFetcher({module.URL: error})
    with pytest.raises(ProviderError) as caught:
        await module.collect(fetcher, {})
    assert "TOKEN_MARKER" not in str(caught.value)
    assert "RuntimeError" in str(caught.value)
    assert caught.value.retry_after_seconds == 900


def test_eonet_keeps_curated_precision_geometry_and_upstream_identity():
    batch = eonet.parse(document(fixture("eonet.json")))
    fire, volcano, storm = batch.events
    assert batch.rejected_count == 0 and not batch.warnings
    assert fire.category == "disaster" and fire.kind == "incident"
    assert fire.occurred_start == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert fire.time_precision == "day" and "date_only_utc_anchor" in fire.tags
    assert fire.geometry == {"type": "Point", "coordinates": [21.1, 52.1]}
    assert "latest_geometry_not_full_track" in fire.tags
    assert fire.issued_at is None and fire.source_updated_at is None and fire.occurred_end is None
    assert fire.origins == ["nasa:firms"]
    assert fire.external_ids == ["eonet:EONET_FIXTURE_FIRE", "gdacs:WF:9990001"]
    assert volcano.geometry["type"] == "Polygon" and volcano.location_precision == "area"
    # EONET's curator can close a day at 00:00 even if that day's point is later.
    assert volcano.lifecycle_status == "expired" and volcano.occurred_end is None
    assert "curator_closed_not_verified_physical_end" in volcano.tags
    assert storm.origins == ["noaa:nhc"] and storm.severity == 0
    assert all(event.countries == [] for event in batch.events)


def test_eonet_reference_to_gdacs_is_not_independent_confirmation():
    from monitor.ingestion import independent_origins

    event = eonet.parse(document(fixture("eonet.json"))).events[0]
    count, origins = independent_origins([
        event.model_dump(mode="json"), {"source_id": "gdacs", "origins": ["nasa:firms"]},
    ])
    assert count == 1 and origins == ["nasa:firms"]


@pytest.mark.parametrize("url", [
    "https://www.gdacs.org.evil.invalid/report.aspx?eventtype=WF&eventid=9990001",
    "https://user:password@www.gdacs.org/report.aspx?eventtype=WF&eventid=9990001",
    "https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=9990001",
    "https://www.gdacs.org/report.aspx?eventtype=WF&eventid=9990001&eventid=9990002",
])
def test_eonet_does_not_join_untrusted_or_ambiguous_upstream_links(url):
    data = data_fixture("eonet.json")
    data["events"][0]["sources"][0]["url"] = url
    event = eonet.parse(json_doc(data)).events[0]
    assert event.external_ids == ["eonet:EONET_FIXTURE_FIRE"]


def test_eonet_unknown_origin_stays_unknown():
    data = data_fixture("eonet.json")
    data["events"][0]["sources"] = [{"id": "UNMAPPED_SOURCE", "url": "https://example.invalid"}]
    assert eonet.parse(json_doc(data)).events[0].origins == ["unknown:eonet:unmapped_source"]


@pytest.mark.parametrize("value", ["not-a-date", "2026-08-27T12:00:00Z", "2026-08-26T12:00:00"])
def test_eonet_bad_or_future_geometry_time_is_not_a_current_observation(value):
    data = data_fixture("eonet.json")
    data["events"][0]["geometry"] = [{"date": value, "type": "Point", "coordinates": [21, 52]}]
    batch = eonet.parse(json_doc(data))
    event = batch.events[0]
    assert event.occurred_start is None and event.geometry is None
    assert event.time_precision == "unknown" and batch.warnings


def test_eonet_unknown_time_keeps_explicit_geometry_without_inventing_a_clock():
    data = data_fixture("eonet.json")
    data["events"][0]["geometry"] = [{"type": "Point", "coordinates": [21, 52]}]
    event = eonet.parse(json_doc(data)).events[0]
    assert event.geometry["coordinates"] == [21.0, 52.0]
    assert event.occurred_start is None and event.time_precision == "unknown"


def test_eonet_invalid_geometry_does_not_become_a_centroid_or_hide_other_records():
    data = data_fixture("eonet.json")
    data["events"][0]["geometry"][-1]["coordinates"] = [201, 95]
    data["events"].append({"id": "EONET_BAD", "categories": None})
    batch = eonet.parse(json_doc(data))
    assert len(batch.events) == 3 and batch.events[0].geometry is None
    assert batch.rejected_count == 1 and batch.warnings


def test_eonet_filters_unselected_categories_and_old_geometry_without_fake_updates():
    data = data_fixture("eonet.json")
    data["events"][0]["geometry"] = [{
        "date": "2026-07-01T12:00:00Z", "type": "Point", "coordinates": [21, 52],
    }]
    data["events"][1]["categories"] = [{"id": "icebergs", "title": "Sea and Lake Ice"}]
    batch = eonet.parse(json_doc(data))
    assert [event.provider_record_id for event in batch.events] == ["EONET_FIXTURE_STORM"]
    assert batch.metadata["excluded_outside_window"] == 1
    assert batch.metadata["excluded_categories"] == 1


@pytest.mark.parametrize("adapter,name", [(eonet, "eonet"), (swpc, "swpc")])
def test_new_public_feeds_collapse_identical_but_reject_conflicting_duplicate_ids(adapter, name):
    data = data_fixture(name + ".json")
    records = data["events"] if name == "eonet" else data
    original = copy.deepcopy(records[0])
    records.append(copy.deepcopy(original))
    batch = adapter.parse(json_doc(data))
    assert len(batch.events) == len(records) - 1 and batch.metadata["duplicate_records"] == 1
    changed = copy.deepcopy(original)
    if name == "eonet":
        changed["title"] = "Conflicting source title"
    else:
        changed["message"] += "\nConflicting extra content"
    records.append(changed)
    batch = adapter.parse(json_doc(data))
    assert len(batch.events) == len(records) - 3
    assert batch.rejected_count == 2 and batch.warnings


@pytest.mark.parametrize("adapter,name", [(eonet, "eonet"), (swpc, "swpc")])
def test_new_public_feeds_are_bounded_and_do_not_claim_a_complete_capped_catalog(adapter, name):
    data = data_fixture(name + ".json")
    row = data["events"][0] if name == "eonet" else data[0]
    data = {"events": [row] * 401} if name == "eonet" else [row] * 401
    batch = adapter.parse(json_doc(data))
    assert batch.metadata["partial"] is True
    assert batch.metadata["records_seen"] == 401 and batch.metadata["record_limit"] == 400
    assert batch.metadata["duplicate_records"] == 399 and len(batch.events) == 1


def test_eonet_next_link_marks_partial_and_is_never_followed():
    data = data_fixture("eonet.json")
    data["links"] = {"next": "http://127.0.0.1/private"}
    fetcher = FakeFetcher({eonet.URL: json_doc(data)})
    batch = asyncio.run(eonet.collect(fetcher, {}))
    assert batch.metadata["partial"] is True and batch.warnings
    assert fetcher.calls == [(eonet.URL, None)]


def test_swpc_separates_observations_from_forecasts_and_does_not_map_impact_prose():
    batch = swpc.parse(document(fixture("swpc.json")))
    events = {event.raw["product_id"]: event for event in batch.events}
    observed, warning, watch, summary = (events[key] for key in ("K05A", "K05W", "A30F", "BHIS"))
    assert len(batch.events) == 4 and not batch.warnings and batch.rejected_count == 0
    assert observed.kind == "measurement" and "observed_alert" in observed.tags
    assert observed.occurred_start == datetime(2026, 8, 26, 18, 14, tzinfo=timezone.utc)
    assert observed.lifecycle_status == "unknown"
    assert observed.issued_at == datetime(2026, 8, 26, 18, 15, tzinfo=timezone.utc)
    assert observed.original_severity == "G1 - Minor" and observed.severity == 0
    assert warning.kind == "advisory" and "forecast" in warning.tags and "advisory" in warning.tags
    assert warning.occurred_start is None and warning.lifecycle_status == "active"
    assert warning.valid_to == datetime(2026, 8, 26, 23, tzinfo=timezone.utc)
    assert watch.original_severity == "G2" and watch.valid_from is None and watch.valid_to is None
    assert watch.lifecycle_status == "unknown"
    assert summary.occurred_end == datetime(2026, 8, 26, 6, 32, tzinfo=timezone.utc)
    assert summary.lifecycle_status == "expired"
    assert all(event.category == "space_weather" and event.origins == ["noaa:swpc"] for event in batch.events)
    assert all(event.geometry is None and event.countries == [] for event in batch.events)
    assert batch.metadata["provider_timestamp"] is None


def test_swpc_product_id_repetition_does_not_collapse_different_bulletins():
    first = data_fixture("swpc.json")[0]
    second = copy.deepcopy(first)
    second["message"] = second["message"].replace("7001", "7002")
    batch = swpc.parse(json_doc([first, second]))
    assert len(batch.events) == 2
    assert len({event.provider_record_id for event in batch.events}) == 2


def test_swpc_reused_serial_gets_a_distinct_dated_identity():
    first = data_fixture("swpc.json")[0]
    second = copy.deepcopy(first)
    second["issue_datetime"] = "2026-08-25 18:15:03.120"
    second["message"] = second["message"].replace("2026 Aug 26", "2026 Aug 25")
    batch = swpc.parse(json_doc([first, second]))
    assert len(batch.events) == 2
    assert len({event.provider_record_id for event in batch.events}) == 2


def test_swpc_extension_uses_explicit_previous_bulletin_and_updated_validity():
    first = data_fixture("swpc.json")[1]
    extension = copy.deepcopy(first)
    extension["issue_datetime"] = "2026-08-26 20:10:21.003"
    extension["message"] = (extension["message"].replace("8001", "8002")
                            .replace("1910 UTC", "2010 UTC")
                            .replace("WARNING:", "EXTENDED WARNING:")
                            .replace("Valid To: 2026 Aug 26 2300 UTC", "Now Valid Until: 2026 Aug 27 0200 UTC")
                            + "\nExtension to Serial Number: 8001")
    batch = swpc.parse(json_doc([extension, first]))
    assert len(batch.events) == 2 and not batch.warnings
    old, new = batch.events
    assert "swpc:" + old.provider_record_id in new.external_ids
    assert new.valid_to == datetime(2026, 8, 27, 2, tzinfo=timezone.utc)
    assert new.provider_record_id != old.provider_record_id


def test_swpc_explicit_cancellation_can_resolve_target_outside_the_current_feed():
    cancel = data_fixture("swpc.json")[2]
    cancel["issue_datetime"] = "2026-08-26 20:30:00.000"
    cancel["message"] = (cancel["message"].replace("9001", "9002").replace("1630 UTC", "2030 UTC")
                         .replace("WATCH:", "CANCEL WATCH:")
                         + "\nCancel Serial Number: 9001\nOriginal Issue Time: 2026 Aug 26 1630 UTC")
    batch = swpc.parse(json_doc([cancel]))
    event = batch.events[0]
    assert event.lifecycle_status == "withdrawn" and event.kind == "advisory"
    assert "swpc:WATA30:9001:20260826T1630Z" in event.external_ids
    assert "source_cancellation" in event.tags
    assert not batch.warnings


def test_swpc_missing_reference_is_visible_but_not_guessed_from_serial_alone():
    row = data_fixture("swpc.json")[0]
    row["message"] += "\nContinuation of Serial Number: 7000"
    batch = swpc.parse(json_doc([row]))
    event = batch.events[0]
    assert event.external_ids == ["swpc:" + event.provider_record_id]
    assert "unresolved_source_reference" in event.tags and batch.warnings


def test_swpc_ambiguous_reused_reference_serial_is_not_merged():
    earlier = data_fixture("swpc.json")[0]
    prior = copy.deepcopy(earlier)
    prior["issue_datetime"] = "2026-08-25 18:15:03.120"
    prior["message"] = prior["message"].replace("2026 Aug 26", "2026 Aug 25")
    current = copy.deepcopy(earlier)
    current["issue_datetime"] = "2026-08-26 20:15:03.120"
    current["message"] = (current["message"].replace("Serial Number: 7001", "Serial Number: 7002")
                          .replace("1815 UTC", "2015 UTC") + "\nContinuation of Serial Number: 7001")
    batch = swpc.parse(json_doc([current, earlier, prior]))
    event = batch.events[-1]
    assert len(event.external_ids) == 1 and "unresolved_source_reference" in event.tags


@pytest.mark.parametrize("start,end,status", [
    ("2026 Aug 26 2200 UTC", "2026 Aug 26 2300 UTC", "unknown"),
    ("2026 Aug 26 1900 UTC", "2026 Aug 26 2100 UTC", "expired"),
    ("2026 Aug 26 1900 UTC", "2026 Aug 26 2300 UTC", "active"),
])
def test_swpc_explicit_validity_has_conservative_boundary_status(start, end, status):
    row = data_fixture("swpc.json")[1]
    row["message"] = (row["message"].replace("Valid From: 2026 Aug 26 1900 UTC", "Valid From: " + start)
                      .replace("Valid To: 2026 Aug 26 2300 UTC", "Valid To: " + end))
    assert swpc.parse(json_doc([row])).events[0].lifecycle_status == status


def test_swpc_naive_clock_requires_explicit_matching_utc_and_preserves_missing_precision():
    row = data_fixture("swpc.json")[0]
    row["message"] = row["message"].replace("1815 UTC", "1815")
    batch = swpc.parse(json_doc([row]))
    assert not batch.events and batch.rejected_count == 1
    row = data_fixture("swpc.json")[0]
    row["issue_datetime"] = None
    event = swpc.parse(json_doc([row])).events[0]
    assert event.issued_at == datetime(2026, 8, 26, 18, 15, tzinfo=timezone.utc)
    assert "issue_time_precision:minute" in event.tags


@pytest.mark.parametrize("value", ["not-a-date", "2026-08-26 19:15:03.120", "2026-08-27 18:15:03.120"])
def test_swpc_bad_or_disagreeing_publication_clock_does_not_override_explicit_utc(value):
    row = data_fixture("swpc.json")[0]
    row["issue_datetime"] = value
    batch = swpc.parse(json_doc([row]))
    assert not batch.events and batch.rejected_count == 1


def test_swpc_future_publication_and_invalid_row_do_not_hide_a_valid_bulletin():
    data = data_fixture("swpc.json")
    data[0]["issue_datetime"] = "2026-08-27 18:15:03.120"
    data[0]["message"] = data[0]["message"].replace("2026 Aug 26", "2026 Aug 27")
    batch = swpc.parse(json_doc([data[0], None, data[1]]))
    assert len(batch.events) == 1 and batch.rejected_count == 2
    assert batch.events[0].raw["product_id"] == "K05W"


def test_swpc_future_observation_is_unknown_without_relabeling_publication_as_occurrence():
    row = data_fixture("swpc.json")[0]
    row["message"] = row["message"].replace("Threshold Reached: 2026 Aug 26 1814 UTC",
                                           "Threshold Reached: 2026 Aug 27 1814 UTC")
    batch = swpc.parse(json_doc([row]))
    assert batch.events[0].occurred_start is None and batch.warnings


def test_swpc_old_bulletin_is_outside_the_declared_window():
    row = data_fixture("swpc.json")[0]
    row["issue_datetime"] = "2026-07-01 18:15:03.120"
    row["message"] = row["message"].replace("2026 Aug 26", "2026 Jul 01")
    batch = swpc.parse(json_doc([row]))
    assert not batch.events and batch.metadata["excluded_outside_window"] == 1
    assert not batch.warnings and batch.rejected_count == 0


@pytest.mark.parametrize("adapter,body", [
    (eonet, b""), (eonet, b"{}"), (eonet, b"[]"), (eonet, b"<html>unavailable</html>"),
    (eonet, b'{"events":[],"bad":NaN}'),
    (swpc, b""), (swpc, b"{}"), (swpc, b"<html>unavailable</html>"), (swpc, b"[NaN]"),
])
def test_new_public_feeds_reject_invalid_json_instead_of_empty_success(adapter, body):
    with pytest.raises(ProviderError):
        adapter.parse(document(body))


@pytest.mark.parametrize("adapter,payload", [(eonet, {"events": []}), (swpc, [])])
def test_new_public_feeds_accept_confirmed_empty_feeds(adapter, payload):
    batch = adapter.parse(json_doc(payload))
    assert not batch.events and not batch.warnings and batch.rejected_count == 0
    assert batch.metadata["records_seen"] == 0


@pytest.mark.parametrize("adapter,payload", [(eonet, {"events": []}), (swpc, [])])
@pytest.mark.parametrize("status", [403, 429, 500])
def test_new_public_feeds_preserve_http_failure_and_rate_limit_backoff(adapter, payload, status):
    with pytest.raises(ProviderError) as exc:
        adapter.parse(json_doc(payload, status=status))
    assert exc.value.retry_after_seconds == (60 if status == 429 else None)


@pytest.mark.parametrize("adapter,name", [(eonet, "eonet"), (swpc, "swpc")])
def test_new_public_collectors_use_only_the_fixed_public_endpoint(adapter, name):
    fetcher = FakeFetcher({adapter.URL: fixture(name + ".json")})
    batch = asyncio.run(adapter.collect(fetcher, {"url": "http://127.0.0.1/private"}))
    assert batch.events and fetcher.calls == [(adapter.URL, None)]
    failure = ProviderError("transport unavailable", retry_after_seconds=120)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(adapter.collect(FakeFetcher({adapter.URL: failure}), {}))
    assert exc.value is failure


@pytest.mark.parametrize("lag", ["18:16:06.643", "18:17:38.617"])
def test_swpc_technical_json_delay_does_not_replace_explicit_minute_utc(lag):
    row = data_fixture("swpc.json")[0]
    row["issue_datetime"] = "2026-08-26 " + lag
    batch = swpc.parse(json_doc([row]))
    assert len(batch.events) == 1 and not batch.warnings
    assert batch.events[0].issued_at == datetime(2026, 8, 26, 18, 15, tzinfo=timezone.utc)
    assert batch.events[0].source_updated_at == batch.events[0].issued_at
    assert batch.events[0].raw["issue_datetime"].endswith(lag)
    assert "issue_time_precision:minute" in batch.events[0].tags


@pytest.mark.parametrize("field", [
    "Continuation of Serial Number: 7001",
    "Continuation of Serial Number: 7BAD",
    "Cancel Serial Number: 6999\nOriginal Issue Time: 2026 Aug 27 1815 UTC",
])
def test_swpc_invalid_or_forward_self_references_are_rejected(field):
    row = data_fixture("swpc.json")[0]
    row["message"] += "\n" + field
    batch = swpc.parse(json_doc([row]))
    assert not batch.events and batch.rejected_count == 1
