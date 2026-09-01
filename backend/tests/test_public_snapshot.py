"""Privacy and integrity boundaries of independently generated public data."""
from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest

from monitor import public_snapshot as public
from monitor.providers import SOURCES
from test_api import EVENT_ID, NOW, event

SECOND_ID = "07832b40-e2b2-420f-8d0d-cb97e10d7f0d"


def record(event_id=EVENT_ID, provider_id="fixture-public-1", **overrides):
    value = event(id=event_id, evidence=[{
        "id": "d79a261c-2738-4809-99d0-a6f73dbb1236", "source_id": "usgs", "source_name": "USGS",
        "provider_record_id": provider_id, "source_url": "https://earthquake.usgs.gov/earthquakes/eventpage/example",
        "retrieved_at": NOW, "origins": ["usgs:us"], "payload_hash": "a" * 64,
        "raw": {"private_test_marker": "never-export-this"}, "raw_retained": True,
        "attribution": "U.S. Geological Survey", "license_url": "https://www.usgs.gov/copyright",
    }], revisions=[{"id": "d79a261c-2738-4809-99d0-a6f73dbb1236", "recorded_at": NOW,
                     "change_type": "updated", "summary": "private-history-test-marker"}], relations=[],
                  internal_private_field="not-in-output-contract")
    value.update(overrides)
    return value


def sources():
    return [{**SOURCES[source].model_dump(), "enabled": True, "status": "ok",
             "last_success_at": NOW, "record_count": 1} for source in public.PUBLIC_SOURCE_IDS]


@pytest.mark.parametrize("url", ["", "postgresql+psycopg://localhost/monitor",
                                  "postgresql+psycopg://localhost/monitor_test_fixture",
                                  "sqlite:///postgres",
                                  "postgresql+psycopg://localhost/postgres?dbname=monitor",
                                  "postgresql+psycopg://localhost/postgres?service=private",
                                  "postgresql+psycopg://localhost/postgres?host=other-host"])
def test_existing_database_or_implicit_default_cannot_be_exported(url):
    with pytest.raises(ValueError):
        public.validate_admin_url(url)


def test_administrative_connection_is_only_to_postgres():
    assert public.validate_admin_url("postgresql+psycopg://localhost/postgres").database == "postgres"


def test_snapshot_discards_raw_private_identifiers_and_history_but_keeps_source_evidence():
    payload = public.encode_snapshot([record()], sources(), NOW)
    output = json.loads(payload)
    clean = output["events"][0]
    assert all(marker not in payload.decode() for marker in [
        EVENT_ID, "never-export-this", "private-history-test-marker", "not-in-output-contract",
    ])
    assert clean["evidence"][0]["raw"] is None and not clean["evidence"][0]["raw_retained"]
    assert clean["revisions"] == []
    assert clean["evidence"][0]["provider_record_id"] == "fixture-public-1"
    assert clean["evidence"][0]["payload_hash"] == "a" * 64
    assert clean["source_url"].startswith("https://earthquake.usgs.gov/")


def test_public_identifiers_are_stable_across_fresh_database_runs():
    first = json.loads(public.encode_snapshot([record()], sources(), NOW))["events"][0]
    second = json.loads(public.encode_snapshot([record(SECOND_ID)], sources(), NOW))["events"][0]
    assert first["id"] == second["id"]
    assert first["evidence"][0]["id"] == second["evidence"][0]["id"]


def test_relations_are_remapped_to_public_identifiers_and_unknown_targets_are_not_published():
    edge = {"event_id": SECOND_ID, "title": "Related source record", "relation_type": "possible_same_event",
            "reason": "Temporal similarity, not proof.", "distance_km": 1, "time_delta_hours": 0.1}
    first = record(relations=[edge, {**edge, "event_id": "unknown-private-target"}])
    output = json.loads(public.encode_snapshot([first, record(SECOND_ID, "second-public-record")], sources(), NOW))
    assert [edge["event_id"] for edge in output["events"][0]["relations"]] == [output["events"][1]["id"]]
    assert SECOND_ID not in json.dumps(output)


@pytest.mark.parametrize("where", ["source_ids", "evidence"])
def test_non_allowlisted_sources_fail_closed(where):
    value = record()
    if where == "source_ids":
        value["source_ids"] = ["cloudflare_radar"]
    else:
        value["evidence"][0]["source_id"] = "cloudflare_radar"
    with pytest.raises(ValueError):
        public.encode_snapshot([value], sources(), NOW)


def test_failed_or_missing_source_does_not_replace_previous_publication():
    for broken in (sources()[:-1], [{**source, "status": "error"} for source in sources()]):
        with pytest.raises(ValueError):
            public.encode_snapshot([], broken, NOW)


def test_empty_valid_feeds_are_not_reported_as_a_failure():
    output = json.loads(public.encode_snapshot([], [{**item, "status": "ok_empty"} for item in sources()], NOW))
    assert output["events"] == []
    assert len(output["sources"]) == len(public.PUBLIC_SOURCE_IDS)


