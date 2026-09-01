from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from monitor import api
from monitor.config import Settings

NOW = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
EVENT_ID = "c02cf032-cba1-42fc-bdcc-873e638f5d01"
POST = {"X-Monitor-Request": "1", "Origin": "http://localhost:3180"}
JSON_HEADERS = {**POST, "Content-Type": "application/json"}


def source():
    return {
        "id": "usgs", "name": "USGS", "status": "ok", "enabled": True, "requires_key": False,
        "last_success_at": NOW, "record_count": 1, "poll_interval_seconds": 300,
        "coverage": "Trzęsienia ziemi", "license_name": "Public domain",
        "license_url": "https://www.usgs.gov/copyright", "attribution": "USGS",
    }


def event(**overrides):
    value = {
        "id": EVENT_ID, "kind": "incident", "category": "earthquake", "title": "Komunikat źródłowy",
        "source_url": "https://earthquake.usgs.gov/earthquakes/eventpage/example",
        "occurred_start": NOW - timedelta(hours=2), "issued_at": NOW - timedelta(hours=1),
        "last_changed_at": NOW - timedelta(minutes=30), "countries": ["PL"],
        "severity": 2, "severity_label": "umiarkowana", "verification_status": "reported",
        "lifecycle_status": "active", "source_ids": ["usgs"], "source_count": 1,
        "independent_source_count": 1, "change_type": "new", "time_precision": "second",
    }
    value.update(overrides)
    return value


