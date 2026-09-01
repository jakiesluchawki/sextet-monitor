"""Bounded Polish query interpretation and source-grounded response formatting.

No network, database, geocoding, or model calls belong in this module.
The caller applies EventQuery to the database before calling build_answer.
"""
from __future__ import annotations

import math
import re
import unicodedata
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import EventQuery, QueryInterpretation, utcnow

UTC = timezone.utc
WARSAW = (52.2297, 21.0122)
MAX_FACTS = 30
MAX_QUESTION_LENGTH = 2000

_COUNTRIES = {
    "PL": ("Polska", r"polska|polsce|polski|polske|pl"),
    "DE": ("Niemcy", r"niemcy|niemczech|niemiec|de"),
    "CZ": ("Czechy", r"czechy|czechach|czech|cz"),
    "SK": ("Słowacja", r"slowacja|slowacji|slowacje|sk"),
    "UA": ("Ukraina", r"ukraina|ukrainie|ukrainy|ukraine|ua"),
    "BY": ("Białoruś", r"bialorus|bialorusi"),
    "LT": ("Litwa", r"litwa|litwie|litwy|litwe|lt"),
    "RU": ("Rosja", r"rosja|rosji|rosje|ru"),
}
_CATEGORIES = {
    "earthquake": (r"trzesieni(?:a|e|ach|ami)? ziemi|wstrzas(?:y|ach|ow|u)?", "trzęsienia ziemi"),
    "disaster": (r"katastrof(?:y|a|ach|ami)?", "katastrofy"),
    "weather": (r"ostrzezeni(?:a|e|ach)? (?:pogodow(?:e|ych)|meteorologiczn(?:e|ych))|pogod(?:a|y|zie|owe)", "pogoda"),
    "aviation": (r"ostrzezeni(?:a|e|ach)? (?:dla lotnictwa|lotnicz(?:e|ych))|dla lotnictwa|lotnictw(?:a|o|ie)|(?:ostrzezenia )?(?:easa(?: czib)?|czib)", "ostrzeżenia lotnicze EASA CZIB"),
    "cyber": (r"podatnosci(?:ach|ami)?|cyber|cisa(?: kev)?|kev", "komunikaty o podatnościach"),
    "internet": (r"(?:awari(?:e|i|a)|zakloceni(?:a|ach)) internetu|internet(?:u|em)?", "zakłócenia Internetu"),
}
_GENERIC_WORDS = frozenset(
    "co sie dzieje dzialo wydarzylo wydarzyly wystapilo wystapily wydarzenia wydarzen "
    "zdarzenia zdarzen zdarzenie wazne waznego waznych istotne istotnego istotnych "
    "najwazniejsze najwazniejszego najwazniejszych pokaz wyswietl wypisz sprawdz znajdz "
    "podsumuj prosze mi nam z w we na dla od do za o i oraz a tylko wszystkie przez maja ma czy jakies "
    "ktore jakie sa byly aktualne aktualnie obecnie teraz ostatnie ostatnich ciagu "
    "lista liste informacje informacji informacja lokalne zapisane zarejestrowane "
    "odnotowane dotyczace dotyczacych dotyczacego".split()
)
_INTENT = re.compile(
    r"\b(?:co|pokaz|wyswietl|wypisz|sprawdz|znajdz|podsumuj|jakie|ktore|"
    r"zdarzeni\w*|zdarzen|wydarzeni\w*|wydarzen|lista|liste)\b"
)
_CHANGE = r"\b(?:zmienilo|zmienily|zmieniono|zmienione|zmienionych|zmiany|zmian|zaktualizowano|aktualizacje)\b"
_COVERAGE = (
    "Pokrycie ogranicza się do podłączonych źródeł. Brak rekordu nie potwierdza "
    "braku zdarzeń ani bezpieczeństwa obszaru."
)
_AVIATION = (
    "Lotnictwo obejmuje wyłącznie publiczne ostrzeżenia EASA CZIB; nie jest to "
    "baza NOTAM, pełny wykaz zamknięć przestrzeni ani śledzenie lotów."
)
_CYBER = (
    "CISA KEV opisuje podatności wykorzystywane według CISA. Data dodania do katalogu "
    "nie jest datą pierwszego ataku ani dowodem ataku w danym kraju."
)
_INDEPENDENCE = (
    "Niezależność pochodzi z independent_source_count. Mirrory, języki, liczba "
    "adresów URL i liczba dystrybutorów nie stanowią dodatkowych potwierdzeń."
)


