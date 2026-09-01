"""GDACS RSS episodes are revisions of eventtype:eventid, not new disasters."""
from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import parse_qs, urlsplit

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    UTC, ProviderError, country_code, country_list, element_text, identifier,
    metadata, plain, point, reject, required_title, safe_url, timestamp, warn,
    xml_document, xml_raw,
)

URL = "https://www.gdacs.org/xml/rss.xml"
GD = "{http://www.gdacs.org}"
GEO = "{http://www.w3.org/2003/01/geo/wgs84_pos#}"
GEORSS = "{http://www.georss.org/georss}"


def _upstream(item, event_type: str):
    origins: set[str] = set()
    hard_ids: set[str] = set()
    # The wildfire methodology explicitly uses GWIS/FIRMS. GWIS and the
    # republisher must not each add an independent observation.
    if event_type == "WF":
        origins.add("nasa:firms")
    for node in item.iter():
        declared_source = node.attrib.get("source", "").strip().casefold()
        if node.tag == GD + "source":
            declared_source = (node.text or "").strip().casefold()
        if declared_source in {"usgs", "u.s. geological survey"}:
            origins.add("usgs")
        elif declared_source in {"emsc", "emsc/csem", "emsc-csem"}:
            origins.add("emsc")
        candidates = [node.attrib.get("url", ""), node.attrib.get("href", "")]
        if node.tag in {"link", GD + "url", GD + "sourceurl"}:
            candidates.append(node.text or "")
        for candidate in candidates:
            try:
                url = urlsplit(candidate.strip())
                if url.hostname != "earthquake.usgs.gov":
                    continue
                match = re.match(r"^/earthquakes/eventpage/([A-Za-z0-9._-]+)(?:/|$)", url.path)
                usgs_id = match[1] if match else None
                if not usgs_id and url.path.startswith("/fdsnws/event/"):
                    ids = parse_qs(url.query).get("eventid", [])
                    usgs_id = ids[0] if ids and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", ids[0]) else None
                if usgs_id:
                    origins.add("usgs")
                    hard_ids.add("usgs:" + usgs_id)
            except ValueError:
                continue
    return sorted(origins) or ["unknown:gdacs"], sorted(hard_ids)


def parse(doc: FetchedDocument) -> ProviderBatch:
    root = xml_document(doc, "GDACS", "rss")
    channel = root.find("channel")
    if channel is None:
        raise ProviderError("GDACS: brak channel w RSS")
    items = channel.findall("item")
    batch = ProviderBatch(events=[], metadata=metadata(doc, len(items)))
    published, _ = timestamp(channel.findtext("pubDate"), warnings=batch.warnings, field="GDACS feed pubDate")
    batch.metadata["provider_timestamp"] = published.isoformat() if published else None
    seen_revisions: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(items):
        try:
            kind_code = identifier(element_text(item, GD + "eventtype"), "eventtype").upper()
            if not re.fullmatch(r"[A-Z]{2,3}", kind_code):
                raise ValueError("invalid eventtype")
            event_id = identifier(element_text(item, GD + "eventid"), "eventid")
            if not event_id.isdecimal():
                raise ValueError("non-numeric GDACS eventid")
            episode_id = element_text(item, GD + "episodeid")
            version = element_text(item, GD + "version")
            record_id = kind_code + ":" + event_id
            start, precision = timestamp(element_text(item, GD + "fromdate"), warnings=batch.warnings, field="GDACS fromdate")
            end, _ = timestamp(element_text(item, GD + "todate"), warnings=batch.warnings, field="GDACS todate")
            issued, _ = timestamp(
                element_text(item, GD + "dateadded") or item.findtext("pubDate"),
                warnings=batch.warnings, field="GDACS dateadded",
            )
            updated, _ = timestamp(element_text(item, GD + "datemodified"), warnings=batch.warnings, field="GDACS datemodified")
            if start and end and end < start:
                warn(batch.warnings, "GDACS: todate poprzedza fromdate; koniec pozostawiono nieznany")
                end = None
            revision_key = (record_id, episode_id, version, updated.isoformat() if updated else "")
            if revision_key in seen_revisions:
                continue
            seen_revisions.add(revision_key)
            lat = element_text(item, ".//" + GEO + "lat")
            lon = element_text(item, ".//" + GEO + "long")
            if not lat or not lon:
                georss = element_text(item, GEORSS + "point").split()
                if len(georss) == 2:
                    lat, lon = georss
            geometry = point(lon, lat, batch.warnings, "GDACS geometry")
            countries = country_list(element_text(item, GD + "country"))
            for token in re.split(r"[,;\s]+", element_text(item, GD + "iso3")):
                code = country_code(token)
                if code:
                    countries.append(code)
            alert = element_text(item, GD + "alertlevel")
            severity = {"green": 1, "yellow": 2, "orange": 3, "red": 4}.get(alert.casefold(), 0)
            origins, foreign_ids = _upstream(item, kind_code)
            glide = element_text(item, GD + "glide")
            if re.fullmatch(r"[A-Z]{2}-\d{4}-\d{6}-[A-Z]{3}", glide):
                foreign_ids.append("glide:" + glide)
            current = element_text(item, GD + "iscurrent").casefold()
            tags = ["gdacs_impact_model", "gdacs_type:" + kind_code]
            if kind_code != "EQ" and geometry:
                tags.append("representative_point_not_extent")
            if kind_code == "WF":
                tags.extend(["derived_from_firms", "large_fire_filter", "not_all_fires"])
            if start and kind_code in {"WF", "DR", "FL"} and start.hour == start.minute == start.second == 0:
                precision = "day"
                tags.append("daily_event_bounds")
            batch.events.append(NormalizedEvent(
                source_id="gdacs", provider_record_id=record_id,
                kind="incident", category="earthquake" if kind_code == "EQ" else "disaster",
                title=required_title(item.findtext("title")), description=plain(item.findtext("description")),
                source_url=safe_url(item.findtext("link"), f"https://www.gdacs.org/report.aspx?eventtype={kind_code}&eventid={event_id}"),
                occurred_start=start, occurred_end=end, issued_at=issued,
                source_updated_at=updated, countries=countries, geometry=geometry,
                location_precision=("point" if kind_code == "EQ" else "area") if geometry else ("country" if countries else "unknown"),
                time_precision=precision, severity=severity, original_severity=alert or None,
                severity_reason="Poziom GDACS dotyczy potencjalnego wpływu humanitarnego; nie jest pewnością zdarzenia ani prognozą lokalnych szkód.",
                # iscurrent describes an episode, not proof that a disaster ended.
                lifecycle_status="active" if current == "true" else "unknown",
                verification_status="modelled_impact", origins=origins,
                external_ids=sorted(set(["gdacs:" + record_id] + foreign_ids)),
                tags=tags,
                raw={
                    "event_type": kind_code, "event_id": event_id, "episode_id": episode_id,
                    "version": version, "is_current": current == "true",
                    "item": xml_raw(item),
                },
            ))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(batch, "GDACS", index, exc)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    batch.events.sort(key=lambda e: (
        e.provider_record_id,
        bool(e.raw["is_current"]),
        e.source_updated_at or e.issued_at or e.occurred_start or epoch,
        int(e.raw["episode_id"]) if e.raw["episode_id"].isdigit() else 0,
    ))
    batch.metadata["distinct_events"] = len({event.provider_record_id for event in batch.events})
    batch.metadata["episode_revisions"] = len(batch.events)
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    return parse(await fetcher.get(URL))