@pytest.fixture
def harness(monkeypatch):
    state = {
        "items": [], "health": [source()], "total": None, "background": 0,
        "selected": [], "brief_selected": [], "lookups": [], "saved": [],
        "previous_by_scope": {}, "last_saved": None, "detail": None, "detail_calls": [],
        "driver_sql": [], "commits": 0, "rollbacks": 0, "engine_calls": 0, "db_down": False,
        "operations": [], "on_identity_lock": None,
        "connections_open": 0, "relations": {}, "relation_calls": [],
        "brief_streams": [], "brief_batch_sizes": [], "stream_closed": 0, "stream_fail_after": None,
    }

    class Scalar:
        @staticmethod
        def scalar_one():
            return 1

    class Connection:
        def exec_driver_sql(self, sql):
            state["driver_sql"].append(sql)
            state["operations"].append("sql:" + sql)
            if state["db_down"]:
                raise OperationalError("SELECT 1", {}, Exception("test-secret-password"))
            return Scalar()

        def execute(self, statement):
            sql = str(statement)
            assert sql in {"SELECT 1", "SELECT pg_advisory_xact_lock(61704001)"}
            state["operations"].append("sql:" + sql)
            if state["db_down"]:
                raise OperationalError("SELECT 1", {}, Exception("test-secret-password"))
            if sql == "SELECT pg_advisory_xact_lock(61704001)" and state["on_identity_lock"]:
                state["on_identity_lock"]()
            return Scalar()

    class Engine:
        @contextmanager
        def connect(self):
            state["connections_open"] += 1
            try:
                yield Connection()
            finally:
                state["connections_open"] -= 1

        @contextmanager
        def begin(self):
            state["operations"].append("begin")
            state["connections_open"] += 1
            before = deepcopy((state["saved"], state["previous_by_scope"], state["last_saved"]))
            try:
                yield Connection()
            except BaseException:
                state["saved"], state["previous_by_scope"], state["last_saved"] = before
                state["rollbacks"] += 1
                state["operations"].append("rollback")
                raise
            else:
                state["commits"] += 1
                state["operations"].append("commit")
            finally:
                state["connections_open"] -= 1

    engine = Engine()

    def engine_for(_url):
        state["engine_calls"] += 1
        return engine

    def snapshot(query):
        items = deepcopy(state["items"])
        total = len(items) if state["total"] is None else state["total"]
        mapped = sum(item.get("geometry") is not None for item in items)
        return {
            "items": items, "total": total, "shown": len(items), "mapped": mapped,
            "unlocated": len(items) - mapped, "truncated": total > len(items),
            "query": query.model_dump(mode="json"), "source_health": deepcopy(state["health"]),
            "generated_at": NOW, "limitations": ["Ograniczenie przekazane przez warstwę danych."],
        }

    def select(_conn, query, now=None):
        state["selected"].append(query)
        return snapshot(query)

    def select_briefing(_conn, query, *, first_briefing=False, now=None, stream=False):
        state["operations"].append("select_briefing")
        state["brief_selected"].append((query, first_briefing))
        state["brief_streams"].append(stream)
        result = snapshot(query)
        result["initial_import_background_count"] = state["background"]
        if stream:
            items = result["items"]

            def batches():
                consumed = 0
                try:
                    for offset in range(0, len(items), 250):
                        batch = items[offset:offset + 250]
                        state["brief_batch_sizes"].append(len(batch))
                        for item in batch:
                            if state["stream_fail_after"] == consumed:
                                raise OperationalError("SELECT events", {}, Exception("test-secret-password"))
                            consumed += 1
                            yield item
                finally:
                    state["stream_closed"] += 1

            result["items"] = batches()
        return result

    def relations(_conn, event_ids):
        assert state["connections_open"] > 0, "Relations must be read before the snapshot closes"
        state["operations"].append("select_relations")
        state["relation_calls"].append(event_ids)
        return {event_id: [deepcopy(rel) for rel in state["relations"].get(event_id, [])
                           if rel["event_id"] in event_ids] for event_id in event_ids}

    def latest(_conn, *, country=None, window_hours=None):
        state["operations"].append("latest_briefing")
        state["lookups"].append((country, window_hours))
        value = state["last_saved"] if window_hours is None else state["previous_by_scope"].get((country, window_hours))
        # PostgreSQL JSONB returns JSON primitives, unlike an in-memory datetime/UUID snapshot.
        return json.loads(api.db.json_value(value)) if value is not None else None

    def save(_conn, briefing, *, country, window_hours):
        state["operations"].append("save_briefing")
        result = {**deepcopy(briefing), "id": str(UUID(int=len(state["saved"]) + 1))}
        state["saved"].append(result)
        state["last_saved"] = result
        state["previous_by_scope"][(country, window_hours)] = result
        return result

    def detail(_conn, event_id, now=None):
        state["detail_calls"].append(event_id)
        return deepcopy(state["detail"])

    monkeypatch.setattr(api, "utcnow", lambda: NOW)
    monkeypatch.setattr(api, "get_engine", engine_for)
    monkeypatch.setattr(api.db, "get_source_health", lambda _conn, now=None: deepcopy(state["health"]))
    monkeypatch.setattr(api.db, "select_events", select)
    monkeypatch.setattr(api.db, "event_detail", detail)
    monkeypatch.setattr(api.db, "latest_briefing", latest, raising=False)
    monkeypatch.setattr(api.db, "save_briefing", save, raising=False)
    monkeypatch.setattr(api.db, "select_briefing_events", select_briefing, raising=False)
    monkeypatch.setattr(api.db, "relations_for_ids", relations)
    settings = Settings(database_url="postgresql+psycopg://monitor_reader:test-only@127.0.0.1/monitor")
    application = api.create_app(settings, testing=True)
    with TestClient(application) as client:
        yield client, state, settings


def test_health_and_sources_are_read_only_and_expose_no_credentials(harness):
    client, state, _settings = harness
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    assert health.json()["ai_mode"] == "off"
    assert "database_url" not in health.text
    sources = client.get("/api/sources")
    assert sources.status_code == 200
    assert sources.json()["items"][0]["id"] == "usgs"
    assert state["driver_sql"] == [api.READ_TRANSACTION, api.READ_TRANSACTION]
    assert health.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in health.headers


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "evil@localhost", "[::1]:8000"])
def test_untrusted_hosts_fail_before_database_access(harness, host):
    client, state, _settings = harness
    assert client.get("/api/health", headers={"Host": host}).status_code == 403
    assert state["engine_calls"] == 0


