from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from monitor import briefing
from monitor.briefing import build_briefing

UTC = timezone.utc
SINCE = datetime(2026, 8, 26, 12, tzinfo=UTC)
UNTIL = datetime(2026, 8, 27, 12, tzinfo=UTC)


def event(**overrides):
    value = {
        "id": "one", "title": "Komunikat źródłowy", "kind": "incident", "category": "earthquake",
        "occurred_start": "2026-08-27T09:00:00Z", "issued_at": "2026-08-27T09:05:00Z",
        "last_changed_at": "2026-08-27T09:10:00Z", "last_seen_at": "2026-08-27T11:59:00Z",
        "source_url": "https://example.org/source-record/one", "independent_source_count": 1,
        "severity": 2, "lifecycle_status": "active", "change_type": "new", "time_precision": "second",
        "tags": [],
    }
    value.update(overrides)
    return value


def health(status="ok"):
    return [{"id": "feed", "name": "Źródło testowe", "enabled": True, "status": status, "coverage": "Ograniczony zakres"}]


def test_first_briefing_is_a_starting_point_and_not_bulk_new_attacks():
    historical = [
        event(
            id=f"cve-{number}", kind="vulnerability_notice", category="cyber",
            occurred_start=None, issued_at="2021-11-03T00:00:00Z", change_type="initial_import",
            tags=["catalog_date_added", "date_only_utc_anchor"], time_precision="day",
        )
        for number in range(42)
    ]
    result = build_briefing(historical, health(), SINCE, UNTIL, first_briefing=True)
    assert "Pierwszy briefing: punkt startowy" in result["answer"]
    assert "Brak poprzedniego briefingu" in result["answer"]
    assert result["facts"] == result["inferences"] == []
    assert any("42 rekordów" in section["title"] for section in result["sections"])
    assert any("nie jako nowe zdarzenia lub ataki" in value for value in result["limitations"])
    assert result["generated_at"] == "2026-08-27T12:00:00Z"


def test_recent_initial_import_has_sources_but_is_not_claimed_new_occurrence():
    result = build_briefing([event(change_type="initial_import")], health(), SINCE, UNTIL, True)
    assert len(result["facts"]) == 1
    assert result["sections"][0]["title"] == "Publikacje z udokumentowanym czasem w oknie"
    assert "Pierwszy odczyt" in result["facts"][0]["text"]
    assert "Pierwszy import" in result["facts"][0]["text"]
    assert "nie dowód nowego zdarzenia" in result["facts"][0]["text"]
    assert result["facts"][0]["source_urls"] == ["https://example.org/source-record/one"]


def test_day_precision_is_not_an_hour_and_partial_day_membership_is_uncertain():
    item = event(
        kind="vulnerability_notice", category="cyber", occurred_start=None,
        issued_at="2026-08-27T00:00:00Z", time_precision="day", change_type="initial_import",
        tags=["catalog_date_added", "date_only_utc_anchor"],
    )
    result = build_briefing([item], health(), SINCE, UNTIL, True)
    assert "godzina nieznana" in result["sections"][0]["title"]
    text = result["facts"][0]["text"]
    assert "Publikacja: 2026-08-27 (dokładność: dzień)" in text
    assert "Publikacja: 2026-08-27T00:00" not in text
    assert "czas pierwszego ataku nie wynika" in text
    assert any("dokładnych granicach godzinowych" in value for value in result["limitations"])


def test_old_event_updated_now_is_an_update_not_a_new_event():
    item = event(
        occurred_start="2020-01-01T10:00:00Z", issued_at="2020-01-01T10:05:00Z",
        change_type="updated",
    )
    result = build_briefing([item], health(), SINCE, UNTIL)
    assert result["sections"][0]["title"] == "Zaktualizowane rekordy"
    assert "Czas zdarzenia: 2020-01-01T10:00:00Z" in result["facts"][0]["text"]
    assert "Zmiana lokalnego rekordu: 2026-08-27T09:10:00Z" in result["facts"][0]["text"]
    assert "11:59" not in result["facts"][0]["text"]


def test_withdrawal_is_separate_from_new_records():
    result = build_briefing(
        [event(kind="advisory", category="aviation", change_type="withdrawn", lifecycle_status="withdrawn")],
        health(), SINCE, UNTIL,
    )
    assert result["sections"][0]["title"] == "Wycofane komunikaty"
    assert "Status: wycofany" in result["facts"][0]["text"]
    assert any("NOTAM" in value for value in result["limitations"])


def test_easa_exclusive_date_boundary_is_displayed_as_last_valid_day():
    item = event(
        kind="advisory", category="aviation", change_type="updated",
        issued_at="2017-03-31T00:00:00Z", valid_from=None,
        valid_to="2027-02-01T00:00:00Z", time_precision="day",
        tags=["date_only_utc_anchor", "valid_to_exclusive_day_boundary"],
    )
    result = build_briefing([item], health(), SINCE, UNTIL)
    text = result["facts"][0]["text"]
    assert "2027-01-31 (ostatni dzień ważności)" in text
    assert "2027-02-01" not in text
    assert "Początek obowiązywania: nieznany" in text


