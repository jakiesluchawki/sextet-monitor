"""Pure ingestion regression tests; PostGIS transactions are covered separately."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from monitor.contracts import NormalizedEvent
from monitor.ingestion import (
    IdentityConflict, Lease, LeaseLost, _keys, _source_rank, as_time,
    check_lease, fail_source, independent_origins,
)
from monitor import ingestion

NOW = datetime(2026, 8, 26, 21, tzinfo=timezone.utc)


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