@pytest.mark.parametrize("host", ["localhost", "localhost:3180", "127.0.0.1:8000", "api:8000"])
def test_explicit_local_hosts_are_allowed(harness, host):
    client, _state, _settings = harness
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_testserver_is_rejected_in_production_factory(harness):
    _client, _state, settings = harness
    with TestClient(api.create_app(settings)) as production:
        assert production.get("/api/health").status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:9999", "http://localhost.evil:3180"])
def test_foreign_origin_is_forbidden_for_reads_and_writes(harness, origin):
    client, state, _settings = harness
    assert client.get("/api/sources", headers={"Origin": origin}).status_code == 403
    assert client.post("/api/query", json={"question": "PL 12h"}, headers={**POST, "Origin": origin}).status_code == 403
    assert state["engine_calls"] == 0


def test_duplicate_origin_and_post_marker_are_rejected(harness):
    client, _state, _settings = harness
    assert client.get("/api/health", headers=[
        ("Origin", "http://localhost:3180"), ("Origin", "http://localhost:3180"),
    ]).status_code == 403
    assert client.post("/api/query", json={"question": "PL 12h"}, headers=[
        ("X-Monitor-Request", "1"), ("X-Monitor-Request", "1"),
    ]).status_code == 403


@pytest.mark.parametrize("headers", [{}, {"X-Monitor-Request": "0"}, {"X-Monitor-Request": "true"}])
def test_post_requires_non_simple_marker(harness, headers):
    client, state, _settings = harness
    assert client.post("/api/query", json={"question": "PL 12h"}, headers=headers).status_code == 403
    assert state["selected"] == []


def test_local_non_browser_post_can_omit_origin_but_cross_site_cannot(harness):
    client, _state, _settings = harness
    assert client.post("/api/query", json={"question": "PL 12h"}, headers={"X-Monitor-Request": "1"}).status_code == 200
    assert client.post("/api/query", json={"question": "PL 12h"}, headers={
        "X-Monitor-Request": "1", "Sec-Fetch-Site": "cross-site",
    }).status_code == 403


def test_post_requires_json_and_caps_declared_and_actual_body_size(harness):
    client, state, _settings = harness
    assert client.post("/api/query", content="question=PL", headers={
        **POST, "Content-Type": "text/plain",
    }).status_code == 415
    large = b"x" * (api.MAX_BODY_BYTES + 1)
    assert client.post("/api/query", content=large, headers=JSON_HEADERS).status_code == 413
    assert client.post("/api/query", content=large, headers={**JSON_HEADERS, "Content-Length": "1"}).status_code == 413
    assert client.post("/api/query", content=iter([b" " * 7000] * 3), headers=JSON_HEADERS).status_code == 413
    assert state["selected"] == []


def test_body_at_exact_limit_and_json_charset_are_accepted(harness):
    client, _state, _settings = harness
    body = b'{"question":"PL 12h"}'
    body += b" " * (api.MAX_BODY_BYTES - len(body))
    response = client.post("/api/query", content=body, headers={
        **JSON_HEADERS, "Content-Type": "application/json; charset=utf-8",
    })
    assert response.status_code == 200


@pytest.mark.parametrize("body", [
    {"question": " "}, {"question": 123}, {"question": "a" * 2001},
    {"question": "PL 12h", "sql": "SELECT * FROM secrets"}, [],
])
def test_invalid_query_json_never_selects_events(harness, body):
    client, state, _settings = harness
    assert client.post("/api/query", json=body, headers=POST).status_code == 422
    assert state["selected"] == []


def test_invalid_json_is_not_interpreted_as_question(harness):
    client, state, _settings = harness
    assert client.post("/api/query", content=b"{bad", headers=JSON_HEADERS).status_code == 422
    assert state["selected"] == []


def test_get_events_validates_full_query_and_removes_raw_from_summaries(harness):
    client, state, _settings = harness
    state["items"] = [event(raw={"unrequested": "upstream payload"})]
    response = client.get("/api/events", params={
        "country": "PL", "time_basis": "changed", "since": "2026-08-27T06:00:00Z",
        "until": "2026-08-27T12:00:00Z", "min_sources": 2, "severity_min": 1,
        "lat": 52.2297, "lon": 21.0122, "radius_km": 800, "include_inactive": "true", "limit": 1000,
    })
    assert response.status_code == 200, response.text
    query = state["selected"][0]
    assert query.time_basis == "changed" and query.include_inactive
    assert query.min_sources == 2
    assert query.limit == 1000
    assert query.since == datetime(2026, 8, 27, 6, tzinfo=timezone.utc)
    assert response.json()["shown"] == response.json()["unlocated"] == 1
    assert "raw" not in response.json()["items"][0]


