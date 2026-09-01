"""USGS GeoJSON summary feed; no per-event network calls."""
from __future__ import annotations

import re
from urllib.parse import quote

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    ProviderError, identifier, json_document, metadata, milliseconds, plain,
    point, reject, required_title, safe_url, warn,
)

URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
WEEK_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson"
_HARD_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def parse(doc: FetchedDocument) -> ProviderBatch:
    data = json_document(doc, "USGS")
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ProviderError("USGS: oczekiwano GeoJSON FeatureCollection z features")
    features = data["features"]
    batch = ProviderBatch(events=[], metadata=metadata(doc, len(features)))
    feed_meta = data.get("metadata")
    if isinstance(feed_meta, dict):
        generated = milliseconds(feed_meta.get("generated"), batch.warnings, "USGS generated")
        batch.metadata["provider_timestamp"] = generated.isoformat() if generated else None
        if feed_meta.get("count") is not None and feed_meta["count"] != len(features):
            warn(batch.warnings, "USGS: count nie zgadza się z liczbą features")
    filtered = 0
    for index, feature in enumerate(features):
        try:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError("invalid feature")
            props = feature.get("properties")
            if not isinstance(props, dict):
                raise ValueError("missing properties")
            reported_type = props.get("type")
            if reported_type is not None and (not isinstance(reported_type, str) or not reported_type.strip()):
                raise ValueError("invalid event type")
            reclassified = reported_type is not None and reported_type.strip().casefold() != "earthquake"
            record_id = identifier(feature.get("id"))
            if not _HARD_ID.fullmatch(record_id):
                raise ValueError("invalid USGS id")
            title = required_title(
                props.get("title") or (
                    "Trzęsienie ziemi — " + plain(props["place"]) if props.get("place")
                    else "Trzęsienie ziemi (" + record_id + ")"
                )
            )
            geometry = feature.get("geometry")
            normalized_geometry = None
            if isinstance(geometry, dict) and geometry.get("type") == "Point":
                coords = geometry.get("coordinates")
                if isinstance(coords, list) and len(coords) >= 2:
                    normalized_geometry = point(coords[0], coords[1], batch.warnings, "USGS geometry")
                elif coords is not None:
                    warn(batch.warnings, "USGS: nieprawidłowe coordinates")
            elif geometry is not None:
                warn(batch.warnings, "USGS: nieobsługiwany typ geometrii")
            occurred = milliseconds(props.get("time"), batch.warnings, "USGS time")
            updated = milliseconds(props.get("updated"), batch.warnings, "USGS updated")
            mag = props.get("mag")
            severity = 0
            reason = "Brak magnitudy; nie przypisano priorytetu."
            original = None
            if isinstance(mag, (int, float)) and not isinstance(mag, bool):
                severity = 4 if mag >= 7 else 3 if mag >= 5.5 else 2 if mag >= 4 else 1
                original = f"M {mag:g} {plain(props.get('magType'), 30)}".strip()
                reason = (
                    f"Priorytet według magnitudy {mag:g}: <4 niski, 4–5,4 umiarkowany, "
                    "5,5–6,9 wysoki, ≥7 krytyczny; nie jest to prognoza szkód."
                )
            elif mag is not None:
                warn(batch.warnings, "USGS: nieprawidłowa magnituda; priorytet nieznany")
            hard_ids = {record_id}
            if isinstance(props.get("ids"), str):
                hard_ids.update(value.strip() for value in props["ids"].split(",") if _HARD_ID.fullmatch(value.strip()))
            networks = []
            if isinstance(props.get("net"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,24}", props["net"]):
                networks = [props["net"].lower()]
            elif isinstance(props.get("sources"), str):
                networks = [
                    value.strip().lower() for value in props["sources"].split(",")
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", value.strip())
                ]
            if reclassified:
                title = "Wycofany raport USGS — " + plain(reported_type, 120) + " (" + record_id + ")"
                severity = 0
                original = plain(reported_type, 120)
                reason = "USGS nie klasyfikuje już tego rekordu jako trzęsienia ziemi; wycofano wcześniejszy raport sejsmiczny."
            normalized = NormalizedEvent(
                source_id="usgs", provider_record_id=record_id, kind="incident",
                category="earthquake", title=title, description=plain(props.get("place")),
                source_url=safe_url(props.get("url"), "https://earthquake.usgs.gov/earthquakes/eventpage/" + quote(record_id)),
                occurred_start=occurred, occurred_end=occurred, source_updated_at=updated,
                geometry=normalized_geometry,
                location_precision="point" if normalized_geometry else "unknown",
                time_precision="second" if occurred else "unknown",
                severity=severity, original_severity=original, severity_reason=reason,
                verification_status="reclassified_by_usgs" if reclassified else plain(props.get("status"), 60) or "reported",
                lifecycle_status="withdrawn" if reclassified else "active",
                origins=sorted({"usgs:" + net for net in networks}) or ["usgs"],
                external_ids=sorted("usgs:" + value for value in hard_ids),
                tags=(["usgs_reclassification", "not_an_earthquake", "instantaneous_event"] if reclassified else
                      ["seismic_observation", "magnitude_not_damage", "instantaneous_event"]),
                raw=feature,
            )
            if reclassified:
                filtered += 1
                # This is a conditional withdrawal, not a new non-earthquake incident.
                # Ingestion applies it only when this USGS record is already known.
                batch.metadata.setdefault("reclassifications", []).append(normalized.model_dump(mode="json"))
            else:
                batch.events.append(normalized)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(batch, "USGS", index, exc)
    batch.metadata["excluded_non_earthquake"] = filtered
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    # The worker may explicitly request the weekly repair feed. No arbitrary URL.
    url = WEEK_URL if config.get("usgs_window") == "week" else URL
    return parse(await fetcher.get(url))
