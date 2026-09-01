"""Privacy and integrity boundaries of independently generated public data."""
from copy import deepcopy
import json

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
    assert len(output["sources"]) == 3


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