@pytest.mark.parametrize("basis,inactive,expected", [
    ("occurred", None, False), ("changed", None, True),
    ("changed", "false", False), ("occurred", "true", True),
])
def test_change_view_defaults_to_including_withdrawals_but_honors_explicit_filter(harness, basis, inactive, expected):
    client, state, _settings = harness
    params = {"time_basis": basis}
    if inactive is not None:
        params["include_inactive"] = inactive
    response = client.get("/api/events", params=params)
    assert response.status_code == 200, response.text
    assert state["selected"][-1].include_inactive is expected


@pytest.mark.parametrize("params", [
    {"window_hours": 0}, {"window_hours": 721}, {"window_hours": "1.5"},
    {"limit": 0}, {"limit": 1001}, {"severity_min": 5}, {"severity_min": -1},
    {"min_sources": 0}, {"min_sources": 11}, {"lat": 91}, {"lon": 181},
    {"lat": "nan", "lon": 21, "radius_km": 800}, {"radius_km": 0}, {"radius_km": 20001},
    {"country": "pl"}, {"country": "POL"}, {"region": "eu"}, {"category": "military"},
    {"time_basis": "issued"}, {"since": "2026-08-27T06:00:00"},
    {"since": "2026-08-27T12:00:00Z", "until": "2026-08-27T12:00:00Z"},
    {"since": "2026-08-28T12:00:00Z", "until": "2026-08-27T12:00:00Z"},
    {"since": "2020-01-01T00:00:00Z", "until": "2026-08-27T12:00:00Z"},
    {"sort_sql": "id; DROP TABLE events"},
])
def test_invalid_event_query_bounds_and_unknown_filters_fail_closed(harness, params):
    client, state, _settings = harness
    assert client.get("/api/events", params=params).status_code == 422
    assert state["selected"] == []


def test_duplicate_query_filter_cannot_silently_choose_a_country(harness):
    client, state, _settings = harness
    assert client.get("/api/events?country=PL&country=DE").status_code == 422
    assert state["selected"] == []


def test_event_detail_requires_uuid_and_returns_404_for_missing_record(harness):
    client, state, _settings = harness
    assert client.get("/api/events/not-a-uuid").status_code == 422
    assert state["detail_calls"] == []
    assert client.get(f"/api/events/{EVENT_ID}").status_code == 404


