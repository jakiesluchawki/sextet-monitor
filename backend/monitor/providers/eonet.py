"""Bounded EONET v3 references, not official hazard extents or new NASA confirmations."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, quote, urlsplit

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    ProviderError, identifier, json_document, metadata, observed_now, plain,
    point, reject, required_title, timestamp, warn,
)

BASE_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
URL = BASE_URL + "?category=wildfires,volcanoes,severeStorms&status=all&days=30&limit=400"
MAX_RECORDS = 400
MAX_GEOMETRIES = 1024
CATEGORIES = {"wildfires": "WF", "volcanoes": "VO", "severeStorms": "TC"}
_ORIGINS = {
    "USGS_EHP": "usgs", "USGS_CMT": "usgs", "HDDS": "usgs", "AVO": "usgs",
    "FIRMS": "nasa:firms", "GWIS": "nasa:firms",
    "NOAA_NHC": "noaa:nhc", "NOAA_CPC": "noaa:cpc", "JTWC": "jtwc",
    "IRWIN": "us:interagency_fire", "InciWeb": "us:interagency_fire",
    "SIVolcano": "smithsonian:gvp",
}


def _source_time(value, batch, now, field):
    result, precision = timestamp(value, warnings=batch.warnings, field=field, allow_date=True)
    if result and result > now + timedelta(minutes=5):
        warn(batch.warnings, field + ": czas w przyszłości; nie użyto go jako obserwacji")
        return None, "unknown"
    # EONET documents midnight as its usual placeholder when the source has no clock.
    if result and result.hour == result.minute == result.second == result.microsecond == 0:
        precision = "day"
    return result, precision


def _geometry(sample, batch):
    coordinates = sample.get("coordinates")
    if sample.get("type") == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
        return point(coordinates[0], coordinates[1], batch.warnings, "EONET point")
    if sample.get("type") == "Polygon" and isinstance(coordinates, list) and 0 < len(coordinates) <= 64:
        rings = []
        for ring in coordinates:
            if not isinstance(ring, list) or not 4 <= len(ring) <= 4096:
                break
            points = []
            for pair in ring:
                if not isinstance(pair, list) or len(pair) < 2:
                    break
                parsed = point(pair[0], pair[1], batch.warnings, "EONET polygon")
                if parsed is None:
                    break
                points.append(parsed["coordinates"])
            if len(points) != len(ring) or points[0] != points[-1]:
                break
            rings.append(points)
        if len(rings) == len(coordinates):
            return {"type": "Polygon", "coordinates": rings}
    warn(batch.warnings, "EONET: niepoprawna lub nieobsługiwana geometria; nie zastąpiono jej centroidem")
    return None


def _upstreams(record, categories, batch):
    origins, external = set(), set()
    sources = record.get("sources")
    if not isinstance(sources, list):
        warn(batch.warnings, "EONET: brak poprawnej listy źródeł pierwotnych")
        return ["unknown:eonet"], []
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            warn(batch.warnings, "EONET: niepoprawne wskazanie źródła pierwotnego")
            continue
        source_id = source["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id):
            continue
        origin = _ORIGINS.get(source_id, "unknown:eonet:" + source_id.lower())
        if source_id == "GDACS":
            # GDACS wildfire products explicitly derive from GWIS/FIRMS, not a second sensor.
            origin = "nasa:firms" if "wildfires" in categories else "unknown:gdacs"
        origins.add(origin)
        try:
            url = urlsplit(source.get("url") or "")
            if url.username or url.password or url.scheme not in {"http", "https"}:
                continue
            query = parse_qs(url.query)
            if source_id == "GDACS" and url.hostname in {"gdacs.org", "www.gdacs.org"}:
                types, ids = query.get("eventtype", []), query.get("eventid", [])
                expected = {CATEGORIES[category] for category in categories}
                if (url.path.lower() == "/report.aspx" and len(types) == len(ids) == 1
                        and types[0] in expected and re.fullmatch(r"\d{1,20}", ids[0])):
                    external.add("gdacs:" + types[0] + ":" + ids[0])
            if source_id == "USGS_EHP" and url.hostname == "earthquake.usgs.gov":
                match = re.fullmatch(r"/earthquakes/eventpage/([A-Za-z0-9._-]{1,128})/?", url.path)
                if match:
                    external.add("usgs:" + match[1])
        except (TypeError, ValueError):
            warn(batch.warnings, "EONET: niepoprawny odnośnik źródła; nie użyto go do łączenia")
    return sorted(origins) or ["unknown:eonet"], sorted(external)


def parse(doc: FetchedDocument) -> ProviderBatch:
    data = json_document(doc, "NASA EONET")
    records = data.get("events")
    if not isinstance(records, list):
        raise ProviderError("NASA EONET: brak tablicy events")
    batch = ProviderBatch(events=[], metadata=metadata(
        doc, len(records), window_days=30, record_limit=MAX_RECORDS, excluded_categories=0,
        excluded_outside_window=0, duplicate_records=0, provider_timestamp=None,
    ))
    now = observed_now(doc)
    links = data.get("links")
    has_next = bool(data.get("next") or (isinstance(links, dict) and links.get("next")))
    if len(records) >= MAX_RECORDS or has_next:
        batch.metadata["partial"] = True
        batch.metadata["at_record_limit"] = len(records) >= MAX_RECORDS
        warn(batch.warnings, "EONET: limit 400 rekordów lub dalsza strona; odczyt nie jest pełnym katalogiem")
    candidates, raw_seen, conflicts = {}, {}, set()
    for index, record in enumerate(records[:MAX_RECORDS]):
        try:
            if not isinstance(record, dict):
                raise ValueError("invalid EONET event")
            record_id = identifier(record.get("id"))
            if not re.fullmatch(r"EONET_[A-Za-z0-9_-]{1,80}", record_id):
                raise ValueError("invalid EONET id")
            encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)
            if record_id in raw_seen:
                if record_id not in conflicts and encoded == raw_seen[record_id]:
                    batch.metadata["duplicate_records"] += 1
                    continue
                if record_id in candidates:
                    del candidates[record_id]
                    batch.rejected_count += 1
                conflicts.add(record_id)
                raise ValueError("conflicting duplicate EONET event")
            raw_seen[record_id] = encoded
            values = record.get("categories")
            if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
                raise ValueError("invalid EONET categories")
            categories = sorted({value.get("id") for value in values if value.get("id") in CATEGORIES})
            if not categories:
                batch.metadata["excluded_categories"] += 1
                continue
            title = required_title(record.get("title"))
            samples = record.get("geometry")
            if not isinstance(samples, list) or len(samples) > MAX_GEOMETRIES:
                raise ValueError("invalid or oversized EONET geometry history")
            dated, undated = [], []
            for sample in samples:
                if not isinstance(sample, dict):
                    warn(batch.warnings, "EONET: pominięto niepoprawną próbkę geometrii")
                    continue
                instant, precision = _source_time(sample.get("date"), batch, now, "EONET geometry.date")
                geometry = _geometry(sample, batch)
                if instant is not None:
                    dated.append((instant, precision, geometry))
                elif sample.get("date") in (None, ""):
                    undated.append(geometry)
            dated.sort(key=lambda item: item[0])
            closed, _ = _source_time(record.get("closed"), batch, now, "EONET closed")
            latest = dated[-1][0] if dated else None
            clocks = [value for value in (latest, closed) if value is not None]
            if clocks and max(clocks) < now - timedelta(days=30):
                batch.metadata["excluded_outside_window"] += 1
                continue
            occurred, precision = (dated[0][0], dated[0][1]) if dated else (None, "unknown")
            geometry = dated[-1][2] if dated else next((item for item in reversed(undated) if item), None)
            origins, external = _upstreams(record, categories, batch)
            tags = [
                "eonet_curated_reference", "approximate_source_extent", "source_geometry_time_not_onset",
                *("eonet_category:" + category for category in categories),
            ]
            if precision == "day":
                tags.append("date_only_utc_anchor")
            if geometry and geometry["type"] == "Point":
                tags.append("representative_point_not_extent")
            if len(dated) > 1:
                tags.append("latest_geometry_not_full_track")
            if closed:
                tags.append("curator_closed_not_verified_physical_end")
            status = "expired" if closed else "active" if record.get("closed") is None else "unknown"
            candidates[record_id] = NormalizedEvent(
                source_id="nasa_eonet", provider_record_id=record_id, kind="incident", category="disaster",
                title=title, description=plain(record.get("description")),
                source_url=BASE_URL + "/" + quote(record_id, safe=""),
                occurred_start=occurred, occurred_end=None, issued_at=None, source_updated_at=None,
                geometry=geometry, location_precision=("point" if geometry["type"] == "Point" else "area") if geometry else "unknown",
                time_precision=precision, severity=0,
                severity_reason="EONET jest kuratorskim indeksem; nie przeliczono powierzchni ani prędkości wiatru na lokalną wagę zagrożenia.",
                lifecycle_status=status, verification_status="curated_reference", origins=origins,
                external_ids=sorted({"eonet:" + record_id, *external}), tags=tags, raw=record,
            )
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(batch, "NASA EONET", index, exc)
    batch.events = list(candidates.values())
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    # v3 has no documented offset/page parameter. Never follow arbitrary provider links.
    return parse(await fetcher.get(URL))