@pytest.mark.parametrize("status", ["pending", "disabled", "error", "stale", "needs_credentials", "partial"])
def test_empty_briefing_with_missing_sources_does_not_declare_nothing_happened(status):
    result = build_briefing([], health(status), SINCE, UNTIL, True)
    assert result["facts"] == []
    assert "To nie oznacza" in result["answer"]
    assert "Brak poprzedniego briefingu" in result["answer"]
    assert any("Źródło testowe" in value for value in result["limitations"])
    assert result["source_health"][0]["status"] == status


def test_missing_source_health_is_disclosed_even_without_events():
    result = build_briefing([], [], SINCE, UNTIL)
    assert any("Nie otrzymano stanu źródeł" in value for value in result["limitations"])
    assert "ogranicza ten obraz" in result["answer"]


def test_no_inferences_or_synthetic_urls_for_uncitable_record():
    result = build_briefing([event(source_url=None, source_ids=["usgs"])], health(), SINCE, UNTIL)
    assert result["facts"] == result["inferences"] == []
    assert any("poprawnego adresu źródła" in value for value in result["limitations"])


def test_missing_changed_time_is_not_replaced_by_last_seen_time():
    result = build_briefing([event(change_type="updated", last_changed_at=None)], health(), SINCE, UNTIL)
    assert "Zmiana lokalnego rekordu" not in result["facts"][0]["text"]
    assert any("nie zastąpiono jej last_seen_at" in value for value in result["limitations"])


def test_timezone_aware_bounds_and_deterministic_generation():
    warsaw = ZoneInfo("Europe/Warsaw")
    since = datetime(2026, 3, 29, 0, tzinfo=warsaw)
    until = datetime(2026, 3, 30, 0, tzinfo=warsaw)
    events = [event(id="b"), event(id="a")]
    statuses = health()
    original = deepcopy((events, statuses))
    first = build_briefing(events, statuses, since, until)
    second = build_briefing(list(reversed(events)), statuses, since, until)
    assert first == second
    assert first["since"] == "2026-03-28T23:00:00Z"
    assert first["until"] == first["generated_at"] == "2026-03-29T22:00:00Z"
    assert (events, statuses) == original


@pytest.mark.parametrize("since,until", [
    (SINCE.replace(tzinfo=None), UNTIL),
    (SINCE, UNTIL.replace(tzinfo=None)),
    (UNTIL, SINCE),
    (SINCE, SINCE),
])
def test_invalid_briefing_bounds_are_rejected(since, until):
    with pytest.raises(ValueError):
        build_briefing([], health(), since, until)


@pytest.mark.parametrize("first", [True, False])
@pytest.mark.parametrize("kind,label", [("incident", "Czas zdarzenia"), ("measurement", "Czas pomiaru")])
def test_recent_occurrence_without_publication_is_current_first_read_not_new_incident(first, kind, label):
    item = event(kind=kind, issued_at=None, change_type="initial_import")
    before = deepcopy(item)
    result = build_briefing([item], health(), SINCE, UNTIL, first)
    assert result["sections"][0]["title"] == "Zdarzenia z udokumentowanym czasem w oknie — pierwszy odczyt"
    assert len(result["facts"]) == 1
    fact = result["facts"][0]
    assert f"{label}: 2026-08-27T09:00:00Z" in fact["text"]
    assert "Pierwszy odczyt" in fact["text"] and "nie dowód nowego zdarzenia" in fact["text"]
    assert "Publikacja:" not in fact["text"]
    assert fact["source_urls"] == [item["source_url"]]
    assert not any("Import historyczny" in section["title"] for section in result["sections"])
    assert item == before


@pytest.mark.parametrize("occurred,included", [
    (SINCE, True), (UNTIL, False), (SINCE - timedelta(microseconds=1), False), (None, False),
])
def test_first_occurrence_window_is_half_open_and_unknown_time_stays_unknown(occurred, included):
    item = event(issued_at=None, occurred_start=occurred, change_type="initial_import")
    result = build_briefing([item], health(), SINCE, UNTIL, True)
    assert bool(result["facts"]) is included


@pytest.mark.parametrize("occurred,included", [
    ("2026-08-27T00:00:00Z", True), ("2026-08-26T00:00:00Z", False), ("2026-08-28T00:00:00Z", False),
])
def test_day_precision_occurrence_uses_overlap_and_discloses_unknown_hour(occurred, included):
    item = event(
        issued_at=None, occurred_start=occurred, time_precision="day",
        change_type="initial_import", tags=["date_only_utc_anchor"],
    )
    since = datetime(2026, 8, 27, 6, tzinfo=UTC)
    result = build_briefing([item], health(), since, UNTIL, True)
    assert bool(result["facts"]) is included
    if included:
        assert "godzina nieznana" in result["sections"][0]["title"]
        assert "Czas zdarzenia: 2026-08-27 (dokładność: dzień)" in result["facts"][0]["text"]
        assert "Czas zdarzenia: 2026-08-27T00:00" not in result["facts"][0]["text"]
        assert any("zdarzenie nastąpiło w jego dokładnych granicach" in value for value in result["limitations"])


