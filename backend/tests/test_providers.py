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
from monitor.providers import cisa, easa, gdacs, meteoalarm, radar, usgs
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
