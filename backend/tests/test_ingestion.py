"""Pure ingestion regressions plus optional isolated-PostGIS current-list checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from monitor.contracts import NormalizedEvent
from sqlalchemy import text

from test_storage import URL as _STORAGE_URL, db as _postgis_fixture, ingest as _ingest
from monitor.ingestion import (
    IdentityConflict, Lease, LeaseLost, _keys, _source_rank, as_time,
    check_lease, fail_source, independent_origins,
)
from monitor import ingestion

NOW = datetime(2026, 8, 26, 21, tzinfo=timezone.utc)
postgis_db = _postgis_fixture  # Reuse the existing dedicated-database/rollback fixture.


def event(**changes):
    fields = {
        "source_id": "meteoalarm", "provider_record_id": "warning-new",
        "kind": "advisory", "category": "weather", "title": "Warning",
        "source_url": "https://feeds.meteoalarm.org/",
        "origins": ["imgw"], "external_ids": ["cap:https://www.imgw.pl:warning-new"],
        "raw": {"sender": "https://www.imgw.pl"},
    }
    return NormalizedEvent(**(fields | changes))


@pytest.mark.parametrize(("normals", "expected"), [
    ([{"origins": ["usgs:tx"]}, {"origins": ["usgs"]}], (1, ["usgs"])),
    ([{"origins": ["unknown:gdacs"]}, {"origins": ["usgs:us"]}], (1, ["usgs"])),
    ([{"origins": ["unknown:gdacs"]}, {"origins": ["unknown:other"]}], (1, [])),
    ([{"origins": ["gwis"]}, {"origins": ["firms"]}, {"origins": ["nasa:firms"]}], (1, ["nasa:firms"])),
    ([{"origins": ["imgw"]}, {"origins": ["imgw"]}], (1, ["imgw"])),
    ([{"origins": ["usgs", "emsc"]}], (1, [])),
    ([{"origins": ["usgs", "emsc"]}, {"origins": ["usgs:tx"]}], (1, ["usgs"])),
    ([{"origins": ["usgs:tx"]}, {"origins": ["emsc"]}], (2, ["emsc", "usgs"])),
    ([{"origins": ["USGS:TX", "usgs:us"]}], (1, ["usgs"])),
])
def test_origin_count_is_not_number_of_mirrors_or_listed_upstreams(normals, expected):
    assert independent_origins(normals) == expected


def test_cap_references_use_sender_scoped_hard_ids():
    keys = _keys(event(supersedes=["warning-old"]))
    assert "cap:https://www.imgw.pl:warning-old" in keys
    assert "record:meteoalarm:warning-old" not in keys
    assert "record:meteoalarm:warning-new" in keys


def test_cap_reference_does_not_match_same_identifier_from_different_sender():
    cancellation = _keys(event(supersedes=["warning-old"]))
    other_original = _keys(event(
        provider_record_id="warning-old",
        external_ids=["cap:https://dwd.de:warning-old"], raw={"sender": "https://dwd.de"},
    ))
    assert set(cancellation).isdisjoint(other_original)


def test_cap_reference_matches_original_with_same_sender():
    cancellation = _keys(event(supersedes=["warning-old"]))
    original = _keys(event(
        provider_record_id="warning-old",
        external_ids=["cap:https://www.imgw.pl:warning-old"],
    ))
    assert set(cancellation).intersection(original) == {"cap:https://www.imgw.pl:warning-old"}


def test_reference_requires_known_sender():
    with pytest.raises(IdentityConflict):
        _keys(event(supersedes=["warning-old"], raw={}))


def test_unimplemented_reference_protocol_is_rejected():
    with pytest.raises(IdentityConflict):
        _keys(event(
            source_id="usgs", provider_record_id="us7000x",
            supersedes=["other"], external_ids=["usgs:us7000x"],
        ))


def test_source_rank_keeps_current_gdacs_episode_ahead_of_newer_old_episode():
    current = {"source_id": "gdacs", "source_updated_at": "2026-08-25T12:00:00Z"}
    previous = {"source_id": "gdacs", "source_updated_at": "2026-08-26T12:00:00Z"}
    assert _source_rank(current, {"is_current": True, "episode_id": "5"}) > _source_rank(
        previous, {"is_current": False, "episode_id": "4"}
    )


def test_source_rank_survives_raw_payload_retention():
    normal = {
        "source_id": "gdacs", "source_updated_at": "2026-08-25T12:00:00Z",
        "provider_revision": {"is_current": True, "episode_id": "9"},
    }
    assert _source_rank(normal, None) == _source_rank(
        normal, {"is_current": True, "episode_id": "9"}
    )
    assert _source_rank(normal, None)[0] is True


def test_source_rank_orders_numeric_episodes_and_source_clocks():
    normal = {"source_id": "gdacs", "source_updated_at": "2026-08-26T12:00:00Z"}
    assert _source_rank(normal, {"is_current": True, "episode_id": "10"}) > _source_rank(
        normal, {"is_current": True, "episode_id": "9"}
    )
    assert _source_rank({"source_id": "usgs", "source_updated_at": "2026-08-26T12:00:00Z"}) > _source_rank(
        {"source_id": "usgs", "source_updated_at": "2026-08-25T12:00:00Z"}
    )


def test_naive_or_malformed_time_is_not_given_an_invented_offset():
    assert as_time("2026-08-26T12:00:00") is None
    assert as_time("not a date") is None
    assert as_time("2026-08-26T12:00:00+02:00").utcoffset() == timedelta(hours=2)


class FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row


class LeaseConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((str(query), params))
        return FakeResult(self.row)


def lease_and_connection(*, failures=0):
    owner = uuid4()
    lease = Lease("usgs", owner, {}, False, 300)
    row = {
        "lease_owner": owner, "lease_until": NOW + timedelta(seconds=60),
        "failures": failures,
    }
    return lease, LeaseConnection(row)


def test_fencing_accepts_only_current_unexpired_owner():
    lease, conn = lease_and_connection()
    assert check_lease(conn, lease, NOW) is conn.row
    conn.row["lease_owner"] = uuid4()
    with pytest.raises(LeaseLost):
        check_lease(conn, lease, NOW)


@pytest.mark.parametrize("expiry", [None, NOW, NOW - timedelta(seconds=1)])
def test_fencing_rejects_missing_expired_and_boundary_lease(expiry):
    lease, conn = lease_and_connection()
    conn.row["lease_until"] = expiry
    with pytest.raises(LeaseLost):
        check_lease(conn, lease, NOW)


def test_retry_after_takes_precedence_over_exponential_delay(monkeypatch):
    monkeypatch.setattr(ingestion.random, "randint", lambda _a, _b: 0)
    lease, conn = lease_and_connection(failures=1)
    result = fail_source(conn, lease, "Rate limited", now=NOW, retry_after=7200)
    assert result["retry_seconds"] == 7200
    assert conn.calls[1][1]["next"] == NOW + timedelta(hours=2)
    assert result["status"] == "error"


def test_backoff_is_bounded_and_credentials_remain_distinct(monkeypatch):
    monkeypatch.setattr(ingestion.random, "randint", lambda _a, _b: 0)
    lease, conn = lease_and_connection(failures=100)
    result = fail_source(conn, lease, "Missing credentials", now=NOW, needs_credentials=True)
    assert result["retry_seconds"] <= 21600
    assert result["status"] == "needs_credentials"


def test_cap_clock_activation_does_not_change_immutable_source_normalization():
    from monitor.lifecycle import effective_event_state
    record = event(
        lifecycle_status="unknown", valid_from=NOW+timedelta(hours=1),
        valid_to=NOW+timedelta(hours=4), occurred_start=NOW+timedelta(hours=1),
        tags=["cap", "hazard_onset_in_future"],
    ).model_dump(mode="json", exclude={"raw"})
    initial = dict(record)
    current = effective_event_state(record, NOW+timedelta(hours=2))
    assert current["lifecycle_status"] == "active"
    assert "hazard_onset_in_future" not in current["tags"]
    assert record == initial
    assert current["valid_from"] == record["valid_from"]
    assert effective_event_state(record, NOW+timedelta(hours=4))["lifecycle_status"] == "expired"


@pytest.mark.parametrize("status,end,expected", [
    ("withdrawn", NOW+timedelta(hours=2), "withdrawn"),
    ("expired", NOW+timedelta(hours=2), "expired"),
    ("unknown", None, "unknown"),
])
def test_cap_clock_never_revives_terminal_state_or_guesses_missing_expiry(status, end, expected):
    from monitor.lifecycle import effective_event_state
    record = event(lifecycle_status=status, valid_from=NOW-timedelta(hours=1), valid_to=end)
    assert effective_event_state(record.model_dump(mode="json"), NOW)["lifecycle_status"] == expected


@pytest.mark.parametrize("elapsed,expected", [(0, "unknown"), (1, "active"), (2, "active"), (4, "expired")])
def test_swpc_forecast_uses_only_its_declared_validity_without_mutating_observation(elapsed, expected):
    from monitor.lifecycle import effective_event_state
    record = event(source_id="noaa_swpc", category="space_weather", lifecycle_status="unknown",
                   valid_from=NOW+timedelta(hours=1), valid_to=NOW+timedelta(hours=4),
                   issued_at=NOW, occurred_start=None, tags=["forecast", "advisory"]).model_dump(mode="json")
    current = effective_event_state(record, NOW+timedelta(hours=elapsed))
    assert current["lifecycle_status"] == expected
    assert record["lifecycle_status"] == "unknown" and current["issued_at"] == record["issued_at"]
    assert current["occurred_start"] is None


@pytest.mark.parametrize("status,start,end,expected", [
    ("withdrawn", NOW, NOW+timedelta(hours=1), "withdrawn"),
    ("expired", NOW, NOW+timedelta(hours=1), "expired"),
    ("unknown", NOW, None, "unknown"),
    ("unknown", None, NOW+timedelta(hours=1), "unknown"),
])
def test_swpc_forecast_never_invents_validity_or_revives_terminal_state(status, start, end, expected):
    from monitor.lifecycle import effective_event_state
    record = event(source_id="noaa_swpc", category="space_weather", lifecycle_status=status,
                   valid_from=start, valid_to=end).model_dump(mode="json")
    assert effective_event_state(record, NOW)["lifecycle_status"] == expected


@pytest.mark.parametrize("incoming,previous,expected", [
    ("2", "1", True), ("1", "2", False), ("2", "2", False), ("new", "1", False),
])
def test_normalizer_generation_only_advances_explicit_numeric_versions(incoming, previous, expected):
    assert ingestion._newer_normalizer(incoming, previous) is expected


@pytest.mark.parametrize("references,cycle", [
    ({"a": [], "b": ["a"], "c": ["missing"]}, False),
    ({"a": ["b"], "b": ["a"]}, True),
    ({"a": ["b"], "b": ["a"], "c": ["b"]}, True),
])
def test_cap_cycle_detection_covers_all_components(references, cycle):
    normals = [{"source_id": "meteoalarm", "provider_record_id": key, "supersedes": refs}
               for key, refs in references.items()]
    if cycle:
        with pytest.raises(IdentityConflict):
            ingestion._assert_cap_acyclic(normals)
    else:
        ingestion._assert_cap_acyclic(normals)


def imgw_event(record="123", **changes):
    return event(**({
        "source_id": "imgw_hydro", "provider_record_id": record,
        "source_url": "https://hydro.imgw.pl/#/warnings/hydro",
        "lifecycle_status": "active", "issued_at": NOW-timedelta(hours=1),
        "source_updated_at": NOW-timedelta(hours=1), "valid_from": NOW-timedelta(hours=1),
        "valid_to": None, "external_ids": ["imgw:hydro:" + record],
        "tags": ["current_list_not_archive", "until_revoked"],
        "raw": {"id": record, "statusIsCurrent": True, "isUntilRevoke": True},
    } | changes))


def current_list_metadata(at=NOW, **changes):
    return {"current_list_complete": True, "current_list_fetched_at": at.isoformat(), **changes}


def test_imgw_absence_is_derived_unknown_without_changing_source_evidence():
    normal = imgw_event().model_dump(mode="json", exclude={"raw"})
    before = dict(normal)
    cursor = {"current_list_ingested_at": (NOW+timedelta(minutes=1)).isoformat()}
    missing = ingestion._current_list_state(normal, NOW, cursor)
    assert missing["lifecycle_status"] == "unknown"
    assert ingestion.CURRENT_LIST_MISSING_TAG in missing["tags"]
    assert missing["valid_to"] is None and normal == before
    assert ingestion._current_list_state(normal, NOW+timedelta(minutes=2), cursor) == normal


@pytest.mark.parametrize("changes", [
    {"source_id": "meteoalarm"}, {"kind": "incident"}, {"lifecycle_status": "withdrawn"},
    {"lifecycle_status": "expired"}, {"lifecycle_status": "unknown"},
    {"valid_to": NOW+timedelta(hours=1)}, {"tags": ["current_list_not_archive"]},
])
def test_current_list_does_not_reinterpret_other_sources_terminal_states_or_finite_validity(changes):
    normal = imgw_event(**changes).model_dump(mode="json", exclude={"raw"})
    assert ingestion._current_list_state(normal, NOW, {
        "current_list_ingested_at": (NOW+timedelta(minutes=1)).isoformat(),
    }) == normal


@pytest.mark.parametrize("source,metadata,partial,stale,invalid,expected", [
    ("imgw_hydro", current_list_metadata(), False, False, False, True),
    ("imgw_hydro", current_list_metadata(), True, False, False, False),
    ("imgw_hydro", current_list_metadata(), False, True, False, False),
    ("imgw_hydro", current_list_metadata(), False, False, True, False),
    ("meteoalarm", current_list_metadata(), False, False, False, False),
    ("imgw_hydro", {}, False, False, False, False),
    ("imgw_hydro", current_list_metadata(current_list_complete="true"), False, False, False, False),
    ("imgw_hydro", current_list_metadata(current_list_fetched_at="2026-08-26T21:00:00"), False, False, False, False),
])
def test_current_list_reconciliation_requires_explicit_complete_fresh_success(source, metadata, partial, stale, invalid, expected):
    assert ingestion._can_reconcile_current_list(
        source, metadata, partial=partial, stale=stale, invalid_clock=invalid,
    ) is expected


@pytest.mark.parametrize("fetched", [None, "bad date", NOW.isoformat(), (NOW-timedelta(minutes=1)).isoformat()])
def test_current_list_rejects_missing_invalid_equal_or_regressed_fetch_clock(fetched):
    assert ingestion._current_list_clock_invalid("imgw_hydro", current_list_metadata(
        current_list_fetched_at=fetched,
    ), {"current_list_fetched_at": NOW.isoformat()}, NOW+timedelta(minutes=2))


def test_current_list_clock_has_future_and_local_clock_regression_guards():
    assert ingestion._current_list_clock_invalid("imgw_hydro", current_list_metadata(NOW+timedelta(hours=1)), {}, NOW)
    assert ingestion._current_list_clock_invalid("imgw_hydro", current_list_metadata(NOW), {
        "current_list_ingested_at": NOW.isoformat(),
    }, NOW)
    assert not ingestion._current_list_clock_invalid("imgw_hydro", current_list_metadata(NOW+timedelta(minutes=1)), {
        "current_list_fetched_at": NOW.isoformat(), "current_list_ingested_at": NOW.isoformat(),
    }, NOW+timedelta(minutes=2))


@pytest.mark.skipif(not _STORAGE_URL, reason="Requires an isolated TEST_DATABASE_URL (monitor_test_*).")
def test_imgw_complete_list_absence_and_same_payload_return_have_auditable_revisions(postgis_db):
    db = postgis_db
    original = imgw_event()
    _ingest(db, "imgw_hydro", [original, imgw_event("456", valid_to=NOW+timedelta(hours=2)),
                              imgw_event("789", lifecycle_status="withdrawn")], NOW,
            metadata=current_list_metadata())
    event_id = db.execute(text("SELECT event_id FROM provider_records WHERE source_id='imgw_hydro' AND provider_record_id='123'")).scalar_one()
    missing_at = NOW+timedelta(minutes=1)
    result = _ingest(db, "imgw_hydro", [], missing_at, metadata=current_list_metadata(missing_at))
    assert result["status"] == "ok_empty" and result["changed_events"] == 1
    row = db.execute(text("SELECT * FROM events WHERE id=:id"), {"id": event_id}).mappings().one()
    assert row["lifecycle_status"] == "unknown" and row["valid_to"] is None
    assert row["last_seen_at"] == NOW and row["last_changed_at"] == missing_at
    assert ingestion.CURRENT_LIST_MISSING_TAG in row["normal"]["tags"]
    saved = db.execute(text("SELECT normalized FROM observations WHERE provider_record_id='123'")).scalar_one()
    assert saved["lifecycle_status"] == "active" and ingestion.CURRENT_LIST_MISSING_TAG not in saved["tags"]
    assert db.execute(text("SELECT lifecycle_status FROM events JOIN provider_records p ON p.event_id=events.id WHERE p.provider_record_id='456'")).scalar_one() == "active"
    assert db.execute(text("SELECT lifecycle_status FROM events JOIN provider_records p ON p.event_id=events.id WHERE p.provider_record_id='789'")).scalar_one() == "withdrawn"
    repeat_at = NOW+timedelta(minutes=2)
    assert _ingest(db, "imgw_hydro", [], repeat_at, metadata=current_list_metadata(repeat_at))["changed_events"] == 0
    assert not ingestion.rebuild_event(db, event_id, repeat_at)
    returned_at = NOW+timedelta(minutes=3)
    result = _ingest(db, "imgw_hydro", [original], returned_at, metadata=current_list_metadata(returned_at))
    assert result["new_observations"] == 0 and result["changed_events"] == 1
    row = db.execute(text("SELECT * FROM events WHERE id=:id"), {"id": event_id}).mappings().one()
    assert row["lifecycle_status"] == "active" and ingestion.CURRENT_LIST_MISSING_TAG not in row["normal"]["tags"]
    revisions = list(db.execute(text("SELECT change_type,summary,snapshot FROM event_revisions WHERE event_id=:id ORDER BY recorded_at"), {"id": event_id}).mappings())
    assert [r["snapshot"]["lifecycle_status"] for r in revisions] == ["active", "unknown", "active"]
    assert [r["change_type"] for r in revisions[1:]] == ["updated", "updated"]
    assert "kompletnej bieżącej liście" in revisions[1]["summary"] and "ponownie obecny" in revisions[2]["summary"]
    # A delayed old complete list cannot remove or revive records after a newer one.
    stale = _ingest(db, "imgw_hydro", [], NOW+timedelta(minutes=4), metadata=current_list_metadata(missing_at))
    assert stale["status"] == "partial" and stale["changed_events"] == 0
    assert db.execute(text("SELECT lifecycle_status FROM events WHERE id=:id"), {"id": event_id}).scalar_one() == "active"


@pytest.mark.skipif(not _STORAGE_URL, reason="Requires an isolated TEST_DATABASE_URL (monitor_test_*).")
@pytest.mark.parametrize("problem", ["partial", "truncated", "warning", "rejected", "db_rejected"])
def test_imgw_incomplete_list_never_changes_absent_warning(postgis_db, problem):
    db = postgis_db
    _ingest(db, "imgw_hydro", [imgw_event()], NOW, metadata=current_list_metadata())
    at = NOW+timedelta(minutes=1)
    fields = {"metadata": current_list_metadata(at)}
    incoming = []
    if problem in {"partial", "truncated"}:
        fields["metadata"][problem] = True
    elif problem == "warning":
        fields["warnings"] = ["Incomplete upstream document"]
    elif problem == "rejected":
        fields["rejected_count"] = 1
    else:
        # Missing coordinates is invalid GeoJSON. PostGIS may coerce a string
        # coordinate such as "bad" to zero, so that is not a rejection fixture.
        incoming = [imgw_event("456", geometry={"type": "Point"}, location_precision="point")]
    result = _ingest(db, "imgw_hydro", incoming, at, **fields)
    assert result["status"] == "partial" and result["changed_events"] == 0
    assert db.execute(text("SELECT lifecycle_status FROM events")).scalar_one() == "active"
    cursor = db.execute(text("SELECT cursor FROM sources WHERE id='imgw_hydro'")).scalar_one()
    assert cursor["current_list_fetched_at"] == NOW.isoformat()
    assert cursor["latest_observed_fetched_at"] == at.isoformat()
    if problem == "db_rejected":
        assert result["rejected"] == 1 and result["records"] == 0


def test_partial_read_high_water_rejects_delayed_complete_list_and_local_clock_regression():
    partial_at = NOW+timedelta(minutes=2)
    cursor = {
        "current_list_fetched_at": NOW.isoformat(), "current_list_ingested_at": NOW.isoformat(),
        "latest_observed_fetched_at": partial_at.isoformat(),
        "latest_observed_ingested_at": partial_at.isoformat(),
    }
    assert ingestion._current_list_clock_invalid(
        "imgw_hydro", current_list_metadata(NOW+timedelta(minutes=1)), cursor, NOW+timedelta(minutes=3),
    )
    assert ingestion._current_list_clock_invalid(
        "imgw_hydro", current_list_metadata(NOW+timedelta(minutes=3)), cursor, partial_at,
    )
    assert not ingestion._current_list_clock_invalid(
        "imgw_hydro", current_list_metadata(NOW+timedelta(minutes=3)), cursor, NOW+timedelta(minutes=4),
    )


@pytest.mark.skipif(not _STORAGE_URL, reason="Requires an isolated TEST_DATABASE_URL (monitor_test_*).")
def test_partial_imgw_presence_cannot_be_overruled_by_delayed_older_complete_list(postgis_db):
    db = postgis_db
    original = imgw_event()
    _ingest(db, "imgw_hydro", [original], NOW, metadata=current_list_metadata())
    partial_at = NOW+timedelta(minutes=2)
    partial = _ingest(db, "imgw_hydro", [original], partial_at, metadata=current_list_metadata(
        partial_at, partial=True, current_list_complete=False,
    ))
    assert partial["status"] == "partial" and partial["records"] == 1
    cursor = db.execute(text("SELECT cursor FROM sources WHERE id='imgw_hydro'")).scalar_one()
    assert cursor["current_list_fetched_at"] == NOW.isoformat()
    assert cursor["latest_observed_fetched_at"] == partial_at.isoformat()
    delayed = _ingest(db, "imgw_hydro", [], NOW+timedelta(minutes=3),
                      metadata=current_list_metadata(NOW+timedelta(minutes=1)))
    assert delayed["status"] == "partial" and delayed["changed_events"] == 0
    row = db.execute(text("SELECT lifecycle_status,last_seen_at FROM events")).mappings().one()
    assert row["lifecycle_status"] == "active" and row["last_seen_at"] == partial_at
    assert db.execute(text("SELECT count(*) FROM event_revisions")).scalar_one() == 1
    assert db.execute(text("SELECT cursor FROM sources WHERE id='imgw_hydro'")).scalar_one() == cursor
    # A genuinely newer complete list may still establish absence.
    newer_at = NOW+timedelta(minutes=4)
    assert _ingest(db, "imgw_hydro", [], newer_at, metadata=current_list_metadata(newer_at))["changed_events"] == 1
    assert db.execute(text("SELECT lifecycle_status FROM events")).scalar_one() == "unknown"


def test_imgw_descriptive_area_never_triggers_country_polygon_lookup():
    class NoGeometryLookup:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("IMGW descriptive coverage must not request a country polygon")

    normal = imgw_event(countries=["PL"], location_precision="country",
                        tags=["area_description_no_geometry"]).model_dump(mode="json", exclude={"raw"})
    result = ingestion._geo(NoGeometryLookup(), normal)
    assert result["geometry"] is None and result["countries"] == ["PL"]
    assert "country_geometry_not_extent" not in result["tags"]


@pytest.mark.skipif(not _STORAGE_URL, reason="Requires an isolated TEST_DATABASE_URL (monitor_test_*).")
@pytest.mark.parametrize("source,category,has_geometry", [("imgw_hydro", "weather", False), ("easa_czib", "aviation", True)])
def test_persist_keeps_imgw_geometry_unknown_without_changing_easa_country_coverage(postgis_db, source, category, has_geometry):
    db = postgis_db
    normal = imgw_event(source_id=source, category=category, countries=["PL"],
                        location_precision="country", tags=["area_description_no_geometry"] if source == "imgw_hydro" else [],
                        external_ids=[source+":country-fixture"])
    _ingest(db, source, [normal], NOW, metadata=current_list_metadata() if source == "imgw_hydro" else {})
    row = db.execute(text("SELECT geom IS NOT NULL AS has_geometry,countries,normal FROM events")).mappings().one()
    assert row["has_geometry"] is has_geometry and row["countries"] == ["PL"]
    assert (row["normal"]["geometry"] is not None) is has_geometry
    assert ("country_geometry_not_extent" in row["normal"]["tags"]) is has_geometry
    if source == "easa_czib":
        assert "country_geometry_not_fir" in row["normal"]["tags"]
