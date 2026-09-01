"""MeteoAlarm Atom discovery and CAP 1.2 lifecycle normalization."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    UTC, ProviderError, element_text, identifier, metadata, observed_now, plain,
    reject, required_title, retry_after, timestamp, warn, xml_document, xml_raw,
)

ATOM = "{http://www.w3.org/2005/Atom}"
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"
MAX_CAP_REQUESTS = 200
COUNTRIES = {
    "andorra": "AD", "austria": "AT", "belgium": "BE", "bosnia-herzegovina": "BA",
    "bulgaria": "BG", "croatia": "HR", "cyprus": "CY", "czechia": "CZ",
    "denmark": "DK", "estonia": "EE", "finland": "FI", "france": "FR",
    "germany": "DE", "greece": "GR", "hungary": "HU", "iceland": "IS",
    "ireland": "IE", "israel": "IL", "italy": "IT", "latvia": "LV",
    "lithuania": "LT", "luxembourg": "LU", "malta": "MT", "moldova": "MD",
    "montenegro": "ME", "netherlands": "NL", "north-macedonia": "MK",
    "norway": "NO", "poland": "PL", "portugal": "PT", "romania": "RO",
    "serbia": "RS", "slovakia": "SK", "slovenia": "SI", "spain": "ES",
    "sweden": "SE", "switzerland": "CH", "ukraine": "UA", "united-kingdom": "GB",
}


def feed_url(country: str) -> str:
    if country not in COUNTRIES:
        raise ProviderError("MeteoAlarm: kraj nie znajduje się na obsługiwanej liście kanałów")
    return "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-" + country


def _cap_url(href: str, base: str) -> str:
    value = urljoin(base, href)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or parsed.hostname != "feeds.meteoalarm.org"
        or parsed.port not in {None, 443} or parsed.username or parsed.password
        or not parsed.path.startswith("/api/v1/warnings/")
        or ".." in parsed.path.split("/") or "%" in parsed.path or parsed.fragment
    ):
        raise ValueError("unapproved CAP URL")
    return value


def _origin(sender: str) -> str:
    host = urlsplit(sender).hostname or (sender.rsplit("@", 1)[-1] if "@" in sender else "")
    host = host.lower()
    if host == "imgw.pl" or host.endswith(".imgw.pl"):
        return "imgw"
    if host == "usgs.gov" or host.endswith(".usgs.gov"):
        return "usgs"
    return "cap_sender:" + sender.casefold()


def _references(root, sender: str, warnings: list[str]) -> list[str]:
    result = []
    for reference in element_text(root, CAP + "references").split():
        pieces = reference.split(",", 2)
        if len(pieces) != 3:
            warn(warnings, "CAP: nieprawidłowy format references; nie połączono wskazania")
            continue
        if pieces[0] != sender:
            warn(warnings, "CAP: references wskazuje innego nadawcę; wymaga weryfikacji")
            continue
        try:
            result.append(identifier(pieces[1], "reference identifier"))
        except ValueError:
            warn(warnings, "CAP: nieprawidłowy identyfikator w references")
    return sorted(set(result))


def _polygon(value: str):
    ring = []
    for pair in value.split():
        components = pair.split(",")
        if len(components) != 2:
            raise ValueError("invalid CAP polygon")
        lat, lon = (float(part) for part in components)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("polygon outside WGS84")
        ring.append([lon, lat])
    if len(ring) < 4 or ring[0] != ring[-1] or len({tuple(pair) for pair in ring[:-1]}) < 3:
        raise ValueError("unclosed or degenerate CAP polygon")
    return [ring]


def _geometry(infos, warnings: list[str]):
    polygons = []
    seen = set()
    for info in infos:
        for area in info.findall(CAP + "area"):
            for polygon in area.findall(CAP + "polygon"):
                try:
                    coords = _polygon(polygon.text or "")
                    key = json.dumps(coords)
                    if key not in seen:
                        seen.add(key)
                        polygons.append(coords)
                except (ValueError, OverflowError):
                    warn(warnings, "CAP: nieprawidłowy polygon; pominięto geometrię tego obszaru")
            # Do not turn a radius or an area label into an exact map point.
            # Circles remain in raw evidence for a later explicit implementation.
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    if polygons:
        return {"type": "MultiPolygon", "coordinates": polygons}
    return None


def _select_language(infos, country: str):
    languages = list(dict.fromkeys(element_text(info, CAP + "language") or "unknown" for info in infos))
    preferred_prefixes = ["pl", "en"] if country == "PL" else ["en"]
    selected = None
    for preferred in preferred_prefixes:
        selected = next((lang for lang in languages if lang.casefold().split("-")[0] == preferred), None)
        if selected:
            break
    selected = selected or (languages[0] if languages else "unknown")
    return [
        info for info in infos
        if (element_text(info, CAP + "language") or "unknown") == selected
    ], selected, languages


def parse_cap(doc: FetchedDocument, country: str = "PL") -> ProviderBatch:
    root = xml_document(doc, "MeteoAlarm CAP", CAP + "alert")
    batch = ProviderBatch(events=[], metadata=metadata(doc, 1))
    try:
        record_id = identifier(element_text(root, CAP + "identifier"), "CAP identifier")
        sender = identifier(element_text(root, CAP + "sender"), "CAP sender")
        status = element_text(root, CAP + "status")
        scope = element_text(root, CAP + "scope")
        message_type = element_text(root, CAP + "msgType")
        if status != "Actual" or scope != "Public":
            raise ValueError("non-operational or non-public CAP")
        if message_type not in {"Alert", "Update", "Cancel"}:
            raise ValueError("not an operational CAP message type")
        infos = root.findall(CAP + "info")
        if not infos and message_type != "Cancel":
            raise ValueError("CAP Alert/Update without info")
        selected, language, languages = _select_language(infos, country)
        sent, sent_precision = timestamp(
            element_text(root, CAP + "sent"), warnings=batch.warnings, field="CAP sent",
        )
        references = _references(root, sender, batch.warnings)
        references = [value for value in references if value != record_id]
        if message_type in {"Update", "Cancel"} and not references:
            warn(batch.warnings, "CAP: Update/Cancel bez poprawnych references; poprzedniego komunikatu nie można powiązać")
        starts, ends, effective_times = [], [], []
        severities, original_severities, titles, descriptions = [], [], [], []
        precision = "unknown"
        missing_expiry = not selected
        for info in selected:
            onset, onset_precision = timestamp(
                element_text(info, CAP + "onset"), warnings=batch.warnings, field="CAP onset",
            )
            effective, effective_precision = timestamp(
                element_text(info, CAP + "effective"), warnings=batch.warnings, field="CAP effective",
            )
            expires, _ = timestamp(
                element_text(info, CAP + "expires"), warnings=batch.warnings, field="CAP expires",
            )
            if onset:
                starts.append(onset)
                precision = onset_precision
            if effective:
                effective_times.append(effective)
            if expires:
                ends.append(expires)
            else:
                missing_expiry = True
            original = element_text(info, CAP + "severity")
            original_severities.append(original)
            severities.append({"Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}.get(original, 0))
            title = plain(element_text(info, CAP + "headline") or element_text(info, CAP + "event"), 800)
            if title and title not in titles:
                titles.append(title)
            for field in ("description", "instruction"):
                description = plain(element_text(info, CAP + field))
                if description and description not in descriptions:
                    descriptions.append(description)
        occurred_start = min(starts) if starts else None
        valid_from = min(effective_times) if effective_times else sent
        valid_to = max(ends) if ends and not missing_expiry else None
        if valid_to and valid_from and valid_to < valid_from:
            warn(batch.warnings, "CAP: expires poprzedza effective; ważność nieznana")
            valid_to = None
        if valid_to and occurred_start and valid_to < occurred_start:
            warn(batch.warnings, "CAP: expires poprzedza onset; ważność nieznana")
            valid_to = None
        now = observed_now(doc)
        if message_type == "Cancel":
            lifecycle = "withdrawn"
        elif valid_to and valid_to <= now:
            lifecycle = "expired"
        elif valid_to and valid_from is not None and valid_from <= now:
            lifecycle = "active"
        else:
            lifecycle = "unknown"
        tags = ["cap", "cap_message:" + message_type.lower()]
        if occurred_start and occurred_start > now:
            tags.append("hazard_onset_in_future")
        if not valid_to and message_type != "Cancel":
            tags.append("expiry_not_known")
        if any(info.findall(".//" + CAP + "circle") for info in selected):
            tags.append("cap_circle_kept_in_raw")
        geometry = _geometry(selected, batch.warnings)
        title = " / ".join(titles)[:800]
        if not title:
            title = ("Odwołanie ostrzeżenia: " if message_type == "Cancel" else "Ostrzeżenie: ") + record_id
        batch.events.append(NormalizedEvent(
            source_id="meteoalarm", provider_record_id=record_id,
            kind="advisory", category="weather", title=required_title(title),
            description="\n".join(descriptions)[:12000], source_url=doc.url,
            occurred_start=occurred_start, issued_at=sent, source_updated_at=sent,
            valid_from=valid_from, valid_to=valid_to,
            countries=[country], geometry=geometry,
            location_precision="area" if selected else "country",
            time_precision=precision if occurred_start else sent_precision,
            severity=max(severities, default=0),
            original_severity=", ".join(sorted(set(filter(None, original_severities)))) or None,
            severity_reason="Oryginalna skala CAP: Minor→1, Moderate→2, Severe→3, Extreme→4; Unknown→0.",
            lifecycle_status=lifecycle, verification_status="official_warning",
            origins=[_origin(sender)],
            external_ids=["cap:" + sender + ":" + record_id],
            supersedes=references if message_type in {"Update", "Cancel"} else [],
            tags=tags,
            raw={
                "identifier": record_id, "sender": sender, "status": status,
                "message_type": message_type, "references": element_text(root, CAP + "references"),
                "languages": languages, "selected_language": language,
                "country_feed": country, "cap": xml_raw(root),
            },
        ))
        batch.metadata["provider_timestamp"] = sent.isoformat() if sent else None
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        reject(batch, "MeteoAlarm CAP", 0, exc)
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    slug = config.get("meteoalarm_country", "poland").strip().casefold()
    url = feed_url(slug)
    doc = await fetcher.get(url)
    root = xml_document(doc, "MeteoAlarm Atom", ATOM + "feed")
    if any(root.find(ATOM + field) is None for field in ("id", "title", "updated")):
        raise ProviderError("MeteoAlarm: niekompletny nagłówek Atom")
    entries = root.findall(ATOM + "entry")
    batch = ProviderBatch(events=[], metadata=metadata(doc, len(entries), country=COUNTRIES[slug]))
    feed_updated, _ = timestamp(
        root.findtext(ATOM + "updated"), warnings=batch.warnings, field="MeteoAlarm feed updated",
    )
    batch.metadata["provider_timestamp"] = feed_updated.isoformat() if feed_updated else None
    urls = []
    seen_urls: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            links = [
                link.attrib.get("href", "")
                for link in entry.findall(ATOM + "link")
                if link.attrib.get("type", "").split(";", 1)[0].strip() == "application/cap+xml"
            ]
            if not links:
                raise ValueError("missing CAP link")
            cap_url = _cap_url(links[0], doc.url)
            if cap_url not in seen_urls:
                seen_urls.add(cap_url)
                urls.append(cap_url)
        except (ValueError, TypeError) as exc:
            reject(batch, "MeteoAlarm Atom", index, exc)
    if len(urls) > MAX_CAP_REQUESTS:
        batch.metadata["truncated"] = True
        batch.metadata["partial"] = True
        batch.rejected_count += len(urls) - MAX_CAP_REQUESTS
        warn(batch.warnings, "MeteoAlarm: limit 200 dokumentów CAP; wynik częściowy")
    selected_urls = urls[:MAX_CAP_REQUESTS]
    semaphore = asyncio.Semaphore(2)
    blocked_retry: int | None = None
    requested = 0

    async def one(cap_url):
        nonlocal blocked_retry, requested
        async with semaphore:
            # At most one other request can already be in flight when a 429
            # arrives. Waiting tasks must not start more requests after it.
            if blocked_retry is not None:
                return "skipped", None
            requested += 1
            try:
                return "ok", parse_cap(await fetcher.get(cap_url), COUNTRIES[slug])
            except Exception as exc:
                # Report failure without response content or arbitrary transport details.
                delay = retry_after(exc)
                if delay is not None:
                    blocked_retry = max(blocked_retry or 0, delay)
                return "failed", None

    cap_batches = await asyncio.gather(*(one(cap_url) for cap_url in selected_urls))
    by_identifier: dict[str, NormalizedEvent] = {}
    failed = skipped = 0
    for index, (outcome, cap_batch) in enumerate(cap_batches):
        if cap_batch is None:
            batch.rejected_count += 1
            if outcome == "skipped":
                skipped += 1
            else:
                failed += 1
                warn(batch.warnings, f"MeteoAlarm: nie udało się odczytać CAP nr {index + 1}; wynik częściowy")
            continue
        batch.rejected_count += cap_batch.rejected_count
        for message in cap_batch.warnings:
            warn(batch.warnings, message)
        for event in cap_batch.events:
            previous = by_identifier.get(event.provider_record_id)
            if previous and previous.raw["sender"] != event.raw["sender"]:
                batch.rejected_count += 1
                warn(batch.warnings, "CAP: kolizja identifier pomiędzy nadawcami; nie połączono")
                continue
            if previous is None or (
                event.raw["selected_language"].startswith("pl"),
                len(event.raw["languages"]),
                event.source_updated_at or datetime.min.replace(tzinfo=UTC),
            ) > (
                previous.raw["selected_language"].startswith("pl"),
                len(previous.raw["languages"]),
                previous.source_updated_at or datetime.min.replace(tzinfo=UTC),
            ):
                by_identifier[event.provider_record_id] = event
    batch.events = sorted(
        by_identifier.values(),
        key=lambda event: (
            event.source_updated_at or datetime.min.replace(tzinfo=UTC),
            1 if event.lifecycle_status == "withdrawn" else 0, event.provider_record_id,
        ),
    )
    batch.metadata.update(
        cap_urls=len(urls), cap_planned=len(selected_urls),
        cap_requested=requested, cap_failed=failed,
        cap_skipped_after_rate_limit=skipped,
    )
    if blocked_retry is not None:
        batch.metadata["retry_after_seconds"] = blocked_retry
        warn(batch.warnings, f"MeteoAlarm: limit zapytań; ponowienie nie wcześniej niż za {blocked_retry} s")
    if skipped:
        warn(batch.warnings, f"MeteoAlarm: po limicie zapytań nie pobrano {skipped} dokumentów CAP; wynik częściowy")
    if failed or batch.rejected_count:
        batch.metadata["partial"] = True
    return batch