@pytest.mark.parametrize("kind,category", [("advisory", "aviation"), ("vulnerability_notice", "cyber")])
def test_advisory_and_catalog_start_cannot_use_incident_clock_to_qualify_initial_import(kind, category):
    item = event(
        kind=kind, category=category, issued_at="2020-01-01T00:00:00Z",
        change_type="initial_import",
    )
    result = build_briefing([item], health(), SINCE, UNTIL, True)
    assert result["facts"] == []
    assert "tło" in result["answer"]


@pytest.mark.parametrize("budget", [20, briefing.MAX_FACTS])
def test_global_fact_budget_keeps_high_severity_occurrence_before_routine_publications(monkeypatch, budget):
    monkeypatch.setattr(briefing, "MAX_FACTS", budget)
    publications = [
        event(
            id=f"published-{number:02}", kind="advisory", category="weather", severity=1,
            lifecycle_status="expired", change_type="initial_import",
            last_changed_at=UNTIL - timedelta(minutes=number + 1),
            source_url=f"https://example.org/cap/{number}",
        )
        for number in range(budget)
    ]
    quake = event(
        id="quake", title="Testowy raport M5.6", severity=3, issued_at=None,
        change_type="initial_import", last_changed_at=SINCE + timedelta(minutes=1),
        source_url="https://example.org/quake/one",
    )
    records = [*publications, quake]
    before = deepcopy(records)
    result = build_briefing(records, health(), SINCE, UNTIL, True)
    assert result == build_briefing(list(reversed(records)), health(), SINCE, UNTIL, True)
    assert records == before
    fact_ids = [fact["event_id"] for fact in result["facts"]]
    assert len(fact_ids) == budget and fact_ids[0] == "quake"
    assert f"published-{budget - 1:02}" not in fact_ids
    occurrence_section = next(section for section in result["sections"]
                              if section["title"].startswith("Zdarzenia z udokumentowanym czasem"))
    assert [item["event_id"] for item in occurrence_section["items"]] == ["quake"]
    assert result["facts"][0]["source_urls"] == [quake["source_url"]]
    assert any("pominięto w części opisowej 1 dalszych rekordów" in value for value in result["limitations"])


def test_historical_and_uncitable_high_severity_records_do_not_consume_fact_budget(monkeypatch):
    monkeypatch.setattr(briefing, "MAX_FACTS", 1)
    records = [
        event(id="historical", severity=4, change_type="initial_import",
              issued_at="2020-01-01T00:00:00Z", occurred_start="2020-01-01T00:00:00Z"),
        event(id="uncitable", severity=4, source_url=None, change_type="updated"),
        event(id="current", severity=3, issued_at=None, change_type="initial_import"),
    ]
    result = build_briefing(records, health(), SINCE, UNTIL, True)
    assert [fact["event_id"] for fact in result["facts"]] == ["current"]
    assert any("1 rekordów bez identyfikatora" in value for value in result["limitations"])
    assert any("Import historyczny" in section["title"] for section in result["sections"])
    assert not any("pominięto w części opisowej" in value for value in result["limitations"])



def test_stream_consumes_every_record_while_retaining_only_global_fact_budget():
    consumed = []

    def records():
        for number in range(1201):
            consumed.append(number)
            yield event(id=f"item-{number:04}", severity=4 if number == 1200 else 1,
                        change_type="updated")

    result = build_briefing(records(), health(), SINCE, UNTIL)
    assert len(consumed) == result["processed_count"] == result["citable_count"] == 1201
    assert len(result["facts"]) == briefing.MAX_FACTS
    assert result["facts"][0]["event_id"] == "item-1200"
    assert result["omitted_fact_count"] == 1201 - briefing.MAX_FACTS
    assert "Wszystkie rekordy zakresu zostały przetworzone" in " ".join(result["limitations"])


def test_stream_counters_account_for_uncitable_and_historical_records():
    records = iter([
        event(id="old", change_type="initial_import", occurred_start=None, issued_at=None),
        event(id="bad-url", source_url="javascript:void(0)", change_type="updated"),
        event(id="current", change_type="updated"),
    ])
    result = build_briefing(records, health(), SINCE, UNTIL)
    assert result["processed_count"] == 3
    assert result["historical_count"] == result["uncitable_count"] == result["citable_count"] == 1
    assert result["omitted_fact_count"] == 0
    assert [fact["event_id"] for fact in result["facts"]] == ["current"]
