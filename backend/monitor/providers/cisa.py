"""CISA KEV entries are vulnerability notices, never geolocated attacks."""
from __future__ import annotations

import re

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import (
    ProviderError, identifier, json_document, metadata, plain, reject,
    required_title, timestamp, warn,
)

URL = "https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json"
CATALOG_URL = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"


def parse(doc: FetchedDocument) -> ProviderBatch:
    data = json_document(doc, "CISA KEV")
    records = data.get("vulnerabilities")
    if not isinstance(records, list):
        raise ProviderError("CISA KEV: brak tablicy vulnerabilities")
    batch = ProviderBatch(events=[], metadata=metadata(
        doc, len(records), catalog_version=data.get("catalogVersion"),
    ))
    released, release_precision = timestamp(data.get("dateReleased"), warnings=batch.warnings, field="CISA dateReleased")
    if release_precision != "second":
        released = None
    batch.metadata["provider_timestamp"] = released.isoformat() if released else None
    if released is None:
        warn(batch.warnings, "CISA KEV: dateReleased wymaga daty ze strefą i sekundami; nie można potwierdzić kolejności snapshotów.")
    if data.get("count") is not None and data["count"] != len(records):
        warn(batch.warnings, "CISA KEV: count nie zgadza się z liczbą vulnerabilities")
    seen: set[str] = set()
    for index, record in enumerate(records):
        try:
            if not isinstance(record, dict):
                raise ValueError("invalid vulnerability")
            cve = identifier(record.get("cveID"), "cveID").upper()
            if not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve):
                raise ValueError("invalid CVE")
            if cve in seen:
                raise ValueError("duplicate CVE in snapshot")
            seen.add(cve)
            issued, precision = timestamp(
                record.get("dateAdded"), warnings=batch.warnings,
                field="CISA dateAdded", allow_date=True,
            )
            title = required_title(record.get("vulnerabilityName") or cve)
            description = plain(record.get("shortDescription"))
            action = plain(record.get("requiredAction"))
            if action:
                description = (description + "\nZalecenie CISA: " + action).strip()[:12000]
            tags = ["catalog_date_added", "not_an_attack_report", "no_incident_location"]
            if precision == "day":
                tags.append("date_only_utc_anchor")
            if str(record.get("knownRansomwareCampaignUse", "")).casefold() == "known":
                tags.append("ransomware_use_reported_by_cisa")
            batch.events.append(NormalizedEvent(
                source_id="cisa_kev", provider_record_id=cve,
                kind="vulnerability_notice", category="cyber",
                title=title, description=description, source_url=CATALOG_URL,
                # dateAdded is the catalog entry's publication day, not an attack.
                occurred_start=None, issued_at=issued, source_updated_at=None,
                time_precision=precision, geometry=None, countries=[],
                location_precision="unknown", lifecycle_status="active",
                severity=0, original_severity=None,
                severity_reason=(
                    "KEV informuje o wykorzystywanej podatności, bez skali skutków konkretnego "
                    "incydentu. Nie przypisano CVSS ani geograficznego ryzyka."
                ),
                verification_status="catalogued_by_cisa", origins=["cisa"],
                external_ids=["cve:" + cve], tags=tags,
                # Catalog-level timestamps do not create a revision for every row.
                raw={**record, "date_only_fields": {"dateAdded": record.get("dateAdded")}},
            ))
        except (ValueError, TypeError, KeyError) as exc:
            reject(batch, "CISA KEV", index, exc)
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    return parse(await fetcher.get(URL))