def test_duplicate_public_identity_is_rejected():
    with pytest.raises(ValueError):
        public.encode_snapshot([record(), record(SECOND_ID)], sources(), NOW)


def test_size_and_event_limits_do_not_silently_truncate(monkeypatch):
    monkeypatch.setattr(public, "MAX_EVENTS", 0)
    with pytest.raises(ValueError, match="event limit"):
        public.encode_snapshot([record()], sources(), NOW)
    monkeypatch.setattr(public, "MAX_EVENTS", 10_000)
    monkeypatch.setattr(public, "MAX_BYTES", 2)
    with pytest.raises(ValueError, match="16 MiB"):
        public.encode_snapshot([], sources(), NOW)


def test_source_error_detail_and_credentials_are_not_serialized():
    health = deepcopy(sources())
    health[0].update(status="partial", error="test-secret-url-and-parameters")
    encoded = public.encode_snapshot([record()], health, NOW).decode()
    assert "test-secret-url-and-parameters" not in encoded
    assert "Niepełny odczyt" in encoded


def failed_source(source_id="usgs"):
    return [{**source, "status": "error", "last_success_at": None, "last_attempt_at": NOW+timedelta(hours=1),
             "record_count": 0, "error": "private-connection-details"}
            if source["id"] == source_id else source for source in sources()]


def test_one_source_failure_does_not_block_other_sources_or_expose_diagnostics():
    encoded = public.encode_snapshot([], failed_source(), NOW+timedelta(hours=1))
    failed = next(source for source in json.loads(encoded)["sources"] if source["id"] == "usgs")
    assert failed["status"] == "error" and failed["record_count"] == 0
    assert failed["last_success_at"] is None
    assert b"private-connection-details" not in encoded


def test_partial_batch_with_no_accepted_records_is_an_error_and_can_use_previous_data():
    previous = json.loads(public.encode_snapshot([record()], sources(), NOW))
    health = [{**item, "status": "partial"} if item["id"] == "usgs" else item for item in failed_source()]
    output = json.loads(public.encode_snapshot([], health, NOW+timedelta(hours=1), previous))
    assert len(output["events"]) == 1 and "cached_public_data" in output["events"][0]["tags"]
    assert next(source for source in output["sources"] if source["id"] == "usgs")["status"] == "error"


def test_empty_partial_batch_does_not_count_as_success_when_other_sources_also_failed():
    health = [{**item, "status": "partial" if item["id"] == "usgs" else "error",
               "record_count": 0, "last_success_at": None} for item in sources()]
    with pytest.raises(ValueError, match="All public sources failed"):
        public.encode_snapshot([], health, NOW)


def test_failure_retains_only_previous_public_data_with_unchanged_evidence_clocks():
    previous = json.loads(public.encode_snapshot([record()], sources(), NOW))
    output = json.loads(public.encode_snapshot([], failed_source(), NOW+timedelta(hours=1), previous))
    saved = output["events"][0]
    assert saved == {**previous["events"][0], "tags": sorted(set(previous["events"][0]["tags"]) | {"cached_public_data"})}
    failed = next(source for source in output["sources"] if source["id"] == "usgs")
    assert failed["status"] == "error" and failed["record_count"] == 1
    assert failed["last_success_at"] == previous["sources"][0]["last_success_at"]
    assert "oryginalnymi datami" in failed["error"]
    assert output["generated_at"] != previous["generated_at"]


def test_legacy_three_source_publication_can_supply_an_error_fallback():
    previous = json.loads(public.encode_snapshot([record()], sources(), NOW))
    previous["sources"] = [source for source in previous["sources"] if source["id"] in {"usgs", "meteoalarm", "cisa_kev"}]
    assert len(json.loads(public.encode_snapshot([], failed_source(), NOW, previous))["events"]) == 1


def test_successful_empty_or_partial_read_does_not_resurrect_previous_records():
    previous = json.loads(public.encode_snapshot([record()], sources(), NOW))
    for status in ("ok", "ok_empty", "partial", "stale"):
        health = [{**item, "status": status} for item in sources()]
        assert json.loads(public.encode_snapshot([], health, NOW, previous))["events"] == []


def test_fresh_provider_record_wins_over_previous_version_even_with_different_hash():
    previous = json.loads(public.encode_snapshot([record()], sources(), NOW))
    current = record(title="New source revision")
    current["evidence"][0]["payload_hash"] = "b" * 64
    output = json.loads(public.encode_snapshot([current], sources(), NOW, previous))
    assert len(output["events"]) == 1 and output["events"][0]["title"] == "New source revision"
    assert "cached_public_data" not in output["events"][0]["tags"]


def test_mixed_source_record_is_not_misrepresented_as_one_failed_sources_observation():
    combined = record(source_ids=["usgs", "gdacs"], source_count=2)
    combined["evidence"].append({**combined["evidence"][0], "source_id": "gdacs", "provider_record_id": "gdacs-1"})
    previous = json.loads(public.encode_snapshot([combined], sources(), NOW))
    output = json.loads(public.encode_snapshot([], failed_source("gdacs"), NOW, previous))
    assert output["events"] == []