@pytest.mark.parametrize("raw", [{"public": "record"}, None])
def test_event_detail_serializes_database_uuid_datetime_and_nullable_json(harness, raw):
    client, state, _settings = harness
    evidence_id, revision_id, relation_id = UUID(int=101), UUID(int=102), UUID(int=103)
    state["detail"] = event(
        id=UUID(EVENT_ID),
        evidence=[{
            "id": evidence_id, "source_id": "usgs", "source_name": "USGS", "provider_record_id": "example",
            "source_url": event()["source_url"], "retrieved_at": NOW, "origins": ["usgs"],
            "payload_hash": "a" * 64, "raw": raw, "raw_retained": raw is not None,
            "attribution": "USGS", "license_url": "https://www.usgs.gov/copyright",
        }],
        revisions=[{"id": revision_id, "recorded_at": NOW, "change_type": "new", "summary": "Pierwszy zapis"}],
        relations=[{
            "event_id": relation_id, "title": "Powiązany komunikat", "relation_type": "spatiotemporal",
            "reason": "Bliskość czasu i miejsca nie oznacza wspólnej przyczyny.",
            "distance_km": 12.5, "time_delta_hours": None,
        }],
    )
    response = client.get(f"/api/events/{EVENT_ID}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == EVENT_ID
    assert body["evidence"][0]["id"] == str(evidence_id)
    assert body["evidence"][0]["raw"] == raw
    assert body["evidence"][0]["raw_retained"] is (raw is not None)
    assert body["revisions"][0]["id"] == str(revision_id)
    assert datetime.fromisoformat(body["revisions"][0]["recorded_at"]) == NOW
    assert body["relations"][0]["event_id"] == str(relation_id)
    assert body["relations"][0]["time_delta_hours"] is None


def test_question_response_is_flat_and_cites_database_records(harness):
    client, state, _settings = harness
    state["items"] = [event()]
    response = client.post("/api/query", json={"question": "Co w Polsce przez ostatnie 12 godzin?"}, headers=POST)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interpretation"]["country"] == "PL"
    assert "query" not in body["interpretation"]
    assert body["facts"][0]["source_urls"] == [event()["source_url"]]
    assert body["facts"][0]["event_id"] == EVENT_ID
    assert body["total"] == body["shown"] == 1
    assert "Ograniczenie przekazane przez warstwę danych." in body["limitations"]


def test_unsupported_question_never_performs_broad_event_query(harness):
    client, state, _settings = harness
    state["items"] = [event()]
    response = client.post("/api/query", json={"question": "Pokaż GNSS w Polsce 12h"}, headers=POST)
    assert response.status_code == 200
    assert response.json()["supported"] is False
    assert response.json()["facts"] == response.json()["events"] == []
    assert response.json()["interpretation"] is None
    assert response.json()["total"] is None
    assert state["selected"] == []


def test_query_discloses_database_truncation(harness):
    client, state, _settings = harness
    state["items"], state["total"] = [event()], 2
    response = client.post("/api/query", json={"question": "PL 12h"}, headers=POST)
    assert response.status_code == 200
    assert response.json()["truncated"]
    assert any("1 z 2" in value for value in response.json()["limitations"])


def test_first_briefing_records_exact_scope_and_separates_import_background(harness):
    client, state, _settings = harness
    state["background"] = 1682
    response = client.post("/api/briefings", json={"country": "PL", "window_hours": 24}, headers=POST)
    assert response.status_code == 200, response.text
    body = response.json()
    query, first = state["brief_selected"][0]
    assert state["lookups"] == [("PL", 24)]
    assert first and body["first_briefing"]
    assert query.time_basis == "changed" and query.include_inactive and query.limit == 1000
    assert query.since == NOW - timedelta(hours=24) and query.until == NOW
    assert body["scope"] == {"country": "PL", "window_hours": 24}
    assert body["initial_import_background_count"] == 1682
    assert body["facts"] == []
    assert "punkt startowy" in body["answer"]
    assert any("nie jest liczbą nowych" in value for value in body["limitations"])
    assert len(state["saved"]) == state["commits"] == 1


def test_briefing_waits_for_writer_commit_before_clock_and_fresh_reads(harness, monkeypatch):
    client, state, _settings = harness
    state["previous_by_scope"][("PL", 24)] = {"until": (NOW - timedelta(hours=1)).isoformat()}
    after_wait = NOW + timedelta(seconds=5)

    def writer_finishes_while_waiting():
        # Its event timestamp predates the HTTP request, but it only becomes
        # visible when the worker commits and releases the common identity lock.
        state["operations"].append("writer_commit")
        state["items"] = [event(last_changed_at=NOW - timedelta(seconds=1))]

    def clock_after_wait():
        state["operations"].append("clock")
        return after_wait

    state["on_identity_lock"] = writer_finishes_while_waiting
    monkeypatch.setattr(api, "utcnow", clock_after_wait)
    response = client.post("/api/briefings", json={"country": "PL"}, headers=POST)
    assert response.status_code == 200, response.text
    assert state["operations"] == [
        "begin", "sql:SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "sql:SELECT pg_advisory_xact_lock(61704001)", "writer_commit", "clock",
        "latest_briefing", "select_briefing", "select_relations", "save_briefing", "commit",
    ]
    query, first = state["brief_selected"][0]
    assert not first and query.until == after_wait
    assert query.since == NOW - timedelta(hours=1)
    assert response.json()["facts"][0]["event_id"] == EVENT_ID
    assert datetime.fromisoformat(response.json()["until"]) == after_wait


def test_briefing_cursor_is_only_reused_for_matching_country_and_window(harness, monkeypatch):
    client, state, _settings = harness
    state["items"] = [event()]
    first = client.post("/api/briefings", json={"country": "PL", "window_hours": 24}, headers=POST)
    assert first.status_code == 200
    later = NOW + timedelta(hours=2)
    monkeypatch.setattr(api, "utcnow", lambda: later)
    second = client.post("/api/briefings", json={"country": "PL", "window_hours": 24}, headers=POST)
    assert second.status_code == 200, second.text
    assert not second.json()["first_briefing"]
    assert state["brief_selected"][1][0].since == NOW
    for country, window in [("DE", 24), ("PL", 12), (None, 24)]:
        response = client.post("/api/briefings", json={"country": country, "window_hours": window}, headers=POST)
        assert response.status_code == 200, response.text
        query, is_first = state["brief_selected"][-1]
        assert is_first
        assert query.since == later - timedelta(hours=window)


@pytest.mark.parametrize("first", [True, False])
def test_truncated_briefing_never_inserts_or_advances_cursor(harness, first):
    client, state, _settings = harness
    if not first:
        state["previous_by_scope"][("PL", 24)] = {"until": (NOW - timedelta(hours=2)).isoformat()}
    before = deepcopy(state["previous_by_scope"])
    state["items"], state["total"] = [event()], 1001
    response = client.post("/api/briefings", json={"country": "PL"}, headers=POST)
    assert response.status_code == 409, response.text
    assert "nie przesunięto kursora" in response.json()["detail"]
    assert state["saved"] == []
    assert state["previous_by_scope"] == before
    assert state["commits"] == 0 and state["rollbacks"] == 1


@pytest.mark.parametrize("invalid", ["not-a-date", "2026-08-27T10:00:00", "2026-08-28T12:00:00Z"])
def test_invalid_stored_cursor_is_not_silently_replaced_by_bootstrap(harness, invalid):
    client, state, _settings = harness
    state["previous_by_scope"][(None, 24)] = {"until": invalid}
    response = client.post("/api/briefings", json={}, headers=POST)
    assert response.status_code == 409
    assert state["saved"] == state["brief_selected"] == []


@pytest.mark.parametrize("payload", [
    {"window_hours": 0}, {"window_hours": 721}, {"window_hours": True},
    {"window_hours": "24"}, {"window_hours": 12.5}, {"country": "POL"}, {"first_briefing": False},
])
def test_briefing_input_rejects_invalid_bounds_or_cursor_override(harness, payload):
    client, state, _settings = harness
    assert client.post("/api/briefings", json=payload, headers=POST).status_code == 422
    assert state["lookups"] == state["saved"] == []


def test_latest_briefing_is_null_until_insert_then_returns_saved_body(harness):
    client, _state, _settings = harness
    assert client.get("/api/briefings/latest").json() is None
    saved = client.post("/api/briefings", json={}, headers=POST)
    assert saved.status_code == 200
    latest = client.get("/api/briefings/latest")
    assert latest.status_code == 200
    assert latest.json() == saved.json()


def test_database_failures_are_real_errors_without_connection_details(harness):
    client, state, _settings = harness
    state["db_down"] = True
    response = client.get("/api/sources")
    assert response.status_code == 503
    assert "test-secret-password" not in response.text
    health_response = client.get("/api/health")
    assert health_response.status_code == 503
    assert health_response.json()["database"] == "unavailable"
    assert "test-secret-password" not in health_response.text


def test_api_refuses_privileged_database_url_before_connecting(harness):
    _client, state, _settings = harness
    unsafe = Settings(database_url="postgresql+psycopg://monitor_owner:test-secret@127.0.0.1/monitor")
    with TestClient(api.create_app(unsafe, testing=True)) as client:
        response = client.get("/api/sources")
        assert response.status_code == 503
        assert "monitor_reader" in response.json()["detail"]
        assert "test-secret" not in response.text
    assert state["engine_calls"] == 0


def test_docs_and_openapi_are_not_exposed(harness):
    client, _state, _settings = harness
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


@pytest.mark.parametrize("first", [True, False])
def test_large_briefing_processes_all_batches_then_advances_cursor(harness, monkeypatch, first):
    client, state, _settings = harness
    if not first:
        state["previous_by_scope"][(None, 24)] = {"until": (NOW - timedelta(hours=2)).isoformat()}
    state["items"] = [event(id=str(UUID(int=number + 1)), severity=1) for number in range(1200)]
    strongest = str(UUID(int=2000))
    state["items"].append(event(id=strongest, severity=4))
    response = client.post("/api/briefings", json={}, headers=POST)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processed_count"] == body["total"] == body["citable_count"] == 1201
    assert body["shown"] == len(body["facts"]) == 30
    assert body["omitted_fact_count"] == 1171 and body["truncated"]
    assert body["facts"][0]["event_id"] == strongest
    assert state["brief_streams"] == [True]
    assert state["brief_batch_sizes"] == [250, 250, 250, 250, 201]
    assert state["stream_closed"] == 1 and state["commits"] == 1
    assert len(state["relation_calls"][0]) == 30
    assert datetime.fromisoformat(state["previous_by_scope"][(None, 24)]["until"]) == NOW
    state["items"] = []
    monkeypatch.setattr(api, "utcnow", lambda: NOW + timedelta(seconds=5))
    repeated = client.post("/api/briefings", json={}, headers=POST)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["processed_count"] == 0
    assert state["brief_selected"][-1][0].since == NOW


def test_failure_inside_later_batch_closes_stream_and_preserves_cursor(harness):
    client, state, _settings = harness
    state["previous_by_scope"][(None, 24)] = {"until": (NOW - timedelta(hours=2)).isoformat()}
    previous = deepcopy(state["previous_by_scope"])
    state["items"] = [event(id=str(UUID(int=number + 1))) for number in range(300)]
    state["stream_fail_after"] = 251
    response = client.post("/api/briefings", json={}, headers=POST)
    assert response.status_code == 503
    assert "test-secret-password" not in response.text
    assert state["saved"] == [] and state["previous_by_scope"] == previous
    assert state["stream_closed"] == 1 and state["rollbacks"] == 1 and state["commits"] == 0


@pytest.mark.parametrize("path,payload", [
    ("/api/query", {"question": "Pokaż zdarzenia w Polsce 12h"}),
    ("/api/briefings", {}),
])
def test_real_formatter_receives_relations_before_snapshot_closes(harness, path, payload):
    client, state, _settings = harness
    second = str(UUID(int=2))
    state["items"] = [event(), event(id=second)]
    reason = "Dwa raporty blisko w czasie i przestrzeni; identyczność niepotwierdzona."
    state["relations"] = {
        EVENT_ID: [{"event_id": second, "relation_type": "possible_same_event", "reason": reason}],
        second: [{"event_id": EVENT_ID, "relation_type": "possible_same_event", "reason": reason}],
    }
    assert all("relations" not in item for item in state["items"])
    response = client.post(path, json=payload, headers=POST)
    assert response.status_code == 200, response.text
    assert len(response.json()["facts"]) == 2
    assert len(response.json()["inferences"]) == 1
    assert "nie dowód przyczyny" in response.json()["inferences"][0]
    assert reason in response.json()["inferences"][0]
    assert state["relation_calls"] == [sorted([EVENT_ID, second])]
    assert state["connections_open"] == 0


@pytest.mark.parametrize("basis", ["published", "validity"])
def test_new_history_time_bases_default_to_including_completed_records(harness, basis):
    client, state, _settings = harness
    response = client.get("/api/events", params={"time_basis": basis})
    assert response.status_code == 200, response.text
    assert state["selected"][-1].time_basis == basis
    assert state["selected"][-1].include_inactive is True
    explicit = client.get("/api/events", params={"time_basis": basis, "include_inactive": "false"})
    assert explicit.status_code == 200
    assert state["selected"][-1].include_inactive is False
