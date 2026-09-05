"""IMGW's public hydrological warnings, with explicit UTC source clocks.

The official hydro.imgw.pl frontend uses this endpoint. The older
danepubliczne warningshydro endpoint has offsetless dates and is not a fallback.
This is a current warning list, not a river measurement feed or an archive.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import re

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    ProviderError, ensure_document, identifier, metadata, observed_now, plain,
    reject, required_title, timestamp, warn,
)

URL = "https://hydro-back.imgw.pl/alerts/warnings/hydro/getCurrentWarnings"
SOURCE_URL = "https://hydro.imgw.pl/#/warnings/hydro"
LICENSE_URL = "https://hydro.imgw.pl/#/regulamin"
ATTRIBUTION = (
    "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – "
    "Państwowy Instytut Badawczy. Dane Instytutu Meteorologii i Gospodarki Wodnej – "
    "Państwowego Instytutu Badawczego zostały przetworzone."
)
MAX_RECORDS = 500
MAX_BYTES = 5 * 1024 * 1024
_FLAGS = (
    "isUpdate", "isDelete", "statusIsChanged", "statusIsRevoked",
    "statusIsToConfirm", "statusIsCurrent", "statusIsFuture", "statusIsPast",
    "isUntilRevoke",
)


def _nonfinite(_value):
    raise ValueError("non-finite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _clock(record, name, batch):
    value = record.get(name)
    if value is not None and (not isinstance(value, str) or len(value) > 80):
        raise ValueError("invalid IMGW timestamp field")
    # Some older IMGW formats use year 9999 as an open-ended sentinel.
    if name == "dateTo" and isinstance(value, str) and value.startswith("9999-"):
        warn(batch.warnings, "IMGW: data końca 9999 oznacza brak daty; nie jest terminem ważności.")
        return None, "unknown"
    return timestamp(value, warnings=batch.warnings, field="IMGW " + name)


def _areas(record):
    provinces = record.get("provinces", [])
    if not isinstance(provinces, list) or len(provinces) > 16:
        raise ValueError("invalid IMGW provinces")
    names, codes = set(), set()
    for province in provinces:
        if not isinstance(province, dict):
            raise ValueError("invalid IMGW province")
        name = plain(province.get("name"), 100)
        if name:
            names.add(name)
        areas = province.get("areas", [])
        if not isinstance(areas, list) or len(areas) > 200:
            raise ValueError("invalid IMGW catchments")
        for area in areas:
            code = identifier(area, "catchment")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", code):
                raise ValueError("invalid IMGW catchment code")
            codes.add(code)
    return sorted(names), sorted(codes)


def _record(record, batch, now):
    if not isinstance(record, dict):
        raise ValueError("invalid IMGW record")
    record_id = identifier(record.get("id"))
    if not re.fullmatch(r"[1-9][0-9]{0,19}", record_id):
        raise ValueError("invalid IMGW warning id")
    flags = {}
    for name in _FLAGS:
        value = record.get(name)
        if value is not None and not isinstance(value, bool):
            raise ValueError("invalid IMGW status flag")
        flags[name] = value is True
    # Draft/unconfirmed material is not a public warning, even if a changed
    # upstream accidentally includes it in this endpoint.
    if flags["statusIsToConfirm"]:
        batch.metadata["excluded_unconfirmed"] += 1
        warn(batch.warnings, "IMGW: pominięto niepotwierdzony komunikat w publicznym kanale.")
        return None

    event_name = required_title(record.get("eventDescription"))
    area = plain(record.get("areaDescription"), 1000)
    province_names, catchments = _areas(record)
    issued, issue_precision = _clock(record, "releaseDate", batch)
    valid_from, start_precision = _clock(record, "dateFrom", batch)
    valid_to, _ = _clock(record, "dateTo", batch)
    reference, _ = _clock(record, "referenceDate", batch)
    if issued and issued > now + timedelta(minutes=5):
        warn(batch.warnings, "IMGW: przyszła publikacja nie jest potwierdzonym czasem wydania.")
        issued = None
    bad_interval = bool(valid_from and valid_to and valid_to <= valid_from)
    if bad_interval:
        warn(batch.warnings, "IMGW: koniec poprzedza początek ważności; przedział pozostawiono nieznany.")
        valid_from = valid_to = None

    degree = record.get("degree")
    if not isinstance(degree, int) or isinstance(degree, bool):
        raise ValueError("invalid IMGW degree")
    severity = {1: 2, 2: 3, 3: 4}.get(degree, 0)
    reason = (
        "Stopnie IMGW 1/2/3 (żółty/pomarańczowy/czerwony) odwzorowano na poziomy 2/3/4. "
        "To poziom ostrzeżenia, nie potwierdzenie wystąpienia szkód."
    )
    if degree == -1:
        reason = (
            "IMGW oznacza suszę hydrologiczną kodem -1, poza porządkową skalą stopni 1–3. "
            "Poziom 0 oznacza brak porównywalnej oceny, nie brak zagrożenia."
        )
    elif degree not in (1, 2, 3):
        warn(batch.warnings, "IMGW: nieznany stopień ostrzeżenia; bez przypisanej oceny zagrożenia.")

    tags = ["imgw_hydrological_warning", "current_list_not_archive", "area_description_no_geometry"]
    if degree == -1:
        tags.append("hydrological_drought")
    if flags["isUntilRevoke"]:
        tags.append("until_revoked")
        if valid_to is not None:
            warn(batch.warnings, "IMGW: do odwołania i podany koniec są sprzeczne; ważność niepewna.")
            bad_interval = True
            valid_from = valid_to = None
    if flags["isUpdate"] or reference:
        # referenceDate is not the previous warning's ID. Do not merge by
        # similarity, office, warning number, or a guessed historical link.
        tags.append("update_reference_unresolved")

    status_count = sum(flags[name] for name in (
        "statusIsCurrent", "statusIsFuture", "statusIsPast", "statusIsRevoked",
    ))
    if flags["isDelete"] or flags["statusIsRevoked"]:
        lifecycle = "withdrawn"
    elif bad_interval or status_count > 1:
        lifecycle = "unknown"
        if status_count > 1:
            warn(batch.warnings, "IMGW: sprzeczne statusy komunikatu; stan pozostawiono nieznany.")
    elif valid_to and valid_to <= now:
        lifecycle = "expired"
    elif flags["statusIsPast"]:
        lifecycle = "expired"
    elif (flags["statusIsCurrent"] and valid_from and valid_from <= now
          and (valid_to is not None or flags["isUntilRevoke"])):
        lifecycle = "active"
    else:
        lifecycle = "unknown"
    if valid_from and valid_from > now:
        tags.append("hazard_onset_in_future")
    if valid_to is None and not flags["isUntilRevoke"]:
        tags.append("expiry_not_known")
        warn(batch.warnings, "IMGW: brak daty końca i informacji o ważności do odwołania.")

    description = []
    if area:
        description.append("Obszar: " + area + ".")
    if province_names:
        description.append("Województwa: " + ", ".join(province_names) + ".")
    for field in ("run", "comment"):
        value = plain(record.get(field), 4000)
        if value:
            description.append(value)
    if flags["isUntilRevoke"]:
        description.append("IMGW: ważne do odwołania, według stanu bieżącej listy w chwili pobrania.")
    if "update_reference_unresolved" in tags:
        description.append("IMGW oznacza komunikat jako aktualizację; poprzedniego komunikatu nie powiązano.")
    for field, instant, label in (
        ("releaseDate", issued, "Publikacja"),
        ("dateFrom", valid_from, "Początek ważności"),
        ("dateTo", valid_to, "Koniec ważności"),
    ):
        if instant is None and record.get(field):
            description.append(label + " w źródle (czas nieustalony): " + plain(record[field], 80) + ".")
    description.append("Zasięg jest opisowy; nie wyznaczono poligonu ostrzeżenia.")
    description.append(ATTRIBUTION)
    raw = {name: record.get(name) for name in (
        "id", "number", "office", "officeDescription", "event", "degree",
        "releaseDate", "dateFrom", "dateTo", "referenceDate", "probability",
    )}
    raw.update(flags)
    raw.update(api_url=URL, province_names=province_names, catchment_codes=catchments)
    return NormalizedEvent(
        source_id="imgw_hydro", provider_record_id=record_id,
        kind="advisory", category="weather",
        title=required_title(event_name + (" — " + area if area else "")),
        description="\n".join(description)[:12000], source_url=SOURCE_URL,
        issued_at=issued, source_updated_at=issued,
        valid_from=valid_from, valid_to=valid_to, countries=["PL"],
        geometry=None, location_precision="country",
        time_precision=start_precision if valid_from else issue_precision if issued else "unknown",
        severity=severity, original_severity=str(degree), severity_reason=reason,
        lifecycle_status=lifecycle, verification_status="official_warning",
        origins=["imgw"], external_ids=["imgw:hydro:" + record_id],
        tags=tags, raw=raw,
    )


def parse(doc: FetchedDocument) -> ProviderBatch:
    ensure_document(doc, "IMGW hydrologia")
    if len(doc.body) > MAX_BYTES:
        raise ProviderError("IMGW hydrologia: odpowiedź przekracza limit 5 MiB")
    try:
        records = json.loads(doc.body, parse_constant=_nonfinite, parse_float=_finite_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProviderError("IMGW hydrologia: nieprawidłowy JSON") from exc
    if not isinstance(records, list):
        raise ProviderError("IMGW hydrologia: oczekiwano listy komunikatów")
    batch = ProviderBatch(events=[], metadata=metadata(
        doc, len(records), excluded_unconfirmed=0, current_list_only=True,
        publication_timezone="explicit_offset", geometry_available=False,
    ))
    if len(records) > MAX_RECORDS:
        warn(batch.warnings, "IMGW: lista przekroczyła limit 500 komunikatów; wynik częściowy.")
    now = observed_now(doc)
    seen = set()
    revisions = {}
    conflicting_revisions = set()
    for index, record in enumerate(records[:MAX_RECORDS]):
        try:
            event = _record(record, batch, now)
            if event is None:
                continue
            fingerprint = event.model_dump_json()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            revision_key = (event.provider_record_id, event.source_updated_at)
            if revision_key in conflicting_revisions:
                reject(batch, "IMGW hydrologia", index, ValueError("conflicting revision"))
                continue
            if revision_key in revisions:
                # Equal source clocks do not establish which differing payload
                # is current. Remove both instead of trusting array order.
                previous = revisions.pop(revision_key)
                batch.events.remove(previous)
                conflicting_revisions.add(revision_key)
                batch.rejected_count += 1
                reject(batch, "IMGW hydrologia", index, ValueError("conflicting revision"))
                warn(batch.warnings, "IMGW: różna treść przy tym samym ID i czasie; pominięto obie wersje.")
                continue
            revisions[revision_key] = event
            batch.events.append(event)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(batch, "IMGW hydrologia", index, exc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    batch.events.sort(key=lambda event: (event.provider_record_id, event.source_updated_at or epoch))
    latest = max((event.issued_at for event in batch.events if event.issued_at), default=None)
    batch.metadata["provider_timestamp"] = latest.isoformat() if latest else None
    batch.metadata["unlinked_updates"] = sum(
        "update_reference_unresolved" in event.tags for event in batch.events
    )
    batch.metadata["current_list_fetched_at"] = now.isoformat()
    batch.metadata["current_list_complete"] = bool(
        not batch.warnings and not batch.rejected_count
        and not batch.metadata["excluded_unconfirmed"] and len(records) <= MAX_RECORDS
    )
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    return parse(await fetcher.get(URL, headers={"Accept": "application/json"}))
