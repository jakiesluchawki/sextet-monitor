"""EASA CZIB export; advisory country reference points are not FIR geometry."""
from __future__ import annotations

from datetime import timedelta
import re
from urllib.parse import quote

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    ProviderError, country_list, identifier, json_document, metadata, observed_now,
    plain, reject, required_title, timestamp,
)

URL = "https://www.easa.europa.eu/en/domains/air-operations/czibs/export-json?_format=json&page="
LIST_URL = "https://www.easa.europa.eu/en/domains/air-operations/czibs"


def parse(doc: FetchedDocument) -> ProviderBatch:
    data = json_document(doc, "EASA CZIB")
    records = data.get("conflict_zones")
    if not isinstance(records, list):
        raise ProviderError("EASA CZIB: brak tablicy conflict_zones")
    batch = ProviderBatch(events=[], metadata=metadata(doc, len(records)))
    now = observed_now(doc)
    updates = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        try:
            if not isinstance(record, dict):
                raise ValueError("invalid advisory")
            nid = identifier(record.get("Nid"), "Nid")
            if not nid.isdecimal() or nid in seen:
                raise ValueError("invalid or duplicate Nid")
            seen.add(nid)
            title = required_title(record.get("name"))
            issued_value = record.get("issued_date")
            day_match = re.match(r"^\d{4}-\d{2}-\d{2}(?=T|$)", issued_value) if isinstance(issued_value, str) else None
            issued, issued_precision = timestamp(
                issued_value, warnings=batch.warnings,
                field="EASA issued_date", allow_date=True,
            )
            if issued and day_match:
                issued, issued_precision = timestamp(day_match[0], allow_date=True)
            updated, _ = timestamp(
                record.get("updated"), warnings=batch.warnings, field="EASA updated",
            )
            if updated:
                updates.append(updated)
            end_day, _ = timestamp(
                record.get("valid_until_date"), warnings=batch.warnings,
                field="EASA valid_until_date", allow_date=True, day_first=True,
            )
            # An explicit calendar convention: valid through the named day.
            valid_to = end_day + timedelta(days=1) if end_day else None
            status = plain(record.get("status"), 80).casefold()
            if status in {"withdrawn", "cancelled", "canceled"}:
                lifecycle = "withdrawn"
            elif status in {"expired", "inactive"}:
                lifecycle = "expired"
            elif valid_to and valid_to <= now:
                lifecycle = "expired"
            elif status == "active":
                lifecycle = "active"
            else:
                lifecycle = "unknown"
            countries = country_list(record.get("country"))
            tags = ["czib_not_notam", "country_not_fir_boundary"]
            raw = dict(record)
            raw["date_only_fields"] = {}
            if end_day:
                tags.extend(["date_only_utc_anchor", "valid_to_exclusive_day_boundary"])
                raw["date_only_fields"]["valid_until_date"] = record["valid_until_date"]
            # The issue date is a calendar day serialized as local midnight.
            # Use the source calendar day, not the previous UTC date.
            if issued:
                raw["date_only_fields"]["issued_date"] = record.get("issued_date")
                if "date_only_utc_anchor" not in tags:
                    tags.append("date_only_utc_anchor")
            if record.get("coordinates"):
                tags.append("provider_reference_point_not_used_as_fir")
            validity = plain(record.get("field_easa_valid_until_descr"))
            description = (
                "Biuletyn EASA dotyczący ryzyka operacji lotniczych. "
                "Nie stanowi kompletnej bazy NOTAM ani granic zamkniętej przestrzeni."
            )
            if validity:
                description += "\nWażność według EASA: " + validity
            batch.events.append(NormalizedEvent(
                source_id="easa_czib", provider_record_id=nid, kind="advisory",
                category="aviation", title=title, description=description,
                source_url="https://www.easa.europa.eu/en/node/" + quote(nid),
                occurred_start=None, issued_at=issued, source_updated_at=updated,
                valid_from=issued, valid_to=valid_to, countries=countries,
                geometry=None, location_precision="country" if countries else "unknown",
                time_precision="day" if issued or end_day else "unknown",
                severity=0, original_severity=None,
                severity_reason="Eksport CZIB nie podaje wspólnej skali ciężkości; status Active nie jest poziomem zagrożenia.",
                lifecycle_status=lifecycle, verification_status="official_advisory",
                origins=["easa"], external_ids=["easa:czib:" + nid],
                tags=tags, raw=raw,
            ))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(batch, "EASA CZIB", index, exc)
    batch.metadata["provider_timestamp"] = max(updates).isoformat() if updates else None
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    return parse(await fetcher.get(URL))
