"""Deterministic briefings over changes already selected by the database."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .query import (
    MAX_FACTS,
    _AVIATION,
    _COVERAGE,
    _CYBER,
    _INDEPENDENCE,
    _as_utc,
    _datetime,
    _fact,
    _health_limitations,
    _iso,
    _json_value,
    _relations,
    _sort_events,
    _unique,
)

_GROUPS = (
    ("published", "Publikacje z udokumentowanym czasem w oknie"),
    ("day_publication", "Publikacje datowane na dni okna — godzina nieznana"),
    ("occurred", "Zdarzenia z udokumentowanym czasem w oknie — pierwszy odczyt"),
    ("day_occurrence", "Zdarzenia datowane na dni okna — pierwszy odczyt, godzina nieznana"),
    ("new", "Nowe zapisy w lokalnej bazie"),
    ("updated", "Zaktualizowane rekordy"),
    ("withdrawn", "Wycofane komunikaty"),
    ("other", "Pozostałe przekazane zmiany"),
)


def _publication_scope(event: dict, since: datetime, until: datetime) -> str | None:
    issued = _datetime(event.get("issued_at"))
    if issued is None:
        return None
    if "date_only_utc_anchor" in (event.get("tags") or []):
        # Midnight is a storage anchor, not the hour the publisher issued the item.
        day_start = issued.replace(hour=0, minute=0, second=0, microsecond=0)
        if day_start < until and day_start + timedelta(days=1) > since:
            return "day_publication"
        return None
    return "published" if since <= issued < until else None


def _occurrence_scope(event: dict, since: datetime, until: datetime) -> str | None:
    # Incident and measurement clocks are source evidence, not publication clocks.
    # Advisory validity and KEV catalog dates must never be treated as incidents.
    if event.get("kind") not in {"incident", "measurement"}:
        return None
    occurred = _datetime(event.get("occurred_start"))
    if occurred is None:
        return None
    if event.get("time_precision") == "day":
        day_start = occurred.replace(hour=0, minute=0, second=0, microsecond=0)
        if day_start < until and day_start + timedelta(days=1) > since:
            return "day_occurrence"
        return None
    return "occurred" if since <= occurred < until else None


def build_briefing(
    events: Iterable[dict],
    source_health: list[dict],
    since: datetime,
    until: datetime,
    first_briefing: bool = False,
) -> dict:
    """Consume the entire selected snapshot once, retaining at most MAX_FACTS records.

    Database callers can yield bounded batches. Every record contributes to the
    counters, including records outside the short narrative. The caller verifies
    the processed count and advances its cursor only after successful exhaustion.
    """
    since, until = _as_utc(since), _as_utc(until)
    if since >= until:
        raise ValueError("since must precede until")

    group_counts = {key: 0 for key, _ in _GROUPS}
    selected: list[dict] = []
    historical = uncitable = citable = processed = 0
    has_aviation = has_cyber = missing_change_time = False
    limitations = [
        *_health_limitations(source_health), _COVERAGE, _INDEPENDENCE,
        "Briefing opisuje przekazane zmiany lokalnych rekordów. Data pobrania i pierwszy import nie są czasem zdarzenia; aktualizacja nie jest nowym zdarzeniem.",
    ]
    if first_briefing:
        limitations.append("Pierwszy briefing ustanawia punkt startowy. Nie istnieje poprzedni briefing do porównania.")
    for event in events:
        processed += 1
        has_aviation |= event.get("category") == "aviation"
        has_cyber |= event.get("category") == "cyber"
        change_type = event.get("change_type")
        missing_change_time |= change_type not in {"initial_import", "new"} and not _datetime(event.get("last_changed_at"))
        initial_scope = _publication_scope(event, since, until) or _occurrence_scope(event, since, until)
        if change_type == "initial_import":
            key = initial_scope
        elif change_type == "withdrawn":
            key = "withdrawn"
        elif change_type == "updated":
            key = "updated"
        elif first_briefing:
            key = initial_scope
        elif change_type == "new":
            key = "new"
        else:
            key = "other"
        if key is None:
            historical += 1
            continue
        group_counts[key] += 1
        prefix = "Pierwszy odczyt. " if (
            change_type == "initial_import" or key in {"occurred", "day_occurrence"}
        ) else ""
        fact = _fact(event, changed=True, prefix=prefix)
        if fact is None:
            uncitable += 1
            continue
        citable += 1
        # Independent of iteration/batch order; memory is bounded by 31 candidates.
        candidate = {**event, "_briefing_group": key, "_briefing_fact": fact}
        selected = _sort_events([*selected, candidate], changed=True)[:MAX_FACTS]

    omitted_for_length = citable - len(selected)
    facts = [candidate["_briefing_fact"] for candidate in selected]
    sections: list[dict] = []
    for key, title in _GROUPS:
        items = [{"event_id": candidate["_briefing_fact"]["event_id"],
                  "text": candidate["_briefing_fact"]["text"]}
                 for candidate in selected if candidate["_briefing_group"] == key]
        if items:
            sections.append({"title": title, "items": items})
    if historical:
        sections.append({"title": f"Import historyczny / punkt startowy: {historical} rekordów", "items": []})
        limitations.append(
            f"{historical} rekordów z pierwszego importu lub punktu startowego nie ma udokumentowanej publikacji ani czasu incydentu/pomiaru przypisanego do okna. "
            "Zachowano je jako tło, nie jako nowe zdarzenia lub ataki."
        )
    if group_counts["day_publication"]:
        limitations.append(
            "Część publikacji ma tylko datę dzienną. Dzień przecina okno briefingu, ale nie można potwierdzić, "
            "że publikacja nastąpiła w jego dokładnych granicach godzinowych."
        )
    if group_counts["day_occurrence"]:
        limitations.append(
            "Część incydentów lub pomiarów ma tylko datę dzienną. Dzień przecina okno briefingu, ale nie można "
            "potwierdzić, że zdarzenie nastąpiło w jego dokładnych granicach godzinowych. "
            "Nie zastąpiono czasu zdarzenia datą publikacji ani pobrania."
        )
    if uncitable:
        limitations.append(f"{uncitable} rekordów bez identyfikatora, tytułu lub poprawnego adresu źródła pominięto w faktach.")
    if omitted_for_length:
        limitations.append(
            f"Pokazano maksymalnie {MAX_FACTS} faktów; pominięto w części opisowej {omitted_for_length} dalszych rekordów z odnośnikami. "
            "Wszystkie rekordy zakresu zostały przetworzone; skrócenie narracji nie jest pominięciem partii danych. "
            "Kursor obejmuje cały przetworzony zakres; lista zmian ma osobny limit wyników i może wymagać zawężenia filtrów."
        )
    if has_aviation:
        limitations.append(_AVIATION)
    if has_cyber:
        limitations.append(_CYBER)
    if missing_change_time:
        limitations.append("Niektóre przekazane rekordy nie mają last_changed_at; ich chwili zmiany nie potwierdzono i nie zastąpiono jej last_seen_at.")

    answer_parts = [
        "Pierwszy briefing: punkt startowy. Brak poprzedniego briefingu do porównania."
        if first_briefing else "Briefing zmian zapisanych w lokalnej bazie.",
        f"Przedział UTC: {_iso(since)} — {_iso(until)}.",
        f"Przetworzono rekordów: {processed}. Fakty z odnośnikami do źródeł: {len(facts)}.",
    ]
    if not facts:
        answer_parts.append("Brak zmian z udokumentowanym źródłem do przedstawienia w części bieżącej. To nie oznacza, że nic ważnego się nie wydarzyło.")
    if historical:
        answer_parts.append(f"Rekordy zachowane jako tło pierwszego importu: {historical}.")
    if omitted_for_length:
        answer_parts.append(f"Poza krótką narracją pozostaje {omitted_for_length} dalszych rekordów z odnośnikami.")
    if _health_limitations(source_health):
        answer_parts.append("Dostępność lub świeżość źródeł ogranicza ten obraz; sprawdź listę braków.")

    return {
        "answer": " ".join(answer_parts), "facts": facts,
        "inferences": _relations(selected, {fact["event_id"] for fact in facts}),
        "limitations": _unique(limitations), "source_health": _json_value(source_health), "sections": sections,
        "since": _iso(since), "until": _iso(until), "generated_at": _iso(until),
        "processed_count": processed, "citable_count": citable, "omitted_fact_count": omitted_for_length,
        "historical_count": historical, "uncitable_count": uncitable,
    }