def _fold(value: str) -> str:
    value = value.casefold().replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def _unsupported(explanation: str, *limitations: str) -> QueryInterpretation:
    return QueryInterpretation(supported=False, query=None, explanation=explanation, limitations=list(limitations))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Czas musi zawierać strefę lub przesunięcie UTC.")
    return value.astimezone(UTC)


def _wall_time(day: date, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    """Reject nonexistent and ambiguous wall times instead of choosing a DST fold."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Niepoprawna godzina. Użyj formatu HH:MM od 00:00 do 23:59.")
    naive = datetime.combine(day, time(hour, minute))
    possibilities: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            possibilities[instant] = candidate
    if not possibilities:
        raise ValueError("Ta godzina lokalna nie istnieje z powodu zmiany czasu. Podaj inny przedział.")
    if len(possibilities) > 1:
        raise ValueError("Ta godzina lokalna występuje dwukrotnie przy zmianie czasu. Podaj jednoznaczny przedział.")
    return next(iter(possibilities))


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def parse_question(
    question: str,
    now: datetime | None = None,
    timezone_name: str = "Europe/Warsaw",
) -> QueryInterpretation:
    """Parse a deliberately small grammar; unconsumed constraints fail closed."""
    if not isinstance(question, str) or not question.strip():
        return _unsupported("Podaj obszar i okres, np. „Co wydarzyło się w Polsce przez ostatnie 12 godzin?”.")
    if len(question) > MAX_QUESTION_LENGTH:
        return _unsupported("Pytanie jest zbyt długie dla ograniczonego parsera. Podaj jeden obszar i jeden okres.")
    text = _fold(question.strip())
    if re.search(r"\b(?:gps|gnss|gpsjam|jamming|spoofing)\b", text):
        return _unsupported("Nie mam włączonego źródła pomiarów GPS/GNSS ani potwierdzonych danych o zakłócaniu nawigacji.")
    if re.search(r"\b(?:ais|statk\w*|okret\w*|morz\w*|morsk\w*|porty|portow)\b", text):
        return _unsupported("Nie mam włączonego źródła AIS ani pełnych danych o ruchu morskim i portach.")
    if re.search(r"\b(?:ropa|ropy|rope|brent|wti|notowan\w*|gield\w*)\b", text):
        return _unsupported("Nie mam danych rynkowych pozwalających odpowiedzieć o ropie lub wyjaśnić przyczyn zmiany jej ceny.")
    if re.search(r"\b(?:wojsk\w*|militarn\w*|czolg\w*|bombow\w*|mysliwc\w*|front\w*|wojenne|wojennych|cyberatak\w*)\b", text):
        return _unsupported("Nie mam źródła bieżących działań wojskowych, śledzenia jednostek ani kompletnego strumienia ataków.")
    if re.search(r"\b(?:notam\w*|samolot\w*|loty|lotow|lotu|sledz\w*|pozycj\w*|tras\w*)\b", text):
        return _unsupported("Nie obsługuję pozycji samolotów, tras lotów ani NOTAM. Dostępne są tylko publiczne ostrzeżenia EASA CZIB.", _AVIATION)
    if re.search(r"\b(?:dlaczego|przyczyn\w*|spowodow\w*|winny|sprawc\w*|ofiar\w*|zgin\w*|rann\w*)\b", text):
        return _unsupported("Nie mogę ustalić tej przyczyny ani liczby osób z dostępnych rekordów. Mogę pokazać źródłowe zdarzenia dla wskazanego obszaru i czasu.")
    if re.search(r"\b(?:nie|bez|oprocz|wyklucz\w*)\b", text):
        return _unsupported("Parser nie obsługuje wykluczeń ani zaprzeczeń. Podaj dodatni filtr obszaru, kategorii i okresu.")

    try:
        reference = _as_utc(now if now is not None else utcnow())
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return _unsupported("Nie można ustalić jednoznacznego czasu odniesienia i strefy zapytania.")
    local_now = reference.astimezone(zone)
    remaining = re.sub(r"[?,.!;()\[\]„”\"']", " ", text)
    remaining = re.sub(r"\s+", " ", remaining).strip()
    limitations: list[str] = []
    notes: list[str] = []

    radius_matches = list(re.finditer(
        r"\b(?:w promieniu |promien )?(\d+(?:[.,]\d+)?)\s*km\s+(?:od|wokol)\s+warszawy\b",
        remaining,
    ))
    radius = None
    if radius_matches:
        if len(radius_matches) != 1 or float(radius_matches[0].group(1).replace(",", ".")) != 800:
            return _unsupported("Obsługiwany filtr odległości to obecnie „800 km od Warszawy”. Innego promienia nie interpretuję automatycznie.")
        radius = 800.0
        remaining = remaining[:radius_matches[0].start()] + " " + remaining[radius_matches[0].end():]
        limitations.append("Promień liczony od punktu Warszawy 52.2297, 21.0122; rekordy bez geometrii nie potwierdzają braku zdarzeń w tym obszarze.")

    countries: list[str] = []
    for code, (_, aliases) in _COUNTRIES.items():
        pattern = rf"\b(?:{aliases})\b"
        if re.search(pattern, remaining):
            countries.append(code)
            remaining = re.sub(pattern, " ", remaining)
    # BY is an ISO code only when explicitly upper-case, not the Polish conjunction "by".
    if re.search(r"\bBY\b", question):
        if "BY" not in countries:
            countries.append("BY")
        remaining = re.sub(r"\bby\b", " ", remaining)
    europe = bool(re.search(r"\b(?:europa|europie|europy|europe)\b", remaining))
    remaining = re.sub(r"\b(?:europa|europie|europy|europe)\b", " ", remaining)
    global_scope = bool(re.search(r"\b(?:swiat|swiecie|globalnie)\b", remaining))
    remaining = re.sub(r"\b(?:swiat|swiecie|globalnie)\b", " ", remaining)
    if len(countries) > 1 or sum((bool(countries), europe, radius is not None, global_scope)) > 1:
        return _unsupported("Podaj jeden obszar: jeden obsługiwany kraj, Europę albo promień 800 km od Warszawy.")

    categories: list[str] = []
    for category, (pattern, _) in _CATEGORIES.items():
        pattern = rf"\b(?:{pattern})\b"
        if re.search(pattern, remaining):
            categories.append(category)
            remaining = re.sub(pattern, " ", remaining)
    if len(categories) > 1:
        return _unsupported("W jednym pytaniu obsługuję jedną kategorię albo wszystkie kategorie. Podaj jeden taki zakres.")
    category = categories[0] if categories else None

    changed = bool(re.search(_CHANGE, remaining))
    remaining = re.sub(_CHANGE, " ", remaining)
    min_sources = 1
    source_matches = list(re.finditer(
        r"\b(?:co najmniej|minimum|min|przynajmniej)\s+(\d+|dwa|dwoch|dwoma)\s+"
        r"niezalezn(?:e|ych|ymi)\s+(?:zrodl(?:a|ach|ami)?|zrodel)\b", remaining
    ))
    if source_matches:
        if len(source_matches) != 1:
            return _unsupported("Podaj jeden próg liczby niezależnych źródeł.")
        value = source_matches[0].group(1)
        min_sources = int(value) if value.isdigit() else 2
        if not 1 <= min_sources <= 10:
            return _unsupported("Próg liczby niezależnych źródeł musi wynosić od 1 do 10.")
        match = source_matches[0]
        remaining = remaining[:match.start()] + " " + remaining[match.end():]
        limitations.append(_INDEPENDENCE)

    hour_matches = list(re.finditer(r"\b(\d+)\s*(?:h|godzin(?:y|e|a)?)\b", remaining))
    morning = bool(re.search(r"\bod rana\b", remaining))
    clock_matches = list(re.finditer(r"\bod (?:godziny )?(\d{1,2}):(\d{2})\b", remaining))
    today = bool(re.search(r"\b(?:dzisiaj|dzis|dzisiejsze)\b", remaining))
    yesterday = bool(re.search(r"\bwczoraj\b", remaining))
    has_explicit_time = bool(hour_matches or morning or clock_matches or today or yesterday)
    if len(hour_matches) > 1 or len(clock_matches) > 1:
        return _unsupported("Podaj jeden przedział czasu.")
    if (hour_matches and (morning or clock_matches or today or yesterday)) or (
        yesterday and (morning or clock_matches or today)
    ) or (morning and clock_matches):
        return _unsupported("Rozpoznano kilka różnych granic czasu. Podaj jeden jednoznaczny przedział.")

    until = reference
    try:
        if hour_matches:
            hours = int(hour_matches[0].group(1))
            if not 1 <= hours <= 720:
                return _unsupported("Obsługiwany okres wynosi od 1 do 720 godzin.")
            since = reference - timedelta(hours=hours)
            remaining = re.sub(r"\b\d+\s*(?:h|godzin(?:y|e|a)?)\b", " ", remaining)
        elif yesterday:
            until = _wall_time(local_now.date(), 0, 0, zone)
            since = _wall_time(local_now.date() - timedelta(days=1), 0, 0, zone)
        elif morning:
            since = _wall_time(local_now.date(), 6, 0, zone)
            notes.append(f"„Od rana” oznacza dziś 06:00 w {timezone_name}.")
        elif clock_matches:
            since = _wall_time(local_now.date(), int(clock_matches[0].group(1)), int(clock_matches[0].group(2)), zone)
            notes.append(f"Godzinę bez daty interpretuję jako dzisiejszą w {timezone_name}.")
        elif today:
            since = _wall_time(local_now.date(), 0, 0, zone)
        else:
            since = reference - timedelta(hours=24)
            notes.append("Nie podano okresu: przyjęto ostatnie 24 godziny.")
    except ValueError as exc:
        return _unsupported(str(exc))
    if since >= until:
        return _unsupported("Podana dzisiejsza godzina jeszcze nie minęła albo przedział jest pusty. Doprecyzuj dzień i czas.")
    remaining = re.sub(r"\bod rana\b|\bod (?:godziny )?\d{1,2}:\d{2}\b|\b(?:dzisiaj|dzis|dzisiejsze|wczoraj)\b", " ", remaining)

    allowed = set(_GENERIC_WORDS)
    if category == "aviation":
        allowed.update("konfliktow konflikty stref strefy ryzyka publiczne komunikaty".split())
    unconsumed = [word for word in re.findall(r"\w+|[^\w\s]", remaining) if word not in allowed]
    if unconsumed:
        return _unsupported(
            "Nie rozpoznaję wszystkich warunków pytania. Obsługuję PL, DE, CZ, SK, UA, BY, LT, RU, Europę "
            "lub 800 km od Warszawy oraz ostatnie N godzin, dzisiaj, wczoraj, od rana albo od HH:MM."
        )
    has_area = bool(countries or europe or radius is not None or global_scope)
    has_intent = bool(_INTENT.search(text) or changed)
    if not ((has_intent and (has_area or category or has_explicit_time)) or (
        has_explicit_time and (has_area or category)
    )):
        return _unsupported("Pytanie jest zbyt ogólne. Podaj obszar, kategorię lub okres; nie uruchamiam zapytania o całą bazę.")

    if category == "aviation":
        limitations.append(_AVIATION)
    if category == "cyber":
        limitations.append(_CYBER)
    if europe:
        limitations.append("Europa oznacza zdefiniowany zbiór krajów. RU/TR tylko w zdefiniowanym obszarze mapy; ich rekordy bez geometrii są pomijane.")
    if (countries or europe) and radius is None:
        limitations.append("Filtr obszaru nie obejmuje rekordów bez ustalonego kraju; nieznanej lokalizacji nie zgaduję.")
    if re.search(r"\b(?:wazn\w*|najwazn\w*|istotn\w*)\b", text):
        limitations.append("Nie wyliczam własnego wskaźnika ważności ani prawdopodobieństwa; porządek może korzystać wyłącznie z zapisanej ważności źródłowej.")
    basis = "changed" if changed else (
        "published" if category == "cyber" else "validity" if category in {"aviation", "weather"} else "occurred"
    )
    current_only = bool(re.search(r"\b(?:aktualne|aktualnie|obecnie|teraz)\b", text))
    include_inactive = changed or not current_only
    descriptions = {
        "changed": "Przedział dotyczy zmian lokalnych rekordów last_changed_at, nie czasu wystąpienia zdarzeń; uwzględnia także wycofane i wygasłe wpisy.",
        "occurred": "Przedział dotyczy occurred_start. Data publikacji ani pobrania nie zastępuje nieznanego czasu zdarzenia.",
        "published": "Przedział dotyczy publikacji issued_at. Publikacja datowana tylko na dzień jest dopasowana przez przecięcie dnia z oknem; jej godzina pozostaje nieznana. To nie czas ataku ani pobrania.",
        "validity": "Przedział ważności zadeklarowany przez źródło przecina okno. Status jest bieżący; to nie odtworzony stan historyczny. Brak końca ważności pozostaje nieznany.",
    }
    limitations.append(descriptions[basis])
    if include_inactive and not changed:
        limitations.append("Zapytanie o okres uwzględnia także zakończone i wycofane rekordy; ich bieżący status jest podany przy wyniku.")
    elif current_only and not changed:
        notes.append("Słowo „aktualne/teraz” ogranicza wynik do rekordów niewygasłych i niewycofanych obecnie.")
    query = EventQuery(
        window_hours=max(1, min(720, math.ceil((until - since).total_seconds() / 3600))),
        time_basis=basis,
        since=since,
        until=until,
        country=countries[0] if countries else None,
        region="europe" if europe else None,
        category=category,
        min_sources=min_sources,
        lat=WARSAW[0] if radius else None,
        lon=WARSAW[1] if radius else None,
        radius_km=radius,
        include_inactive=include_inactive,
    )
    area = _COUNTRIES[countries[0]][0] + f" ({countries[0]})" if countries else (
        "zdefiniowany region Europy" if europe else "800 km od Warszawy" if radius else
        "wszystkie obszary objęte zapisanymi źródłami"
    )
    category_label = _CATEGORIES[category][1] if category else "wszystkie obsługiwane kategorie"
    explanation = (
        f"Obszar: {area}. Zakres: {category_label}. "
        f"Czas w {timezone_name}: {since.astimezone(zone).isoformat()} — {until.astimezone(zone).isoformat()}; "
        f"UTC: {_iso(since)} — {_iso(until)}. "
        f"Podstawa czasu: {dict(changed='zmiana rekordu', occurred='czas zdarzenia', published='data publikacji', validity='okres ważności')[basis]}. "
        f"Minimum niezależnych źródeł: {min_sources}."
    )
    if not has_area:
        notes.append("Nie podano filtra geograficznego; zakres obejmuje wszystkie zapisane obszary.")
    return QueryInterpretation(
        supported=True, query=query, explanation=" ".join([explanation, *notes]), limitations=limitations
    )


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _display_time(event: dict, field: str) -> str | None:
    value = event.get(field)
    instant = _datetime(value)
    tags = event.get("tags") or []
    day_only = (field == "occurred_start" and event.get("time_precision") == "day") or (
        "date_only_utc_anchor" in tags and field in {"issued_at", "valid_from", "valid_to"}
    )
    if instant is None:
        if day_only and isinstance(value, str):
            try:
                return date.fromisoformat(value).isoformat() + " (dokładność: dzień)"
            except ValueError:
                pass
        return None
    if field == "valid_to" and "valid_to_exclusive_day_boundary" in tags:
        return (instant.date() - timedelta(days=1)).isoformat() + " (ostatni dzień ważności)"
    if day_only:
        return instant.date().isoformat() + " (dokładność: dzień)"
    return _iso(instant)


def _plain(value: Any, max_length: int = 1200) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:max_length]


def _source_urls(event: dict) -> list[str]:
    candidates = [event.get("source_url")]
    candidates.extend(event.get("source_urls") or [])
    for item in event.get("evidence") or []:
        if isinstance(item, dict):
            candidates.append(item.get("source_url"))
    result: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str) or any(c.isspace() for c in candidate):
            continue
        try:
            parsed = urlsplit(candidate)
            valid = parsed.scheme in {"https", "http"} and bool(parsed.netloc) and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if valid and candidate not in result:
            result.append(candidate)
    return result


def _independent_count(event: dict) -> int | None:
    value = event.get("independent_source_count")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _fact(event: dict, changed: bool = False, prefix: str = "") -> dict | None:
    event_id = event.get("id")
    title = _plain(event.get("title"), 800)
    urls = _source_urls(event)
    if not event_id or not title or not urls:
        return None
    kind = event.get("kind", "incident")
    parts = [prefix + title]
    if kind == "vulnerability_notice":
        parts.append("Komunikat o podatności; czas pierwszego ataku nie wynika z tego rekordu")
    elif kind == "advisory":
        start = _display_time(event, "valid_from")
        parts.append("Początek obowiązywania: " + (start or "nieznany"))
    else:
        label = "Czas pomiaru" if kind == "measurement" else "Czas zdarzenia"
        parts.append(f"{label}: {_display_time(event, 'occurred_start') or 'nieznany'}")
    for field, label in (
        ("issued_at", "Publikacja"),
        ("source_updated_at", "Aktualizacja u źródła"),
        ("valid_to", "Ważność do"),
    ):
        if value := _display_time(event, field):
            parts.append(f"{label}: {value}")
    if changed and (value := _display_time(event, "last_changed_at")):
        parts.append(f"Zmiana lokalnego rekordu: {value}")
    if event.get("change_type") == "initial_import":
        parts.append("Pierwszy import do lokalnej bazy, nie dowód nowego zdarzenia")
    lifecycle = event.get("lifecycle_status")
    if lifecycle in {"expired", "withdrawn", "unknown"}:
        parts.append("Status: " + {"expired": "wygasły", "withdrawn": "wycofany", "unknown": "nieznany"}[lifecycle])
    count = _independent_count(event)
    if count is not None:
        parts.append(f"Zapisane niezależne pochodzenia: {count}")
    return {"text": ". ".join(parts) + ".", "event_id": str(event_id), "source_urls": urls}


def _health_limitations(source_health: list[dict]) -> list[str]:
    if not source_health:
        return ["Nie otrzymano stanu źródeł; kompletność i świeżość danych są nieznane."]
    descriptions = {
        "pending": "oczekuje na pierwszy odczyt",
        "partial": "zwróciło tylko częściowe dane",
        "error": "ma błąd odczytu; dostępne rekordy mogą być starsze",
        "stale": "ma nieświeże dane",
        "needs_credentials": "wymaga klucza i nie zapewnia bieżącego pokrycia",
        "disabled": "jest wyłączone i nie zapewnia bieżącego pokrycia",
        "ok_empty": "zwróciło pusty odczyt; nie jest to dowód braku zdarzeń",
    }
    result: list[str] = []
    for source in sorted(source_health, key=lambda s: str(s.get("id", ""))):
        name = _plain(source.get("name") or source.get("id") or "Nieznane źródło", 150)
        status = "disabled" if source.get("enabled") is False else source.get("status", "unknown")
        description = descriptions.get(status)
        if status not in {"ok", *descriptions}:
            description = "ma nieznany stan odczytu"
        if description:
            coverage = _plain(source.get("coverage"), 500)
            result.append(f"{name}: {description}." + (f" Zakres źródła: {coverage}." if coverage else ""))
        elif "last_success_at" in source and source["last_success_at"] is None:
            result.append(f"{name}: brak daty udanego odczytu; świeżość nie jest potwierdzona.")
    return result


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _sort_events(events: list[dict], changed: bool = False, *, time_basis: str | None = None) -> list[dict]:
    def key(event: dict) -> tuple:
        severity = event.get("severity")
        severity = severity if isinstance(severity, int) and not isinstance(severity, bool) and 0 <= severity <= 4 else 0
        field = {"published": "issued_at", "validity": "valid_from", "changed": "last_changed_at"}.get(
            time_basis or ("changed" if changed else "occurred"), "occurred_start"
        )
        instant = _datetime(event.get(field))
        return (-severity, -(instant.timestamp() if instant else float("-inf")), str(event.get("id", "")))
    return sorted(events, key=key)


def _relations(events: list[dict], fact_ids: set[str]) -> list[str]:
    result: list[str] = []
    seen: set[tuple] = set()
    for event in sorted(events, key=lambda e: str(e.get("id", ""))):
        source_id = str(event.get("id", ""))
        if source_id not in fact_ids:
            continue
        relations = sorted(
            (r for r in event.get("relations") or [] if isinstance(r, dict)),
            key=lambda r: (str(r.get("event_id", "")), str(r.get("relation_type", "")), str(r.get("reason", ""))),
        )
        for relation in relations:
            target_id = str(relation.get("event_id", ""))
            reason = _plain(relation.get("reason"), 700)
            if not reason or target_id not in fact_ids:
                continue
            identity = (*sorted((source_id, target_id)), str(relation.get("relation_type", "")), reason)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(
                f"Relacja zapisana w danych, nie dowód przyczyny ani niezależne potwierdzenie "
                f"({source_id}, {target_id}): {reason}"
            )
    return result[:10]


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def build_answer(
    question: str,
    interpretation: QueryInterpretation,
    events: list[dict],
    source_health: list[dict],
    now: datetime | None = None,
) -> dict:
    """Describe only supplied database matches and their actual source addresses."""
    generated_at = _iso(now if now is not None else utcnow())
    health_limits = _health_limitations(source_health)
    limitations = _unique([*interpretation.limitations, *health_limits, _COVERAGE])
    supported = interpretation.supported and interpretation.query is not None
    result = {
        "supported": bool(supported),
        "answer": interpretation.explanation,
        "interpretation": interpretation.query.model_dump(mode="json") if supported else None,
        "query_explanation": interpretation.explanation,
        "events": [],
        "facts": [],
        "inferences": [],
        "limitations": limitations,
        "source_health": _json_value(deepcopy(source_health)),
        "generated_at": generated_at,
    }
    if not supported:
        return result

    query = interpretation.query
    selected = list(events)
    if query.min_sources > 1:
        selected = [event for event in selected if (_independent_count(event) or 0) >= query.min_sources]
        if omitted := len(events) - len(selected):
            limitations.append(f"Pominięto {omitted} rekordów bez wymaganej liczby niezależnych pochodzeń; liczba URL lub source_count nie wystarcza.")
    selected = _sort_events(selected, time_basis=query.time_basis)
    facts = [fact for event in selected if (fact := _fact(event, changed=query.time_basis == "changed"))]
    if omitted := len(selected) - len(facts):
        limitations.append(f"{omitted} rekordów bez identyfikatora, tytułu lub poprawnego adresu źródła nie użyto do sformułowania faktów.")
    if len(facts) > MAX_FACTS:
        limitations.append(f"Część opisowa pokazuje {MAX_FACTS} z {len(facts)} dopasowanych rekordów z odnośnikami; pozostałe są na liście wyników.")
        facts = facts[:MAX_FACTS]
    if len(selected) >= query.limit:
        limitations.append("Osiągnięto limit listy wyników; liczba pokazanych rekordów nie musi być pełną liczbą dopasowań.")
    if any(event.get("category") == "aviation" for event in selected):
        limitations.append(_AVIATION)
    if any(event.get("category") == "cyber" for event in selected):
        limitations.append(_CYBER)
    if any(event.get("kind") == "incident" and _datetime(event.get("occurred_start")) is None for event in selected):
        limitations.append("Co najmniej jeden rekord nie ma znanego czasu zdarzenia; data publikacji nie uzupełnia tej luki.")

    if selected:
        answer = f"Dopasowane rekordy w przekazanym wyniku: {len(selected)}. Fakty z odnośnikami do źródeł: {len(facts)}."
    else:
        answer = "W lokalnych danych nie ma rekordów spełniających rozpoznane filtry. To nie potwierdza braku zdarzeń."
    if health_limits:
        answer += " Stan źródeł wskazuje ograniczenia kompletności lub świeżości."
    result.update(
        answer=answer + " " + interpretation.explanation,
        events=_json_value(deepcopy(selected)),
        facts=facts,
        inferences=_relations(selected, {fact["event_id"] for fact in facts}),
        limitations=_unique(limitations),
    )
    return result
