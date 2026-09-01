"""Cloudflare Radar annotations; requests are impossible without a token."""
from __future__ import annotations

from urllib.parse import urlencode

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    MissingCredentials, ProviderError, country_list, identifier, json_document,
    metadata, observed_now, plain, reject, retry_after, timestamp, warn,
)

BASE_URL = "https://api.cloudflare.com/client/v4/radar/annotations/outages"
PUBLIC_URL = "https://radar.cloudflare.com/outage-center"
PAGE_SIZE = 100
MAX_PAGES = 5


def page_url(offset: int) -> str:
    return BASE_URL + "?" + urlencode({
        "limit": PAGE_SIZE, "offset": offset, "dateRange": "7d", "format": "json",
    })


def parse(doc: FetchedDocument) -> ProviderBatch:
    data = json_document(doc, "Cloudflare Radar")
    if data.get("success") is not True:
        raise ProviderError("Cloudflare Radar: API nie potwierdziło success=true")
    result = data.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("annotations"), list):
        raise ProviderError("Cloudflare Radar: brak result.annotations")
    records = result["annotations"]
    batch = ProviderBatch(events=[], metadata=metadata(doc, len(records)))
    now = observed_now(doc)
    for index, record in enumerate(records):
        try:
            if not isinstance(record, dict):
                raise ValueError("invalid annotation")
            record_id = identifier(record.get("id"))
            event_type = plain(record.get("eventType"), 60).upper()
            if event_type not in {"OUTAGE", "ANOMALY"}:
                raise ValueError("unknown eventType")
            start, precision = timestamp(
                record.get("startDate"), warnings=batch.warnings, field="Radar startDate",
            )
            end, _ = timestamp(record.get("endDate"), warnings=batch.warnings, field="Radar endDate")
            if start and end and end < start:
                warn(batch.warnings, "Radar: endDate poprzedza startDate; koniec pozostawiono nieznany")
                end = None
            countries = country_list(record.get("locations"))
            outage = record.get("outage") if isinstance(record.get("outage"), dict) else {}
            cause = plain(outage.get("outageCause"), 180)
            scope = plain(record.get("scope"), 300)
            title = ("Zakłócenie Internetu" if event_type == "OUTAGE" else "Anomalia ruchu internetowego")
            if scope or countries:
                title += " — " + (scope or ", ".join(countries))
            description = plain(record.get("description"))
            if cause and cause.upper() not in {"UNKNOWN", "OTHER", "UNSPECIFIED"}:
                description = (description + "\nPrzyczyna przypisana przez Cloudflare: " + cause).strip()[:12000]
            tags = ["cloudflare_visibility_only", "reported_type:" + event_type.lower()]
            if event_type == "ANOMALY":
                tags.append("anomaly_is_not_an_outage")
            if cause:
                tags.append("cause_is_provider_attribution")
            if end and end <= now:
                lifecycle = "expired"
            elif start and start <= now:
                lifecycle = "active"
            else:
                lifecycle = "unknown"
            batch.events.append(NormalizedEvent(
                source_id="cloudflare_radar", provider_record_id=record_id,
                kind="incident" if event_type == "OUTAGE" else "measurement", category="internet",
                title=title[:800], description=description, source_url=PUBLIC_URL,
                occurred_start=start, occurred_end=end, time_precision=precision,
                countries=countries, geometry=None,
                location_precision="country" if countries else "unknown",
                severity=0, original_severity=None,
                severity_reason="Brak porównywalnej skali skutków w adnotacji; nie wyliczono pozornego anomaly score.",
                lifecycle_status=lifecycle, verification_status="reported_by_cloudflare",
                # API 'origins' are monitored origins (e.g. AWS), not independent
                # evidence providers. Keep them only in raw.
                origins=["cloudflare"], external_ids=["cloudflare:outage:" + record_id],
                tags=tags, raw=record,
            ))
        except (ValueError, TypeError, KeyError) as exc:
            reject(batch, "Cloudflare Radar", index, exc)
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    token = config.get("radar_token", "").strip()
    if not token:
        raise MissingCredentials(
            "Cloudflare Radar jest wyłączony: brak tokenu Radar Read. Nie wykonano połączenia."
        )
    if "\r" in token or "\n" in token:
        raise MissingCredentials("Cloudflare Radar: nieprawidłowy format tokenu")
    headers = {"Authorization": "Bearer " + token}
    combined = ProviderBatch(events=[], metadata={"pages": 0, "records_seen": 0})
    seen: set[str] = set()
    for page_index in range(MAX_PAGES):
        try:
            batch = parse(await fetcher.get(page_url(page_index * PAGE_SIZE), headers=headers))
        except Exception as exc:
            if page_index == 0:
                # Never reflect auth headers or the token through arbitrary
                # transport exceptions in source-health logs.
                if isinstance(exc, ProviderError):
                    raise
                raise ProviderError(
                    "Cloudflare Radar: nie udało się pobrać danych",
                    retry_after_seconds=retry_after(exc),
                ) from None
            warn(combined.warnings, f"Radar: nie udało się pobrać strony {page_index + 1}; wynik częściowy")
            combined.metadata["partial"] = True
            delay = retry_after(exc)
            if delay is not None:
                combined.metadata["retry_after_seconds"] = delay
            break
        combined.metadata["pages"] += 1
        combined.metadata["records_seen"] += batch.metadata["records_seen"]
        combined.rejected_count += batch.rejected_count
        for message in batch.warnings:
            warn(combined.warnings, message)
        new_ids = 0
        for event in batch.events:
            if event.provider_record_id not in seen:
                seen.add(event.provider_record_id)
                combined.events.append(event)
                new_ids += 1
        if batch.metadata["records_seen"] < PAGE_SIZE:
            break
        if new_ids == 0:
            warn(combined.warnings, "Radar: powtarzająca się strona lub brak poprawnych rekordów; wynik częściowy")
            combined.metadata["partial"] = True
            break
    else:
        warn(combined.warnings, "Radar: osiągnięto limit 500 adnotacji; wynik może być niepełny")
        combined.metadata["partial"] = True
        combined.metadata["truncated"] = True
    return combined