def test_cached_relations_cannot_point_at_omitted_records():
    related = record(SECOND_ID, "second-record", source_ids=["cisa_kev"], category="cyber")
    related["evidence"][0]["source_id"] = "cisa_kev"
    edge = {"event_id": SECOND_ID, "title": "Related", "relation_type": "possible_same_event",
            "reason": "Test, not confirmation.", "distance_km": None, "time_delta_hours": 1}
    previous = json.loads(public.encode_snapshot([record(relations=[edge]), related], sources(), NOW))
    output = json.loads(public.encode_snapshot([], failed_source(), NOW, previous))
    assert len(output["events"]) == 1 and output["events"][0]["relations"] == []


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(private_configuration={"secret": "not-public"}),
    lambda p: p["sources"][0].update(id="cloudflare_radar"),
    lambda p: p["sources"][0].update(requires_key=True),
    lambda p: p["events"][0].update(id=EVENT_ID),
    lambda p: p["events"][0].update(internal_private_field="secret"),
    lambda p: p["events"][0].update(source_count=2),
    lambda p: p["events"][0].update(source_url="http://localhost/private"),
    lambda p: p["events"][0].update(geometry={"type": "Point", "coordinates": [20, 52], "raw": {"private": "data"}}),
    lambda p: p["events"][0].update(geometry={"type": "Point", "coordinates": [True, 52]}),
    lambda p: p["events"][0].update(geometry={"type": "Point", "coordinates": [181, 52]}),
    lambda p: p["events"][0].update(geometry={"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [20, 52], "raw": "private"}]}),
    lambda p: p["events"][0]["evidence"][0].update(raw={"private": "data"}),
    lambda p: p["events"][0]["evidence"][0].update(raw_retained=True),
    lambda p: p["events"][0]["evidence"][0].update(id=SECOND_ID),
    lambda p: p["events"][0]["evidence"][0].update(retrieved_at="2026-08-26T21:00:00"),
    lambda p: p["events"][0]["evidence"][0].update(license_url="https://user:secret@example.org/"),
    lambda p: p["events"][0]["evidence"][0].update(unreviewed_field="private"),
    lambda p: p.update(generated_at="2026-08-26T21:00:00"),
    lambda p: p.update(generated_at=(NOW+timedelta(hours=1)).isoformat()),
])
def test_previous_publication_boundary_rejects_private_fields_identity_and_invalid_clocks(mutation):
    previous = json.loads(public.encode_snapshot([record()], sources(), NOW))
    mutation(previous)
    with pytest.raises(ValueError):
        public.validate_previous_snapshot(previous, NOW)


@pytest.mark.parametrize("site_url", [
    "", "http://jakiesluchawki.github.io/sextet-monitor/", "file:///private/snapshot.json",
    "https://localhost/", "https://127.0.0.1/", "https://example.org/",
    "https://github.io.evil.test/", "https://user:secret@jakiesluchawki.github.io/",
    "https://jakiesluchawki.github.io:443/", "https://jakiesluchawki.github.io/a/../",
    "https://jakiesluchawki.github.io/%2e%2e/", "https://jakiesluchawki.github.io/a/?token=x",
    "https://jakiesluchawki.github.io/a/#private", "https://jakiesluchawki.github.io/a",
])
def test_previous_publication_url_never_reaches_private_or_unapproved_destinations(site_url):
    with pytest.raises(ValueError):
        public.previous_snapshot_url(site_url)


def test_previous_publication_url_is_exactly_the_configured_pages_subpath():
    assert public.previous_snapshot_url("https://jakiesluchawki.github.io/sextet-monitor/") == (
        "https://jakiesluchawki.github.io/sextet-monitor/snapshot.json")


@pytest.mark.parametrize("status,body", [
    (200, b"not JSON"), (404, b"not found"), (302, b"redirect"),
])
async def test_invalid_previous_download_does_not_prevent_fresh_collection(monkeypatch, status, body):
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(status, content=body, headers={"location": "http://127.0.0.1/private"})
    original = httpx.AsyncClient
    monkeypatch.setattr(public.httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(public, "validate_addresses", lambda host: None)
    assert await public.load_previous_snapshot("https://jakiesluchawki.github.io/sextet-monitor/", NOW) is None
    assert calls == ["https://jakiesluchawki.github.io/sextet-monitor/snapshot.json"]


async def test_previous_download_validates_dns_and_does_not_use_local_file_fallback(monkeypatch):
    def private_address(host):
        raise ValueError("private DNS")
    monkeypatch.setattr(public, "validate_addresses", private_address)
    assert await public.load_previous_snapshot("https://jakiesluchawki.github.io/sextet-monitor/", NOW) is None
    assert await public.load_previous_snapshot("", NOW) is None
