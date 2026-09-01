"""Public service-status facts; incident prose and arbitrary URLs are never retained.

Both official endpoints expose their latest 50 incidents. This is a bounded
history window, not evidence that an absent incident was withdrawn or resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    ProviderError, json_document, metadata, plain, reject, required_title,
    retry_after, timestamp, warn,
)


@dataclass(frozen=True)
class _Source:
    source_id: str
    name: str
    origin: str
    host: str

    @property
    def url(self) -> str:
        return f"https://{self.host}/api/v2/incidents.json"


_SOURCES = {
    "github_status": _Source("github_status", "GitHub Status", "github", "www.githubstatus.com"),
    "cloudflare_status": _Source("cloudflare_status", "Cloudflare Status", "cloudflare", "www.cloudflarestatus.com"),
}
_ID = re.compile(r"[a-z0-9]{1,64}")
_ACTIVE = frozenset({"investigating", "identified", "monitoring"})
_ENDED = frozenset({"resolved", "postmortem"})
_IMPACT = {"none": 1, "minor": 2, "major": 3, "critical": 4}


def _source(source_id: str) -> _Source:
    if source_id not in _SOURCES:
        raise ProviderError("Status API: nieznany operator")
    return _SOURCES[source_id]


def endpoint(source_id: str) -> str:
    return _source(source_id).url


def _identifier(value) -> str:
    # IDs also form the only variable part of the public incident link.
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid status incident or component id")
    return value


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _components(value, warnings: list[str], source: _Source) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        warn(warnings, f"{source.name}: nieprawidłowa lista komponentów; pominięto komponenty")
        return []
    result: dict[str, dict[str, str]] = {}
    for item in value:
        try:
            if not isinstance(item, dict):
                raise ValueError("invalid component")
            component_id = _identifier(item.get("id"))
            name = plain(item.get("name"), 200)
            if not name:
                raise ValueError("missing component name")
            # The component's status is CURRENT, not its state during this
            # historical incident. Retain only its identity and name.
            result[component_id] = {"id": component_id, "name": name}
        except (ValueError, TypeError):
            warn(warnings, f"{source.name}: nieprawidłowy komponent; pominięto jego metadane")
    return [result[key] for key in sorted(result)]


def parse(doc: FetchedDocument, *, source_id: str) -> ProviderBatch:
    source = _source(source_id)
    data = json_document(doc, source.name)
    records = data.get("incidents")
    if not isinstance(records, list):
        raise ProviderError(f"{source.name}: brak tablicy incidents")
    batch = ProviderBatch(events=[], metadata=metadata(
        doc, len(records), feed_url=source.url,
        coverage="latest_50_incidents", history_complete=False,
        documented_record_limit=50,
    ))
    seen: set[str] = set()
    for index, record in enumerate(records):
        try:
            if not isinstance(record, dict):
                raise ValueError("invalid incident")
            record_id = _identifier(record.get("id"))
            if record_id in seen:
                raise ValueError("duplicate incident id")
            title = required_title(record.get("name"))
            status = record.get("status")
            if not isinstance(status, str) or status not in _ACTIVE | _ENDED:
                status = None
                warn(batch.warnings, f"{source.name}: nieznany stan incydentu; stan życia pozostawiono nieznany")
            impact = record.get("impact")
            if not isinstance(impact, str) or impact not in _IMPACT:
                impact = None
                warn(batch.warnings, f"{source.name}: nieznany wpływ incydentu; priorytet pozostawiono nieznany")
            start, precision = timestamp(
                record.get("started_at"), warnings=batch.warnings, field=f"{source.name} started_at",
            )
            issued, _ = timestamp(
                record.get("created_at"), warnings=batch.warnings, field=f"{source.name} created_at",
            )
            updated, _ = timestamp(
                record.get("updated_at"), warnings=batch.warnings, field=f"{source.name} updated_at",
            )
            end, _ = timestamp(
                record.get("resolved_at"), warnings=batch.warnings, field=f"{source.name} resolved_at",
            )
            if start and end and end < start:
                warn(batch.warnings, f"{source.name}: resolved_at poprzedza started_at; koniec pozostawiono nieznany")
                end = None
            components = _components(record.get("components"), batch.warnings, source)
            lifecycle = "expired" if status in _ENDED else "active" if status in _ACTIVE else "unknown"
            description = (
                f"Zgłoszenie operatora {source.name}. "
                f"Stan incydentu: {status or 'nieznany'}. "
                f"Wpływ na usługi według operatora: {impact or 'nieznany'}."
            )
            if components:
                description += " Zgłoszone komponenty: " + ", ".join(item["name"] for item in components) + "."
            description += " Nie jest to pomiar dostępności całego Internetu."
            # Positive allowlist, never a copy of the incident or its updates.
            # Validated dates also prevent invalid payloads leaking into raw.
            raw = {
                "id": record_id, "name": title, "status": status, "impact": impact,
                "started_at": _iso(start), "created_at": _iso(issued),
                "updated_at": _iso(updated), "resolved_at": _iso(end),
                "components": components,
            }
            batch.events.append(NormalizedEvent(
                source_id=source.source_id, provider_record_id=record_id,
                kind="incident", category="internet", title=title,
                description=description[:12000],
                source_url=f"https://{source.host}/incidents/{record_id}",
                occurred_start=start, occurred_end=end, issued_at=issued,
                source_updated_at=updated, time_precision=precision,
                countries=[], geometry=None, location_precision="unknown",
                lifecycle_status=lifecycle, verification_status=f"reported_by_{source.origin}",
                severity=_IMPACT.get(impact, 0), original_severity=impact,
                severity_reason=(
                    "Priorytet według wpływu zadeklarowanego przez operatora: none — niski "
                    "(informacja bez zgłoszonego wpływu), minor — umiarkowany, major — wysoki, "
                    "critical — krytyczny. Dotyczy usług tego operatora, nie całego Internetu."
                    if impact is not None else
                    "Brak znanego wpływu incydentu według operatora; nie przypisano priorytetu."
                ),
                origins=[source.origin], external_ids=[f"{source.source_id}:{record_id}"],
                tags=[
                    "operator_reported_service_incident", "service_scope_not_global_internet",
                    "no_incident_location", "status_metadata_only",
                ],
                raw=raw,
            ))
            seen.add(record_id)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(batch, source.name, index, exc)
    return batch


async def collect(fetcher: Fetcher, *, source_id: str) -> ProviderBatch:
    source = _source(source_id)
    try:
        doc = await fetcher.get(source.url)
    except Exception as exc:
        raise ProviderError(
            f"{source.name}: nie udało się pobrać danych ({type(exc).__name__})",
            retry_after_seconds=retry_after(exc),
        ) from None
    return parse(doc, source_id=source_id)
