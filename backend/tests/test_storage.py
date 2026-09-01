"""Regression checks against a fresh PostGIS DB, never the live database."""
from datetime import datetime, timedelta, timezone
import os
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from monitor.contracts import EventQuery, NormalizedEvent, ProviderBatch
from monitor.db import (
    event_detail, get_source_health, latest_briefing, save_briefing, seed_sources,
    select_briefing_events, select_events,
)
from monitor.ingestion import (
    LeaseLost, apply_retention, claim_source, expire_advisories,
    fail_source, persist_batch,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Use manage.py test for an isolated PostGIS database.")


@pytest.fixture
def db():
    assert URL and make_url(URL).database.startswith("monitor_test_"), "Tests require a dedicated database."
    engine = create_engine(URL)
    with engine.connect() as conn:
        tx = conn.begin()
        seed_sources(conn)
        yield conn
        tx.rollback()
    engine.dispose()


def event(record="fixture-1", source="usgs", **changes):
    values = dict(
        source_id=source, provider_record_id=record, kind="incident", category="earthquake",
        title="Testowy raport sejsmiczny", source_url="https://earthquake.usgs.gov/earthquakes/eventpage/" + record,
        occurred_start=NOW - timedelta(hours=1), source_updated_at=NOW - timedelta(minutes=5),
        geometry={"type": "Point", "coordinates": [21.0122, 52.2297]},
        location_precision="point", time_precision="second", severity=2,
        origins=["usgs:us"], external_ids=["usgs:" + record],
    )
    values.update(changes)
    values["raw"] = {key: str(value) for key, value in values.items() if key != "raw"}
    return NormalizedEvent(**values)


def ingest(db, source, events, now=NOW, **batch_fields):
    if source == "cisa_kev":
        batch_fields.setdefault("metadata", {"provider_timestamp": now.isoformat()})
    lease = claim_source(db, source, force=True, now=now)
    assert lease
    return persist_batch(db, lease, ProviderBatch(events=events, **batch_fields), now=now)


def count(db, table):
    assert table in {"events", "observations", "event_revisions", "event_relations"}
    return db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def detail(db, source="usgs", record="fixture-1"):
    id = db.execute(text("SELECT event_id FROM provider_records WHERE source_id=:source AND provider_record_id=:record"),
                    {"source": source, "record": record}).scalar_one()
    return event_detail(db, str(id), now=NOW)


def test_idempotence_and_last_seen_is_not_last_changed(db):
    first = ingest(db, "usgs", [event()])
    second = ingest(db, "usgs", [event()], NOW + timedelta(minutes=1))
    assert first["changed_events"] == 1
    assert second["new_observations"] == second["changed_events"] == 0
    assert count(db, "events") == count(db, "observations") == count(db, "event_revisions") == 1
    row = detail(db)
    assert row["last_changed_at"] == NOW
    assert row["last_seen_at"] == NOW + timedelta(minutes=1)
    assert row["countries"] == ["PL"]


def test_revision_preserves_immutable_evidence(db):
    original = event()
    ingest(db, "usgs", [original])
    ingest(db, "usgs", [event(title="Skorygowany raport", source_updated_at=NOW)], NOW + timedelta(minutes=1))
    row = detail(db)
    assert row["title"] == "Skorygowany raport"
    assert count(db, "events") == 1 and count(db, "observations") == 2 and len(row["revisions"]) == 2
    assert row["evidence"][0]["raw"]["title"] == "Skorygowany raport"


def test_mirror_does_not_double_independent_sources(db):
    ingest(db, "usgs", [event()])
    mirror = event("EQ:123", "gdacs", origins=["usgs"], external_ids=["usgs:fixture-1", "gdacs:EQ:123"])
    ingest(db, "gdacs", [mirror], NOW + timedelta(seconds=1))
    row = detail(db)
    assert count(db, "events") == 1
    assert row["source_ids"] == ["gdacs", "usgs"] and row["independent_source_count"] == 1
    query = EventQuery(since=NOW-timedelta(hours=2), until=NOW, min_sources=2)
    assert select_events(db, query, NOW)["total"] == 0


def test_similar_distinct_quakes_remain_separate_with_relation(db):
    ingest(db, "usgs", [event("a"), event("b", occurred_start=NOW-timedelta(minutes=58))])
    assert count(db, "events") == 2
    assert count(db, "event_relations") == 1
    row = detail(db, record="a")
    assert row["relations"][0]["relation_type"] == "possible_same_event"
    assert "nie scalono" in row["relations"][0]["reason"].lower()


def test_source_error_preserves_data_and_watermark(db):
    ingest(db, "usgs", [event()])
    before = db.execute(text("SELECT last_success_at,cursor,record_count FROM sources WHERE id='usgs'")).mappings().one()
    lease = claim_source(db, "usgs", force=True, now=NOW+timedelta(minutes=1))
    result = fail_source(db, lease, "Kontrolowany timeout", now=NOW+timedelta(minutes=1), retry_after=1800)
    after = db.execute(text("SELECT last_success_at,cursor,record_count FROM sources WHERE id='usgs'")).mappings().one()
    assert dict(before) == dict(after) and count(db, "events") == 1
    assert result["retry_seconds"] >= 1800
    assert next(s for s in get_source_health(db, NOW) if s["id"]=="usgs")["status"] == "error"


def test_partial_feed_is_not_empty_success_or_cursor_advance(db):
    ingest(db, "usgs", [event()])
    before = db.execute(text("SELECT cursor FROM sources WHERE id='usgs'")).scalar_one()
    result = ingest(db, "usgs", [], NOW+timedelta(minutes=1), rejected_count=1, warnings=["Uszkodzony rekord"])
    assert result["status"] == "partial" and count(db, "events") == 1
    assert db.execute(text("SELECT cursor FROM sources WHERE id='usgs'")).scalar_one() == before


def test_genuinely_empty_feed_is_distinct_from_error(db):
    result = ingest(db, "usgs", [])
    assert result["status"] == "ok_empty"
    assert next(s for s in get_source_health(db, NOW) if s["id"]=="usgs")["record_count"] == 0


def test_expired_lease_cannot_publish_after_successor(db):
    old = claim_source(db, "usgs", force=True, now=NOW, lease_seconds=1)
    new = claim_source(db, "usgs", force=True, now=NOW+timedelta(seconds=2))
    persist_batch(db, new, ProviderBatch(events=[event("new")]), now=NOW+timedelta(seconds=2))
    with pytest.raises(LeaseLost):
        persist_batch(db, old, ProviderBatch(events=[event("old")]), now=NOW+timedelta(seconds=3))
    assert count(db, "events") == 1
    assert detail(db, record="new")["title"]


def test_same_active_lease_cannot_be_claimed_twice(db):
    assert claim_source(db, "usgs", force=True, now=NOW)
    assert claim_source(db, "usgs", force=True, now=NOW) is None


def test_older_gdacs_episode_does_not_replace_current_or_current_evidence(db):
    current = event("EQ:123", "gdacs", source_updated_at=NOW, origins=["unknown:gdacs"])
    current.raw.update(is_current=True, episode_id="2")
    old = event("EQ:123", "gdacs", title="Stary epizod", source_updated_at=NOW-timedelta(hours=1), origins=["unknown:gdacs"])
    old.raw.update(is_current=False, episode_id="1")
    ingest(db, "gdacs", [current])
    ingest(db, "gdacs", [old], NOW+timedelta(minutes=1))
    row = detail(db, "gdacs", "EQ:123")
    assert row["title"] == current.title
    assert row["evidence"][0]["raw"]["is_current"] is True


def cap(id, *, sender="sender@imgw.pl", references=(), withdrawn=False, **changes):
    record = event(
        id, "meteoalarm", kind="advisory", category="weather",
        source_url="https://feeds.meteoalarm.org/api/v1/warnings/" + id,
        title="Ostrzeżenie testowe", source_updated_at=NOW-timedelta(minutes=5),
        issued_at=NOW-timedelta(minutes=5), valid_from=NOW-timedelta(minutes=5), valid_to=NOW+timedelta(hours=2),
        origins=["cap_sender:" + sender], external_ids=["cap:"+sender+":"+id],
        supersedes=list(references), lifecycle_status="withdrawn" if withdrawn else "active", **changes,
    )
    record.raw["sender"] = sender
    return record


def test_cap_reference_is_sender_scoped(db):
    ingest(db, "meteoalarm", [cap("alert", sender="first@example.org")])
    cancel = cap("cancel", sender="second@example.org", references=["alert"], withdrawn=True)
    ingest(db, "meteoalarm", [cancel], NOW+timedelta(seconds=1))
    assert count(db, "events") == 2
    assert detail(db, "meteoalarm", "alert")["lifecycle_status"] == "active"


def test_cap_cancel_precedes_late_alert_even_at_identical_source_time(db):
    cancel = cap("cancel", references=["alert"], withdrawn=True)
    ingest(db, "meteoalarm", [cancel])
    ingest(db, "meteoalarm", [cap("alert")], NOW+timedelta(seconds=1))
    assert count(db, "events") == 1
    assert detail(db, "meteoalarm", "alert")["lifecycle_status"] == "withdrawn"


def test_conflicting_hard_ids_do_not_merge_events(db):
    ingest(db, "usgs", [event("a"), event("b")])
    conflict = event("EQ:123", "gdacs", external_ids=["usgs:a", "usgs:b"])
    result = ingest(db, "gdacs", [conflict], NOW+timedelta(seconds=1))
    assert result["status"] == "partial" and count(db, "events") == 2


def test_expiry_has_revision_but_is_not_another_read(db):
    warning = cap("alert")
    warning.valid_to = NOW+timedelta(minutes=1)
    ingest(db, "meteoalarm", [warning])
    assert expire_advisories(db, NOW+timedelta(minutes=2)) == 1
    row = detail(db, "meteoalarm", "alert")
    assert row["lifecycle_status"] == "expired" and row["change_type"] == "expired"
    assert row["last_seen_at"] == NOW and row["last_changed_at"] == NOW+timedelta(minutes=2)
    assert expire_advisories(db, NOW+timedelta(minutes=3)) == 0


def test_radius_uses_metres_and_excludes_country_or_representative_points(db):
    exact = event("exact")
    distant = event("distant", geometry={"type":"Point","coordinates":[-74.006,40.7128]})
    approximate = event("approx", location_precision="area")
    ingest(db, "usgs", [exact, distant, approximate])
    query = EventQuery(since=NOW-timedelta(hours=2), until=NOW, lat=52.23, lon=21.01, radius_km=800)
    result = select_events(db, query, NOW)
    assert result["total"] == 1
    assert result["items"][0]["provider_record_id"] == "exact"


def test_unknown_times_and_geometry_are_not_fabricated(db):
    notice = event("CVE-test", "cisa_kev", kind="vulnerability_notice", category="cyber",
                   geometry=None, location_precision="unknown", occurred_start=None, issued_at=NOW-timedelta(hours=1))
    ingest(db, "cisa_kev", [notice])
    row = detail(db, "cisa_kev", "CVE-test")
    assert row["geometry"] is None and row["occurred_start"] is None
    assert select_events(db, EventQuery(since=NOW-timedelta(hours=2), until=NOW), NOW)["total"] == 0
    assert select_events(db, EventQuery(time_basis="changed", since=NOW-timedelta(hours=2),
                                       until=NOW+timedelta(seconds=1)), NOW)["unlocated"] == 1


def test_half_open_time_window(db):
    ingest(db, "usgs", [event("atstart", occurred_start=NOW-timedelta(hours=1)), event("atend", occurred_start=NOW)])
    result = select_events(db, EventQuery(since=NOW-timedelta(hours=1), until=NOW), NOW)
    assert {e["provider_record_id"] for e in result["items"]} == {"atstart"}


def test_country_geometry_is_area_not_fake_aviation_point(db):
    bulletin = event("czib", "easa_czib", kind="advisory", category="aviation", countries=["PL"],
                     geometry=None, location_precision="country", occurred_start=None)
    ingest(db, "easa_czib", [bulletin])
    row = detail(db, "easa_czib", "czib")
    assert row["geometry"]["type"] in {"Polygon","MultiPolygon"}
    assert row["location_precision"] == "country" and "country_geometry_not_fir" in row["tags"]


def test_initial_catalog_import_does_not_drown_briefing(db):
    old = event("old-cve", "cisa_kev", kind="vulnerability_notice", category="cyber",
                geometry=None, occurred_start=None, issued_at=NOW-timedelta(days=90), source_updated_at=None)
    recent = event("new-cve", "cisa_kev", kind="vulnerability_notice", category="cyber",
                   geometry=None, occurred_start=None, issued_at=NOW-timedelta(minutes=30), source_updated_at=None)
    ingest(db, "cisa_kev", [old,recent])
    result = select_briefing_events(db, EventQuery(time_basis="changed", since=NOW-timedelta(hours=1),
                                    until=NOW+timedelta(seconds=1), include_inactive=True), now=NOW)
    assert result["initial_import_background_count"] == 1
    assert result["total"] == 1 and result["items"][0]["provider_record_id"] == "new-cve"


def test_briefing_cursor_matches_scope_and_reader_can_only_save_briefings(db):
    body = {"generated_at":NOW.isoformat(), "since":(NOW-timedelta(hours=1)).isoformat(), "until":NOW.isoformat(),
            "answer":"Kontrolowany test", "facts":[]}
    with db.begin_nested():
        db.execute(text("SET LOCAL ROLE monitor_reader"))
        saved = save_briefing(db, body, country="PL", window_hours=24)
        assert latest_briefing(db, country="PL", window_hours=24)["id"] == saved["id"]
        assert latest_briefing(db, country="DE", window_hours=24) is None
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(text("DELETE FROM sources"))
        db.execute(text("RESET ROLE"))


def test_retention_erases_raw_without_losing_current_provenance(db):
    old_now = NOW - timedelta(days=35)
    current = event("EQ:123", "gdacs", occurred_start=old_now-timedelta(hours=1),
                    source_updated_at=old_now-timedelta(minutes=1), origins=["unknown:gdacs"])
    current.raw.update(is_current=True, episode_id="3")
    ingest(db, "gdacs", [current], old_now)
    apply_retention(db, NOW)
    row = detail(db, "gdacs", "EQ:123")
    assert row["evidence"][0]["raw"] is None and not row["evidence"][0]["raw_retained"]
    old = event("EQ:123", "gdacs", title="Archiwalny epizod", source_updated_at=old_now-timedelta(days=1))
    old.raw.update(is_current=False, episode_id="1")
    ingest(db, "gdacs", [old], NOW)
    assert detail(db, "gdacs", "EQ:123")["title"] == current.title


def test_impossible_future_source_update_is_quarantined(db):
    result = ingest(db, "usgs", [event(source_updated_at=NOW+timedelta(days=10))])
    assert result["status"] == "partial" and result["rejected"] == 1 and count(db, "events") == 0


def test_disabled_radar_has_no_claim_and_expired_success_is_stale(db):
    assert claim_source(db, "cloudflare_radar", force=True, now=NOW) is None
    ingest(db, "usgs", [event()])
    source = next(s for s in get_source_health(db, NOW+timedelta(hours=2)) if s["id"]=="usgs")
    assert source["status"] == "stale"


def test_normalizer_revision_preserves_raw_and_can_reprocess_same_payload(db, monkeypatch):
    import monitor.ingestion as ingestion
    first = event()
    ingest(db, "usgs", [first])
    second = first.model_copy(update={"title": "Poprawiona normalizacja"})
    monkeypatch.setattr(ingestion, "NORMALIZER_VERSION", "2")
    ingest(db, "usgs", [second], NOW+timedelta(seconds=1))
    versions = list(db.execute(text("SELECT payload_hash,normalizer_version,normalized FROM observations ORDER BY normalizer_version")).mappings())
    assert len(versions) == 2 and versions[0]["payload_hash"] == versions[1]["payload_hash"]
    assert versions[0]["normalized"]["title"] == first.title
    assert detail(db)["title"] == "Poprawiona normalizacja"
    assert ingest(db, "usgs", [second], NOW+timedelta(seconds=2))["changed_events"] == 0


def test_counts_and_newest_time_only_follow_accepted_records(db):
    good = event("same", "gdacs", source_updated_at=NOW)
    bad = event("same", "gdacs", source_updated_at=NOW+timedelta(days=10))
    result = ingest(db, "gdacs", [good, bad])
    assert result["records"] == 1 and result["rejected"] == 1
    older = event("same", "gdacs", title="Stara wersja", source_updated_at=NOW-timedelta(hours=1))
    ingest(db, "gdacs", [older], NOW+timedelta(seconds=1))
    assert db.execute(text("SELECT newest_content_at FROM sources WHERE id='gdacs'")).scalar_one() == NOW


def test_wide_disaster_identifier_is_not_a_merge_key(db):
    first = event("EQ:a", "gdacs", external_ids=["gdacs:EQ:a", "glide:EQ-2026-000001-POL"])
    second = event("EQ:b", "gdacs", external_ids=["gdacs:EQ:b", "glide:EQ-2026-000001-POL"])
    ingest(db, "gdacs", [first,second])
    assert count(db, "events") == 2


def test_first_briefing_includes_real_recent_occurrence_without_publication(db):
    ingest(db, "usgs", [event()])
    result = select_briefing_events(db, EventQuery(time_basis="changed", since=NOW-timedelta(hours=2),
                                    until=NOW+timedelta(seconds=1), include_inactive=True), now=NOW)
    assert result["total"] == 1 and result["initial_import_background_count"] == 0
    assert result["items"][0]["issued_at"] is None


def test_active_incident_without_end_survives_history_retention(db):
    before = NOW-timedelta(days=200)
    ongoing = event("ongoing", "gdacs", category="disaster", occurred_start=before-timedelta(days=1),
                    occurred_end=None, source_updated_at=before, lifecycle_status="active")
    ingest(db, "gdacs", [ongoing], before)
    apply_retention(db, NOW)
    assert count(db, "events") == 1
    assert detail(db, "gdacs", "ongoing")["lifecycle_status"] == "active"


def test_partial_rate_limit_does_not_schedule_before_retry_after(db):
    ingest(db, "meteoalarm", [cap("warning")], rejected_count=1,
           metadata={"partial": True, "retry_after_seconds": 7200})
    next_due = db.execute(text("SELECT next_due_at FROM sources WHERE id='meteoalarm'")).scalar_one()
    assert next_due >= NOW+timedelta(seconds=7200)


def test_stale_usgs_generated_time_is_not_fresh_despite_successful_http(db):
    result = ingest(db, "usgs", [event()], metadata={"provider_timestamp":(NOW-timedelta(hours=1)).isoformat()})
    assert result["status"] == "stale"
    source = db.execute(text("SELECT last_success_at,cursor,error FROM sources WHERE id='usgs'")).mappings().one()
    assert source["last_success_at"] == NOW and source["cursor"] == {}
    assert "20 minut" in source["error"]


def test_interrupted_worker_releases_only_its_own_lease_without_false_source_error(db):
    from monitor.ingestion import release_interrupted_lease
    ingest(db, "usgs", [event()])
    old = claim_source(db, "usgs", force=True, now=NOW+timedelta(minutes=1))
    assert release_interrupted_lease(db, old, NOW+timedelta(minutes=1))
    new = claim_source(db, "usgs", force=True, now=NOW+timedelta(minutes=1))
    assert new and not release_interrupted_lease(db, old, NOW+timedelta(minutes=1))
    assert db.execute(text("SELECT status FROM sources WHERE id='usgs'")).scalar_one() == "ok"
    assert count(db, "events") == 1


def scheduled_cap():
    warning = cap("scheduled").model_copy(update={
        "lifecycle_status": "unknown", "valid_from": NOW+timedelta(hours=1),
        "occurred_start": NOW+timedelta(hours=1), "valid_to": NOW+timedelta(hours=4),
        "tags": ["cap", "hazard_onset_in_future"],
    })
    warning.raw.update(valid_from=warning.valid_from.isoformat(), valid_to=warning.valid_to.isoformat(),
                       occurred_start=warning.occurred_start.isoformat())
    return warning


def test_same_cap_raw_activates_on_next_poll_without_rewriting_observation(db):
    warning = scheduled_cap()
    ingest(db, "meteoalarm", [warning])
    parsed_later = warning.model_copy(update={"lifecycle_status": "active", "tags": ["cap"]})
    result = ingest(db, "meteoalarm", [parsed_later], NOW+timedelta(hours=2))
    row = db.execute(text("SELECT * FROM events")).mappings().one()
    immutable = db.execute(text("SELECT normalized,raw FROM observations")).mappings().one()
    assert result["new_observations"] == 0 and result["changed_events"] == 1
    assert row["lifecycle_status"] == "active" and "hazard_onset_in_future" not in row["normal"]["tags"]
    assert immutable["normalized"]["lifecycle_status"] == "unknown" and immutable["raw"] == warning.raw
    assert count(db, "observations") == 1 and count(db, "event_revisions") == 2


def test_cap_maintenance_activates_then_expires_without_faking_another_read(db):
    ingest(db, "meteoalarm", [scheduled_cap()])
    assert expire_advisories(db, NOW+timedelta(hours=2)) == 1
    row = db.execute(text("SELECT * FROM events")).mappings().one()
    assert row["lifecycle_status"] == "active" and row["last_seen_at"] == NOW
    assert row["last_changed_at"] == NOW+timedelta(hours=2)
    assert expire_advisories(db, NOW+timedelta(hours=3)) == 0
    assert expire_advisories(db, NOW+timedelta(hours=4)) == 1
    row = db.execute(text("SELECT * FROM events")).mappings().one()
    assert row["lifecycle_status"] == "expired" and row["last_seen_at"] == NOW
    assert count(db, "observations") == 1 and count(db, "event_revisions") == 3


def test_swpc_forecast_maintenance_preserves_original_evidence_and_read_clock(db):
    warning = NormalizedEvent(
        source_id="noaa_swpc", provider_record_id="ALTK06-test", kind="advisory", category="space_weather",
        title="Synthetic SWPC forecast", source_url="https://www.swpc.noaa.gov/products/alerts-watches-and-warnings",
        issued_at=NOW, valid_from=NOW+timedelta(hours=1), valid_to=NOW+timedelta(hours=4),
        lifecycle_status="unknown", origins=["noaa:swpc"], tags=["forecast", "advisory"],
        raw={"product_id": "ALTK06-test", "issue_datetime": NOW.isoformat()},
    )
    ingest(db, "noaa_swpc", [warning])
    assert expire_advisories(db, NOW+timedelta(hours=2)) == 1
    row = db.execute(text("SELECT * FROM events")).mappings().one()
    assert row["lifecycle_status"] == "active" and row["last_seen_at"] == NOW and row["occurred_start"] is None
    assert expire_advisories(db, NOW+timedelta(hours=4)) == 1
    immutable = db.execute(text("SELECT normalized,raw FROM observations")).mappings().one()
    assert immutable["normalized"]["lifecycle_status"] == "unknown" and immutable["raw"] == warning.raw
    assert count(db, "observations") == 1 and count(db, "event_revisions") == 3


def usgs_batch(kind="earthquake", revision=0, record_id="us-test-quake", aliases=None):
    from monitor.contracts import FetchedDocument
    from monitor.providers import usgs
    clock = NOW+timedelta(minutes=revision)
    data = {
        "type": "FeatureCollection", "metadata": {"count": 1, "generated": clock.timestamp()*1000},
        "features": [{
            "type": "Feature", "id": record_id,
            "properties": {
                "type": kind, "title": "M 4.2 — kontrolowany rekord testowy", "place": "Test",
                "time": (NOW-timedelta(hours=1)).timestamp()*1000, "updated": clock.timestamp()*1000,
                "mag": 4.2, "magType": "mb", "status": "reviewed", "net": "us",
                "ids": "," + ",".join(aliases or [record_id]) + ",",
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/" + record_id,
            },
            "geometry": {"type": "Point", "coordinates": [21.0122, 52.2297, 10]},
        }],
    }
    return usgs.parse(FetchedDocument(json.dumps(data).encode(), "application/json", usgs.URL,
                                     fetched_at=NOW+timedelta(minutes=revision)))


def test_usgs_reclassification_withdraws_only_known_record_and_old_quake_cannot_revive_it(db):
    first = usgs_batch()
    ingest(db, "usgs", first.events, metadata=first.metadata)
    correction = usgs_batch("quarry blast", 1)
    result = ingest(db, "usgs", correction.events, NOW+timedelta(minutes=1), metadata=correction.metadata)
    row = detail(db, record=first.events[0].provider_record_id)
    assert result["records"] == 1 and result["changed_events"] == 1
    assert row["lifecycle_status"] == "withdrawn" and row["change_type"] == "withdrawn"
    assert row["evidence"][0]["raw"]["properties"]["type"] == "quarry blast"
    assert count(db, "events") == 1 and count(db, "observations") == 2
    repeated = ingest(db, "usgs", first.events, NOW+timedelta(minutes=2), metadata=first.metadata)
    assert repeated["changed_events"] == repeated["new_observations"] == 0
    assert detail(db, record=first.events[0].provider_record_id)["lifecycle_status"] == "withdrawn"


def test_never_seen_non_earthquake_signal_does_not_create_an_event_or_observation(db):
    batch = usgs_batch("quarry blast")
    result = ingest(db, "usgs", batch.events, metadata=batch.metadata)
    assert result["status"] == "ok_empty" and result["records"] == 0
    assert count(db, "events") == count(db, "observations") == 0


def test_stale_usgs_reclassification_cannot_withdraw_newer_quake(db):
    current = usgs_batch(revision=1)
    ingest(db, "usgs", current.events, NOW+timedelta(minutes=1), metadata=current.metadata)
    earlier = usgs_batch("quarry blast")
    result = ingest(db, "usgs", earlier.events, NOW+timedelta(minutes=2), metadata=earlier.metadata)
    assert result["changed_events"] == 0
    assert detail(db, record=current.events[0].provider_record_id)["lifecycle_status"] == "active"


def test_new_normalizer_can_correct_current_payload_timestamp_backwards(db, monkeypatch):
    import monitor.ingestion as ingestion
    monkeypatch.setattr(ingestion, "NORMALIZER_VERSION", "1")
    first = event(source_updated_at=NOW-timedelta(minutes=5))
    ingest(db, "usgs", [first])
    corrected = first.model_copy(update={"source_updated_at": NOW-timedelta(hours=1), "title": "Poprawna normalizacja"})
    monkeypatch.setattr(ingestion, "NORMALIZER_VERSION", "2")
    ingest(db, "usgs", [corrected], NOW+timedelta(minutes=1))
    row = detail(db)
    assert row["title"] == corrected.title and row["source_updated_at"] == corrected.source_updated_at
    versions = list(db.execute(text("SELECT normalizer_version,payload_hash FROM observations ORDER BY normalizer_version")).mappings())
    assert [record["normalizer_version"] for record in versions] == ["1", "2"]
    assert len({record["payload_hash"] for record in versions}) == 1


def test_normalizer_upgrade_cannot_make_a_different_historical_payload_current(db, monkeypatch):
    import monitor.ingestion as ingestion
    monkeypatch.setattr(ingestion, "NORMALIZER_VERSION", "1")
    old = event(title="Stara treść", source_updated_at=NOW-timedelta(hours=2))
    current = event(title="Nowsza treść", source_updated_at=NOW-timedelta(hours=1))
    ingest(db, "usgs", [old])
    ingest(db, "usgs", [current], NOW+timedelta(minutes=1))
    monkeypatch.setattr(ingestion, "NORMALIZER_VERSION", "2")
    ingest(db, "usgs", [old], NOW+timedelta(minutes=2))
    assert detail(db)["title"] == current.title


def test_cap_cycle_rolls_back_bad_edge_not_independent_good_warning(db):
    original = cap("a", references=["b"])
    ingest(db, "meteoalarm", [original])
    before = detail(db, "meteoalarm", "a")
    result = ingest(db, "meteoalarm", [cap("b", references=["a"]), cap("independent")],
                    NOW+timedelta(seconds=1))
    assert result["status"] == "partial" and result["rejected"] == 1 and result["records"] == 1
    assert count(db, "events") == count(db, "observations") == 2
    assert detail(db, "meteoalarm", "a")["last_changed_at"] == before["last_changed_at"]
    assert detail(db, "meteoalarm", "independent")["lifecycle_status"] == "active"
    assert db.execute(text("SELECT count(*) FROM provider_records WHERE provider_record_id='b'")).scalar_one() == 0


def test_multiple_gdacs_episodes_keep_initial_import_classification_in_one_batch(db):
    older = event("EQ:999", "gdacs", source_updated_at=NOW-timedelta(hours=1))
    older.raw.update(is_current=False, episode_id="1")
    current = event("EQ:999", "gdacs", source_updated_at=NOW)
    current.raw.update(is_current=True, episode_id="2")
    result = ingest(db, "gdacs", [older, current])
    assert result["changed_events"] == 1 and result["records"] == 1
    assert detail(db, "gdacs", "EQ:999")["change_type"] == "initial_import"


def kev(record, title):
    return event(record, "cisa_kev", title=title, kind="vulnerability_notice", category="cyber",
                 source_url="https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
                 occurred_start=None, source_updated_at=None, issued_at=NOW-timedelta(days=5),
                 geometry=None, location_precision="unknown", time_precision="day",
                 tags=["date_only_utc_anchor"], origins=["cisa"], external_ids=["cve:"+record])


def catalog(db, records, snapshot, offset=0, **fields):
    return ingest(db, "cisa_kev", records, NOW+timedelta(seconds=offset),
                  metadata={"provider_timestamp": snapshot.isoformat() if snapshot else None}, **fields)


def test_newer_cisa_catalog_can_revert_A_B_A_but_older_snapshot_cannot(db):
    original, correction = kev("CVE-2026-0001", "A"), kev("CVE-2026-0001", "B")
    catalog(db, [original], NOW-timedelta(hours=2))
    catalog(db, [correction], NOW-timedelta(hours=1), 1)
    reverted = catalog(db, [original], NOW, 2)
    assert reverted["new_observations"] == 0 and reverted["changed_events"] == 1
    row = detail(db, "cisa_kev", original.provider_record_id)
    assert row["title"] == "A" and row["source_updated_at"] is None
    assert count(db, "observations") == 2
    stale = catalog(db, [correction], NOW-timedelta(hours=1), 3)
    assert stale["status"] == "partial" and stale["changed_events"] == 0
    assert detail(db, "cisa_kev", original.provider_record_id)["title"] == "A"
    assert db.execute(text("SELECT source_snapshot_at FROM provider_records")).scalar_one() == NOW


def test_cisa_equal_snapshot_time_with_different_content_is_rejected(db):
    catalog(db, [kev("CVE-2026-0001", "A")], NOW)
    result = catalog(db, [kev("CVE-2026-0001", "B")], NOW, 1)
    assert result["status"] == "partial" and result["rejected"] == 1
    assert count(db, "observations") == 1 and detail(db, "cisa_kev", "CVE-2026-0001")["title"] == "A"


def test_cisa_new_snapshot_with_same_raw_advances_provenance_without_fake_revision(db):
    record = kev("CVE-2026-0001", "A")
    catalog(db, [record], NOW-timedelta(hours=1))
    result = catalog(db, [record], NOW, 1)
    assert result["changed_events"] == result["new_observations"] == 0
    assert count(db, "observations") == count(db, "event_revisions") == 1
    assert db.execute(text("SELECT source_snapshot_at FROM provider_records")).scalar_one() == NOW


def test_partial_cisa_snapshot_retry_can_finish_reversions_per_record(db):
    a0, b0 = kev("CVE-2026-0001", "A0"), kev("CVE-2026-0002", "B0")
    a1, b1 = kev("CVE-2026-0001", "A1"), kev("CVE-2026-0002", "B1")
    catalog(db, [a0,b0], NOW-timedelta(hours=2))
    catalog(db, [a1,b1], NOW-timedelta(hours=1), 1)
    previous_complete = db.execute(text("SELECT cursor->>'last_complete_at' FROM sources WHERE id='cisa_kev'")).scalar_one()
    partial = catalog(db, [a0], NOW, 2, rejected_count=1)
    assert partial["status"] == "partial"
    assert db.execute(text("SELECT cursor->>'last_complete_at' FROM sources WHERE id='cisa_kev'")).scalar_one() == previous_complete
    retried = catalog(db, [a0,b0], NOW, 3)
    assert retried["status"] == "ok" and retried["changed_events"] == 1
    assert detail(db, "cisa_kev", a0.provider_record_id)["title"] == "A0"
    assert detail(db, "cisa_kev", b0.provider_record_id)["title"] == "B0"
    assert list(db.execute(text("SELECT DISTINCT source_snapshot_at FROM provider_records")).scalars()) == [NOW]
    assert count(db, "observations") == 4


@pytest.mark.parametrize("snapshot", [None, NOW+timedelta(days=1)])
def test_cisa_missing_or_future_snapshot_cannot_replace_dated_current_record(db, snapshot):
    catalog(db, [kev("CVE-2026-0001", "A")], NOW)
    result = catalog(db, [kev("CVE-2026-0001", "B")], snapshot, 1)
    assert result["status"] == "partial" and result["changed_events"] == 0
    assert detail(db, "cisa_kev", "CVE-2026-0001")["title"] == "A"


def test_published_basis_includes_calendar_day_overlap_without_invented_occurrence(db):
    exact = kev("CVE-2026-0001", "Exact").model_copy(update={
        "issued_at": NOW-timedelta(hours=1), "tags": [], "time_precision": "second",
    })
    day = kev("CVE-2026-0002", "Day").model_copy(update={"issued_at": NOW.replace(hour=0)})
    unknown = kev("CVE-2026-0003", "Unknown").model_copy(update={"issued_at": None})
    boundary = kev("CVE-2026-0004", "Boundary").model_copy(update={
        "issued_at": NOW, "tags": [], "time_precision": "second",
    })
    catalog(db, [exact, day, unknown, boundary], NOW)
    result = select_events(db, EventQuery(time_basis="published", since=NOW-timedelta(hours=6), until=NOW), NOW)
    assert {row["provider_record_id"] for row in result["items"]} == {exact.provider_record_id, day.provider_record_id}
    assert all(row["occurred_start"] is None for row in result["items"])


def test_validity_basis_overlaps_historical_expired_and_open_ended_advisories(db):
    expired = event("expired", "easa_czib", kind="advisory", category="aviation",
                    occurred_start=None, valid_from=NOW-timedelta(days=1),
                    valid_to=NOW-timedelta(hours=1), lifecycle_status="expired")
    unknown = event("unknown", "easa_czib", kind="advisory", category="aviation",
                    occurred_start=None, valid_from=None, valid_to=NOW+timedelta(hours=1))
    open_ended = event("open", "easa_czib", kind="advisory", category="aviation",
                       occurred_start=None, valid_from=NOW-timedelta(days=1), valid_to=None)
    ingest(db, "easa_czib", [expired,unknown,open_ended])
    query = EventQuery(time_basis="validity", since=NOW-timedelta(hours=12), until=NOW-timedelta(hours=6),
                       include_inactive=True)
    result = select_events(db, query, NOW)
    assert {row["provider_record_id"] for row in result["items"]} == {"expired", "open"}
    active = select_events(db, query.model_copy(update={"include_inactive":False}), NOW)
    assert [row["provider_record_id"] for row in active["items"]] == ["open"]
    assert active["items"][0]["valid_to"] is None


def test_relations_for_ids_only_returns_edges_with_both_endpoints_in_scope(db):
    from monitor.db import relations_for_ids
    ingest(db, "usgs", [event("a"), event("b", occurred_start=NOW-timedelta(minutes=58)),
                        event("c", occurred_start=NOW-timedelta(minutes=57))])
    ids = {row["provider_record_id"]: str(row["event_id"]) for row in
           db.execute(text("SELECT provider_record_id,event_id FROM provider_records")).mappings()}
    together = relations_for_ids(db, [ids["a"],ids["b"]])
    assert [relation["event_id"] for relation in together[ids["a"]]] == [ids["b"]]
    assert [relation["event_id"] for relation in together[ids["b"]]] == [ids["a"]]
    assert relations_for_ids(db, [ids["a"]]) == {ids["a"]:[]}


def test_briefing_stream_reads_all_batches_then_closes_cursor_before_insert(db, monkeypatch):
    import monitor.db as storage
    monkeypatch.setattr(storage, "BRIEFING_BATCH_SIZE", 2)
    ingest(db, "usgs", [event("one"),event("two"),event("three")])
    query = EventQuery(time_basis="changed", since=NOW-timedelta(hours=1),
                       until=NOW+timedelta(seconds=1), include_inactive=True, limit=1)
    result = select_briefing_events(db, query, now=NOW, stream=True)
    assert result["total"] == 3
    records = list(result["items"])
    assert len(records) == 3 and len({row["id"] for row in records}) == 3
    saved = save_briefing(db, {"generated_at":NOW.isoformat(), "since":query.since.isoformat(),
                              "until":query.until.isoformat(), "answer":"Test strumienia", "facts":[]},
                         country=None, window_hours=24)
    assert saved["id"]


def test_usgs_reclassified_new_primary_id_with_explicit_known_alias_withdraws_same_event(db):
    first = usgs_batch(record_id="us-old-known")
    ingest(db, "usgs", first.events, metadata=first.metadata)
    original_id = detail(db, record="us-old-known")["id"]
    correction = usgs_batch("quarry blast", 1, record_id="us-new-primary",
                            aliases=["us-old-known", "us-new-primary"])
    result = ingest(db, "usgs", correction.events, NOW+timedelta(minutes=1), metadata=correction.metadata)
    row = detail(db, record="us-new-primary")
    current_evidence = next(item for item in row["evidence"] if item["provider_record_id"] == "us-new-primary")
    assert result["records"] == result["changed_events"] == 1
    assert count(db, "events") == 1 and row["id"] == original_id
    assert row["provider_record_id"] == "us-new-primary" and row["lifecycle_status"] == "withdrawn"
    assert row["independent_source_count"] == 1
    assert current_evidence["raw"]["properties"]["type"] == "quarry blast"


def test_usgs_conditional_withdrawal_with_conflicting_known_aliases_fails_closed(db):
    for record_id in ("us-known-a", "us-known-c"):
        batch = usgs_batch(record_id=record_id)
        ingest(db, "usgs", batch.events, metadata=batch.metadata)
    conflict = usgs_batch("quarry blast", 1, record_id="us-new-primary",
                         aliases=["us-known-a", "us-new-primary", "us-known-c"])
    result = ingest(db, "usgs", conflict.events, NOW+timedelta(minutes=1), metadata=conflict.metadata)
    assert result["status"] == "partial" and result["rejected"] == 1
    assert result["changed_events"] == result["new_observations"] == 0
    assert count(db, "events") == count(db, "observations") == 2
    assert detail(db, record="us-known-a")["lifecycle_status"] == "active"
    assert detail(db, record="us-known-c")["lifecycle_status"] == "active"
