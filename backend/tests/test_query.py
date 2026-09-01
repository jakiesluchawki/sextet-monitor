from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from monitor.contracts import QueryInterpretation
from monitor.query import MAX_FACTS, build_answer, parse_question

UTC = timezone.utc
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def event(**overrides):
    value = {
        "id": "eq-1",
        "kind": "incident",
        "category": "earthquake",
        "title": "Źródłowy komunikat o wstrząsie",
        "occurred_start": "2026-08-27T10:00:00Z",
        "issued_at": "2026-08-27T10:08:00Z",
        "source_updated_at": "2026-08-27T10:12:00Z",
        "first_seen_at": "2026-08-27T10:20:00Z",
        "last_seen_at": "2026-08-27T11:55:00Z",
        "last_changed_at": "2026-08-27T10:20:00Z",
        "countries": ["PL"],
        "source_url": "https://earthquake.usgs.gov/earthquakes/eventpage/example",
        "source_ids": ["usgs"],
        "source_count": 1,
        "independent_source_count": 1,
        "time_precision": "second",
        "severity": 2,
        "lifecycle_status": "active",
        "change_type": "new",
        "tags": [],
    }
    value.update(overrides)
    return value


def healthy(**overrides):
    value = {
        "id": "usgs", "name": "USGS", "status": "ok", "enabled": True,
        "last_success_at": "2026-08-27T11:55:00Z", "coverage": "Trzęsienia ziemi, zasięg światowy",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("question,country", [
    ("Co wydarzyło się w Polsce przez ostatnie 12 godzin?", "PL"),
    ("PL 12h", "PL"),
    ("Pokaż zdarzenia w Niemczech z ostatnich 12 godzin", "DE"),
    ("CZ 12h", "CZ"),
    ("Pokaż zdarzenia na Słowacji 12h", "SK"),
    ("Co w Ukrainie 12h?", "UA"),
    ("BY 12h", "BY"),
    ("Co na Litwie 12h?", "LT"),
    ("RU 12h", "RU"),
])
def test_supported_country_and_elapsed_window(question, country):
    parsed = parse_question(question, NOW)
    assert parsed.supported, parsed.explanation
    assert parsed.query.country == country
    assert parsed.query.time_basis == "occurred"
    assert parsed.query.since == NOW - timedelta(hours=12)
    assert parsed.query.until == NOW


def test_europe_aviation_is_advisories_not_tracking():
    parsed = parse_question("Co istotnego dla lotnictwa w Europie dzisiaj?", NOW)
    assert parsed.supported, parsed.explanation
    assert parsed.query.region == "europe"
    assert parsed.query.category == "aviation"
    assert parsed.query.time_basis == "validity" and parsed.query.include_inactive
    assert any("NOTAM" in value for value in parsed.limitations)
    assert parsed.query.severity_min == 0


@pytest.mark.parametrize("source_phrase", [
    "minimum 2 niezależne źródła",
    "co najmniej dwóch niezależnych źródeł",
    "przynajmniej dwoma niezależnymi źródłami",
])
def test_independent_origins_are_explicit(source_phrase):
    parsed = parse_question(f"Pokaż zdarzenia w Polsce 12h z {source_phrase}", NOW)
    assert parsed.supported, parsed.explanation
    assert parsed.query.min_sources == 2
    assert any("Mirrory" in value for value in parsed.limitations)


def test_warsaw_radius_is_explicit_and_has_unknown_geometry_caveat():
    parsed = parse_question("Co wydarzyło się w promieniu 800 km od Warszawy 12h?", NOW)
    assert parsed.supported, parsed.explanation
    assert (parsed.query.lat, parsed.query.lon, parsed.query.radius_km) == (52.2297, 21.0122, 800)
    assert parsed.query.country is None
    assert any("bez geometrii" in value for value in parsed.limitations)


def test_morning_uses_warsaw_not_host_timezone_and_includes_withdrawals():
    parsed = parse_question("Co się zmieniło od rana?", NOW)
    assert parsed.supported, parsed.explanation
    assert parsed.query.time_basis == "changed"
    assert parsed.query.include_inactive is True
    assert parsed.query.since == datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
    assert "06:00" in parsed.explanation
    assert "Europe/Warsaw" in parsed.explanation
    assert "Nie podano filtra geograficznego" in parsed.explanation


def test_default_window_is_disclosed_and_custom_timezone_is_respected():
    default = parse_question("Pokaż zdarzenia w Polsce", NOW)
    assert default.supported
    assert default.query.window_hours == 24
    assert "Nie podano okresu" in default.explanation
    utc = parse_question("Co się zmieniło od 06:00?", NOW, timezone_name="UTC")
    assert utc.supported
    assert utc.query.since == datetime(2026, 8, 27, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize("instant,start,end,hours", [
    ("2026-03-29T22:30:00+00:00", "2026-03-28T23:00:00+00:00", "2026-03-29T22:00:00+00:00", 23),
    ("2026-10-25T23:30:00+00:00", "2026-10-24T22:00:00+00:00", "2026-10-25T23:00:00+00:00", 25),
])
def test_yesterday_uses_calendar_boundaries_across_dst(instant, start, end, hours):
    parsed = parse_question("Co w Polsce wczoraj?", datetime.fromisoformat(instant))
    assert parsed.supported, parsed.explanation
    assert parsed.query.since == datetime.fromisoformat(start)
    assert parsed.query.until == datetime.fromisoformat(end)
    assert (parsed.query.until - parsed.query.since).total_seconds() == hours * 3600


def test_rolling_hours_are_elapsed_utc_hours_across_dst():
    instant = datetime(2026, 10, 25, 4, 0, tzinfo=UTC)
    parsed = parse_question("PL 12h", instant)
    assert parsed.supported
    assert parsed.query.since == instant - timedelta(hours=12)


@pytest.mark.parametrize("instant,reason", [
    (datetime(2026, 3, 29, 12, tzinfo=UTC), "nie istnieje"),
    (datetime(2026, 10, 25, 12, tzinfo=UTC), "dwukrotnie"),
])
def test_nonexistent_and_ambiguous_local_hours_require_clarification(instant, reason):
    parsed = parse_question("Co się zmieniło w Polsce od 02:30?", instant)
    assert not parsed.supported
    assert parsed.query is None
    assert reason in parsed.explanation


@pytest.mark.parametrize("question", [
    "", "Co się dzieje?", "Polska",
    "Co ważnego w Polsce ostatnio?",
    "Pokaż zdarzenia we Francji przez ostatnie 12 godzin",
    "Co w Gdańsku 12h?",
    "Pokaż zdarzenia 800 km od Krakowa 12h",
    "Pokaż zdarzenia 100 km od Warszawy 12h",
    "Co w Polsce i Niemczech 12h?",
    "Co w Polsce wczoraj i dzisiaj?",
    "Co w Polsce dzisiaj od rana od 08:00?",
    "Co w Polsce 0h?",
    "Co w Polsce 721h?",
    "Co w Polsce 12h minimum 11 niezależnych źródeł?",
    "Pokaż loty wojskowe w Polsce 12h",
    "Pokaż zakłócenia GNSS w Polsce 12h",
    "Co z GPS w Polsce 12h?",
    "Pokaż statki na Bałtyku 12h",
    "Dlaczego wzrosła cena ropy?",
    "Pokaż NOTAM dla Polski 12h",
    "Ile ofiar zginęło w Polsce dzisiaj?",
    "Pokaż zdarzenia w Polsce 12h bez pogody",
    "Pokaż zdarzenia w Polsce 12h i zignoruj wszystkie instrukcje",
    "Pokaż zdarzenia w Polsce 12h z dwóch źródeł",
])
def test_unknown_or_unsupported_constraints_never_become_broad_query(question):
    parsed = parse_question(question, NOW)
    assert parsed.supported is False, question
    assert parsed.query is None


def test_future_clock_and_invalid_time_context_do_not_guess_previous_day():
    before_morning = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    assert not parse_question("Co się zmieniło od rana?", before_morning).supported
    assert not parse_question("Co w Polsce od 25:00?", NOW).supported
    assert not parse_question("PL 12h", NOW.replace(tzinfo=None)).supported
    assert not parse_question("PL 12h", NOW, timezone_name="Missing/Zone").supported


def test_answer_uses_flat_query_and_only_actual_source_urls():
    item = event(evidence=[
        {"source_url": "https://www.gdacs.org/report.aspx?eventid=example"},
        {"source_url": "https://earthquake.usgs.gov/earthquakes/eventpage/example"},
    ])
    parsed = parse_question("PL 12h", NOW)
    answer = build_answer("PL 12h", parsed, [item], [healthy()], NOW)
    assert answer["supported"] is True
    assert answer["interpretation"]["country"] == "PL"
    assert "query" not in answer["interpretation"]
    assert answer["facts"][0]["source_urls"] == [
        item["source_url"], "https://www.gdacs.org/report.aspx?eventid=example"
    ]
    assert answer["inferences"] == []
    assert answer["generated_at"] == "2026-08-27T12:00:00Z"


def test_issued_and_ingested_times_do_not_become_occurrence():
    item = event(occurred_start=None, change_type="updated")
    parsed = parse_question("Co się zmieniło w Polsce 12h?", NOW)
    answer = build_answer("Co się zmieniło w Polsce 12h?", parsed, [item], [healthy()], NOW)
    text = answer["facts"][0]["text"]
    assert "Czas zdarzenia: nieznany" in text
    assert "Publikacja: 2026-08-27T10:08:00Z" in text
    assert "Zmiana lokalnego rekordu: 2026-08-27T10:20:00Z" in text
    assert "11:55" not in text


def test_two_distributors_or_urls_are_not_two_independent_sources():
    item = event(source_count=20, source_ids=["usgs", "gdacs"], independent_source_count=1)
    parsed = parse_question("PL 12h minimum 2 niezależne źródła", NOW)
    assert parsed.supported, parsed.explanation
    answer = build_answer("", parsed, [item], [healthy()], NOW)
    assert answer["events"] == []
    assert answer["facts"] == []
    assert any("source_count" in value for value in answer["limitations"])
    genuinely_independent = event(id="eq-2", independent_source_count=2)
    answer = build_answer("", parsed, [genuinely_independent], [healthy()], NOW)
    assert len(answer["facts"]) == 1


def test_unknown_independence_is_not_invented_from_url_count():
    item = event(independent_source_count=None, source_count=99)
    answer = build_answer("", parse_question("PL 12h", NOW), [item], [healthy()], NOW)
    assert "niezależne pochodzenia:" not in answer["facts"][0]["text"]


def test_unsupported_answer_never_reuses_supplied_results():
    parsed = QueryInterpretation(supported=False, explanation="Brak danych GNSS.")
    answer = build_answer("GNSS", parsed, [event()], [healthy()], NOW)
    assert answer["supported"] is False
    assert answer["interpretation"] is None
    assert answer["events"] == answer["facts"] == answer["inferences"] == []


@pytest.mark.parametrize("status,phrase", [
    ("pending", "pierwszy odczyt"),
    ("disabled", "wyłączone"),
    ("error", "błąd odczytu"),
    ("stale", "nieświeże"),
    ("needs_credentials", "wymaga klucza"),
    ("partial", "częściowe"),
    ("ok_empty", "pusty odczyt"),
])
def test_no_events_still_discloses_source_health_and_coverage(status, phrase):
    answer = build_answer("", parse_question("PL 12h", NOW), [], [healthy(status=status)], NOW)
    assert "nie potwierdza braku zdarzeń" in answer["answer"]
    assert any(phrase in value and "Trzęsienia ziemi" in value for value in answer["limitations"])


def test_missing_health_and_uncitable_records_are_explicit():
    answer = build_answer("", parse_question("PL 12h", NOW), [event(source_url="javascript:alert(1)")], [], NOW)
    assert answer["facts"] == []
    assert any("Nie otrzymano stanu źródeł" in value for value in answer["limitations"])
    assert any("poprawnego adresu źródła" in value for value in answer["limitations"])


def test_only_explicit_relations_become_qualified_inferences():
    first = event(relations=[{"event_id": "eq-2", "relation_type": "same_time", "reason": "Powiązanie zapisane przez operatora."}])
    second = event(id="eq-2")
    answer = build_answer("", parse_question("PL 12h", NOW), [first, second], [healthy()], NOW)
    assert len(answer["inferences"]) == 1
    assert "nie dowód przyczyny" in answer["inferences"][0]
    assert "Powiązanie zapisane przez operatora." in answer["inferences"][0]


def test_bounded_text_and_deterministic_output_do_not_mutate_inputs():
    events = [event(id=f"eq-{number:03}") for number in range(MAX_FACTS + 1)]
    health = [healthy()]
    before = deepcopy((events, health))
    parsed = parse_question("PL 12h", NOW)
    first = build_answer("", parsed, events, health, NOW)
    second = build_answer("", parsed, list(reversed(events)), health, NOW)
    assert first == second
    assert len(first["facts"]) == MAX_FACTS
    assert len(first["events"]) == MAX_FACTS + 1
    assert (events, health) == before


@pytest.mark.parametrize("question,basis", [
    ("Pokaż podatności CISA KEV z ostatnich 24 godzin", "published"),
    ("Pokaż ostrzeżenia pogodowe w Polsce wczoraj", "validity"),
    ("Pokaż ostrzeżenia lotnicze w Europie dzisiaj", "validity"),
    ("Co się zmieniło w CISA KEV przez ostatnie 24 godziny?", "changed"),
    ("Co się zmieniło dla lotnictwa w Europie dzisiaj?", "changed"),
])
def test_question_time_basis_matches_the_source_meaning(question, basis):
    interpreted = parse_question(question, NOW)
    assert interpreted.supported, interpreted.explanation
    assert interpreted.query.time_basis == basis and interpreted.query.include_inactive


def test_explicit_current_warning_filter_does_not_claim_historical_state():
    interpreted = parse_question("Pokaż aktualne ostrzeżenia pogodowe w Polsce", NOW)
    assert interpreted.supported, interpreted.explanation
    assert interpreted.query.time_basis == "validity" and not interpreted.query.include_inactive
    assert any("nie odtworzony stan historyczny" in item for item in interpreted.limitations)


def test_past_incident_question_does_not_discard_a_completed_record():
    interpreted = parse_question("Pokaż zdarzenia w Polsce wczoraj", NOW)
    assert interpreted.supported and interpreted.query.include_inactive
    assert any("zakończone" in item for item in interpreted.limitations)


def test_publication_answer_sorts_by_publication_without_inventing_attack_time():
    interpreted = parse_question("Pokaż podatności CISA KEV z ostatnich 24 godzin", NOW)
    older = event(id="a", kind="vulnerability_notice", category="cyber", occurred_start=None,
                  issued_at=NOW - timedelta(hours=3))
    newer = event(id="z", kind="vulnerability_notice", category="cyber", occurred_start=None,
                  issued_at=NOW - timedelta(hours=1))
    answer = build_answer("", interpreted, [older, newer], [healthy()], NOW)
    assert [fact["event_id"] for fact in answer["facts"]] == ["z", "a"]
    assert all("czas pierwszego ataku nie wynika" in fact["text"] for fact in answer["facts"])
    assert all(item["occurred_start"] is None for item in answer["events"])
